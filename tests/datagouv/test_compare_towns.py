import json

import pytest
import requests

from datagouv import compare_towns, net, towns
from datagouv.sources import rents, risks, water
from datagouv.towns import Town, TownResolutionError

LYON_ARRONDISSEMENTS = [
    {"code": f"6938{i}", "nom": f"Lyon {i}{'er' if i == 1 else 'e'} Arrondissement", "_score": 0.05}
    for i in range(1, 10)
]

RENT_ROW = {
    "INSEE_C": "69383",
    "LIBGEO": "Lyon 3e Arrondissement",
    "loypredm2": 18.8745,
    "lwr.IPm2": 14.58,
    "upr.IPm2": 24.43,
    "TYPPRED": "commune",
    "nbobs_com": 19582,
    "nbobs_mail": 27963,
    "R2_adj": 0.685,
}

GASPAR = {
    "results": 1,
    "data": [
        {
            "risques_detail": [
                {"num_risque": "11", "libelle_risque_long": "Inondation"},
                {"num_risque": "112", "libelle_risque_long": "Par une crue à débordement lent de cours d'eau"},
                {"num_risque": "116", "libelle_risque_long": "Par remontées de nappes naturelles"},
                {"num_risque": "13", "libelle_risque_long": "Séisme"},
                {"num_risque": "215", "libelle_risque_long": "Orphan sub-type"},
            ]
        }
    ],
}


def _row(sample, date, bact="C", pc="C"):
    return {
        "code_prelevement": sample,
        "date_prelevement": date,
        "conclusion_conformite_prelevement": f"conclusion {sample}",
        "conformite_limites_bact_prelevement": bact,
        "conformite_limites_pc_prelevement": pc,
    }


HUBEAU = {
    "count": 5,
    "data": [
        _row("S2", "2026-07-20T09:51:00Z"),
        _row("S2", "2026-07-20T09:51:00Z"),
        _row("S1", "2026-07-10T08:00:00Z", bact="N"),
        _row("S1", "2026-07-10T08:00:00Z", bact="N"),
        _row("S0", "2026-07-01T08:00:00Z", bact="S"),
    ],
}


class FakeNet:
    """Routes URLs to canned bodies and records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        for prefix, handler in self.routes:
            if url.startswith(prefix):
                result = handler(url, params or {})
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"unexpected URL {url}")


@pytest.fixture
def fake_net(monkeypatch):
    def install(routes):
        fake = FakeNet(routes)
        monkeypatch.setattr(net, "get_json", fake)
        return fake

    return install


def _http_404():
    response = requests.Response()
    response.status_code = 404
    return requests.HTTPError("404", response=response)


# --- resolution -------------------------------------------------------------


def test_resolve_picks_exact_arrondissement_among_equal_scores(fake_net):
    fake_net([(towns.GEO_API, lambda u, p: LYON_ARRONDISSEMENTS)])
    assert towns.resolve("Lyon 3e") == Town("69383", "Lyon 3e Arrondissement")
    assert towns.resolve("lyon 1er arrondissement") == Town("69381", "Lyon 1er Arrondissement")


def test_resolve_ignores_accents_case_and_hyphens(fake_net):
    fake_net([(towns.GEO_API, lambda u, p: [{"code": "63113", "nom": "Clermont-Ferrand"}])])
    assert towns.resolve("clermont ferrand").code == "63113"


def test_resolve_without_exact_match_lists_candidates(fake_net):
    fake_net([(towns.GEO_API, lambda u, p: LYON_ARRONDISSEMENTS)])
    with pytest.raises(TownResolutionError) as raised:
        towns.resolve("Lyon")
    assert "69383" in str(raised.value) and "69389" in str(raised.value)


def test_resolve_homonyms_is_ambiguous(fake_net):
    homonyms = [{"code": "01001", "nom": "Sainte-Foy"}, {"code": "40001", "nom": "Sainte-Foy"}]
    fake_net([(towns.GEO_API, lambda u, p: homonyms)])
    with pytest.raises(TownResolutionError, match="several"):
        towns.resolve("Sainte-Foy")


def test_resolve_nothing_found(fake_net):
    fake_net([(towns.GEO_API, lambda u, p: [])])
    with pytest.raises(TownResolutionError, match="no town"):
        towns.resolve("Nowhere")


def test_resolve_code_hits_the_commune_endpoint(fake_net):
    fake = fake_net([(towns.GEO_API, lambda u, p: {"nom": "Clermont-Ferrand", "code": "63113"})])
    assert towns.resolve("63113") == Town("63113", "Clermont-Ferrand")
    assert fake.calls[0][0] == f"{towns.GEO_API}/63113"


def test_resolve_code_falls_back_to_arrondissement_type(fake_net):
    def answer(url, params):
        if params.get("type") == "arrondissement-municipal":
            return {"nom": "Lyon 3e Arrondissement", "code": "69383"}
        return _http_404()

    fake_net([(towns.GEO_API, answer)])
    assert towns.resolve("69383").name == "Lyon 3e Arrondissement"


def test_resolve_unknown_code(fake_net):
    fake_net([(towns.GEO_API, lambda u, p: _http_404())])
    with pytest.raises(TownResolutionError, match="99999"):
        towns.resolve("99999")


@pytest.mark.parametrize(
    "code, parent",
    [
        ("75101", ("75056", "Paris")),
        ("75120", ("75056", "Paris")),
        ("69381", ("69123", "Lyon")),
        ("69389", ("69123", "Lyon")),
        ("13201", ("13055", "Marseille")),
        ("13216", ("13055", "Marseille")),
        ("69123", None),
        ("63113", None),
        ("2A004", None),
    ],
)
def test_parent_commune(code, parent):
    assert towns.parent_commune(code) == parent


# --- sources ----------------------------------------------------------------


def test_rents_parses_every_segment(fake_net):
    def answer(url, params):
        assert params == {"INSEE_C__exact": "69383", "page_size": 1}
        if rents.RESOURCES["house"] in url:
            return {"data": [], "meta": {"total": 0}}
        return {"data": [RENT_ROW], "meta": {"total": 1}}

    fake_net([("https://tabular-api.data.gouv.fr", answer)])
    result = rents.fetch("69383")
    assert result["vintage"] == 2025
    assert result["segments"]["apartment_1_2_rooms"] == {
        "rent_m2": 18.87,
        "low_m2": 14.58,
        "high_m2": 24.43,
        "estimated_on": "commune",
        "observations": 19582,
    }
    assert result["segments"]["house"] is None


def test_risks_nests_subtypes_under_their_prefix(fake_net):
    fake_net([(risks.GASPAR_API, lambda u, p: GASPAR)])
    assert risks.fetch("69123")["risks"] == [
        {
            "risk": "Inondation",
            "subtypes": [
                "Par une crue à débordement lent de cours d'eau",
                "Par remontées de nappes naturelles",
            ],
        },
        {"risk": "Séisme", "subtypes": []},
        {"risk": "Orphan sub-type", "subtypes": []},
    ]


def test_risks_empty_for_arrondissement_payload(fake_net):
    fake_net([(risks.GASPAR_API, lambda u, p: {"results": 0, "data": []})])
    assert risks.fetch("69383")["risks"] == []


def test_water_collapses_parameter_rows_into_samples(fake_net):
    fake_net([(water.HUBEAU_API, lambda u, p: HUBEAU)])
    result = water.fetch("63113")
    assert result["samples"] == 3
    assert result["last_sample_date"] == "2026-07-20"
    assert result["last_conclusion"] == "conclusion S2"
    assert result["bacteriological"] == {"compliant": 1, "non_compliant": 1, "not_applicable": 1}
    assert result["physico_chemical"] == {"compliant": 3}


def test_water_without_samples(fake_net):
    fake_net([(water.HUBEAU_API, lambda u, p: {"count": 0, "data": []})])
    result = water.fetch("69383")
    assert result["samples"] == 0 and result["last_sample_date"] is None


# --- orchestration ----------------------------------------------------------


def _all_routes(water_answer=lambda u, p: HUBEAU):
    return [
        (towns.GEO_API, lambda u, p: LYON_ARRONDISSEMENTS),
        ("https://tabular-api.data.gouv.fr", lambda u, p: {"data": [RENT_ROW]}),
        (risks.GASPAR_API, lambda u, p: GASPAR),
        (water.HUBEAU_API, water_answer),
    ]


def test_arrondissement_uses_parent_for_commune_level_sources(fake_net):
    fake = fake_net(_all_routes())
    [town] = compare_towns.compare(["Lyon 3e"])
    queried = {url: params for url, params in fake.calls}
    assert queried[risks.GASPAR_API]["code_insee"] == "69123"
    assert queried[water.HUBEAU_API]["code_commune"] == "69123"
    assert all(p["INSEE_C__exact"] == "69383" for u, p in fake.calls if "tabular" in u)
    assert town["sources"]["risks"]["scope"] == {"code": "69123", "name": "Lyon"}
    assert town["sources"]["water"]["scope"] == {"code": "69123", "name": "Lyon"}
    assert "scope" not in town["sources"]["rents"]
    assert "[data for Lyon as a whole]" in compare_towns.render_text([town])


def test_failing_source_does_not_sink_the_others(fake_net):
    fake_net(_all_routes(water_answer=lambda u, p: requests.ConnectionError("hub'eau down")))
    [town] = compare_towns.compare(["Lyon 3e"])
    assert "hub'eau down" in town["sources"]["water"]["error"]
    assert town["sources"]["rents"]["segments"]["apartment"]["rent_m2"] == 18.87
    assert town["sources"]["risks"]["risks"][0]["risk"] == "Inondation"
    text = compare_towns.render_text([town])
    assert "error (ConnectionError)" in text and "18.87" in text


def test_main_json_output(fake_net, capsys):
    fake_net(_all_routes())
    assert compare_towns.main(["Lyon 3e", "--json"]) == 0
    [town] = json.loads(capsys.readouterr().out)
    assert town["code"] == "69383"


def test_main_unresolved_town_exits_2(fake_net, capsys):
    fake_net(_all_routes())
    assert compare_towns.main(["Lyon"]) == 2
    assert "use an INSEE code" in capsys.readouterr().err
