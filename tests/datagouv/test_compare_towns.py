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


def _by_code(insee_answer, postal=()):
    """Route a code lookup: postal-code searches get ``postal``, the rest ``insee_answer``."""

    def answer(url, params):
        if "codePostal" in params:
            return list(postal)
        return insee_answer(url, params)

    return answer


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
    fake = fake_net([(towns.GEO_API, _by_code(lambda u, p: {"nom": "Clermont-Ferrand", "code": "63113"}))])
    assert towns.resolve("63113") == Town("63113", "Clermont-Ferrand")
    assert fake.calls[0][0] == f"{towns.GEO_API}/63113"


def test_resolve_code_falls_back_to_arrondissement_type(fake_net):
    def answer(url, params):
        if params.get("type") == "arrondissement-municipal":
            return {"nom": "Lyon 3e Arrondissement", "code": "69383"}
        return _http_404()

    fake_net([(towns.GEO_API, _by_code(answer))])
    assert towns.resolve("69383").name == "Lyon 3e Arrondissement"


def test_resolve_unknown_code(fake_net):
    fake_net([(towns.GEO_API, _by_code(lambda u, p: _http_404()))])
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


def test_resolve_insee_code_that_is_another_postal_code_warns(fake_net):
    lyon = [{"nom": "Lyon", "code": "69123"}]
    fake_net([(towns.GEO_API, _by_code(lambda u, p: {"nom": "Albigny-sur-Saône", "code": "69003"}, lyon))])
    town = towns.resolve("69003")
    assert town == Town("69003", "Albigny-sur-Saône")
    assert town.warnings == (
        "69003 read as INSEE code (Albigny-sur-Saône); it is also the postal code of Lyon (INSEE 69123)",
    )


def test_resolve_insee_code_warning_lists_every_other_postal_commune(fake_net):
    postal = [{"nom": "Picherande", "code": "63279"}, {"nom": "Clermont-Ferrand", "code": "63113"}]
    fake_net([(towns.GEO_API, _by_code(lambda u, p: {"nom": "Clermont-Ferrand", "code": "63113"}, postal))])
    town = towns.resolve("63113")
    assert town == Town("63113", "Clermont-Ferrand")
    assert town.warnings == (
        "63113 read as INSEE code (Clermont-Ferrand); it is also the postal code of Picherande (INSEE 63279)",
    )


def test_resolve_code_that_is_insee_and_postal_of_the_same_commune(fake_net):
    same = [{"nom": "Digne-les-Bains", "code": "04070"}]
    fake_net([(towns.GEO_API, _by_code(lambda u, p: {"nom": "Digne-les-Bains", "code": "04070"}, same))])
    town = towns.resolve("04070")
    assert town == Town("04070", "Digne-les-Bains") and town.warnings == ()


def test_resolve_insee_code_without_postal_homonym_queries_postal_codes(fake_net):
    fake = fake_net([(towns.GEO_API, _by_code(lambda u, p: {"nom": "Clermont-Ferrand", "code": "63113"}))])
    assert towns.resolve("63113") == Town("63113", "Clermont-Ferrand")
    assert (towns.GEO_API, {"codePostal": "63113", "fields": "nom,code"}) in fake.calls


def test_resolve_postal_code_only_suggests_the_communes(fake_net):
    digne = [{"nom": "Digne-les-Bains", "code": "04070"}, {"nom": "Entrages", "code": "04074"}]
    fake_net([(towns.GEO_API, _by_code(lambda u, p: _http_404(), digne))])
    with pytest.raises(TownResolutionError) as raised:
        towns.resolve("04000")
    message = str(raised.value)
    assert "04000 is not an INSEE code" in message
    assert "postal code 04000 = Digne-les-Bains (INSEE code 04070), Entrages (INSEE code 04074)" in message


def test_main_reports_code_warning_on_stderr_json_and_table(fake_net, capsys):
    lyon = [{"nom": "Lyon", "code": "69123"}]
    routes = _all_routes()
    routes[0] = (towns.GEO_API, _by_code(lambda u, p: {"nom": "Albigny-sur-Saône", "code": "69003"}, lyon))
    fake_net(routes)
    warning = "69003 read as INSEE code (Albigny-sur-Saône); it is also the postal code of Lyon (INSEE 69123)"

    assert compare_towns.main(["69003", "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err.strip() == f"warning: {warning}"
    assert json.loads(captured.out)[0]["warnings"] == [warning]

    assert compare_towns.main(["69003"]) == 0
    out = capsys.readouterr().out
    assert any(line.startswith("Warning") and "69003 read as INSEE code" in line for line in out.splitlines())


def test_text_has_no_warning_row_without_warnings(fake_net):
    fake_net(_all_routes())
    report = compare_towns.compare(["Lyon 3e"])
    assert report[0]["warnings"] == []
    assert "Warning" not in compare_towns.render_text(report)


def test_main_town_lookup_unreachable_exits_3(fake_net, capsys):
    fake_net([(towns.GEO_API, lambda u, p: requests.ConnectionError("geo down"))])
    assert compare_towns.main(["Lyon 3e"]) == 3
    assert capsys.readouterr().err.strip() == "error: town lookup failed (ConnectionError)"


def test_main_town_lookup_server_error_exits_3(fake_net, capsys):
    def answer(url, params):
        response = requests.Response()
        response.status_code = 503
        return requests.HTTPError("503", response=response)

    fake_net([(towns.GEO_API, answer)])
    assert compare_towns.main(["63113"]) == 3
    assert "town lookup failed (HTTPError)" in capsys.readouterr().err


# --- resolution edges -------------------------------------------------------


@pytest.mark.parametrize(
    "query, official",
    [
        ("saint etienne", "Saint-Étienne"),
        ("SAINT-ÉTIENNE", "Saint-Étienne"),
        ("l isle sur la sorgue", "L'Isle-sur-la-Sorgue"),
        ("Marseille 1er", "Marseille 1er Arrondissement"),
        ("Paris 20e arrondissement", "Paris 20e Arrondissement"),
    ],
)
def test_normalize_matches_official_name(query, official):
    assert towns.normalize(query) == towns.normalize(official)


def test_normalize_keeps_arrondissement_when_not_trailing():
    assert towns.normalize("Arrondissement de Lyon") == "arrondissement de lyon"


def test_normalize_does_not_confuse_1er_and_10e():
    assert towns.normalize("Paris 1er") != towns.normalize("Paris 10e Arrondissement")


def test_resolve_picks_accented_town_among_prefix_matches(fake_net):
    candidates = [
        {"code": "42218", "nom": "Saint-Étienne"},
        {"code": "42207", "nom": "Saint-Étienne-de-Saint-Geoirs"},
    ]
    fake_net([(towns.GEO_API, lambda u, p: candidates)])
    assert towns.resolve("saint etienne") == Town("42218", "Saint-Étienne")


def test_resolve_sends_name_search_params(fake_net):
    fake = fake_net([(towns.GEO_API, lambda u, p: [{"code": "63113", "nom": "Clermont-Ferrand"}])])
    towns.resolve("  Clermont-Ferrand  ")
    url, params = fake.calls[0]
    assert url == towns.GEO_API
    assert params["nom"] == "Clermont-Ferrand"
    assert params["type"] == "commune-actuelle,arrondissement-municipal"


def test_resolve_null_body_is_not_found(fake_net):
    fake_net([(towns.GEO_API, lambda u, p: None)])
    with pytest.raises(TownResolutionError, match="no town"):
        towns.resolve("Nowhere")


def test_resolve_corsican_code_is_uppercased(fake_net):
    fake = fake_net([(towns.GEO_API, lambda u, p: {"nom": "Ajaccio", "code": "2A004"})])
    assert towns.resolve(" 2a004 ") == Town("2A004", "Ajaccio")
    assert fake.calls[0][0] == f"{towns.GEO_API}/2A004"


@pytest.mark.parametrize("query", ["6911", "691234", "2C004"])
def test_resolve_non_code_digits_go_to_name_search(fake_net, query):
    fake = fake_net([(towns.GEO_API, lambda u, p: [])])
    with pytest.raises(TownResolutionError, match="no town"):
        towns.resolve(query)
    assert fake.calls[0][0] == towns.GEO_API


def test_resolve_code_server_error_propagates(fake_net):
    def answer(url, params):
        response = requests.Response()
        response.status_code = 500
        return requests.HTTPError("500", response=response)

    fake_net([(towns.GEO_API, answer)])
    with pytest.raises(requests.HTTPError):
        towns.resolve("63113")


def test_resolve_code_without_name_falls_through_to_arrondissement(fake_net):
    def answer(url, params):
        if params.get("type") == "arrondissement-municipal":
            return {"nom": "Paris 1er Arrondissement", "code": "75101"}
        return {}

    fake_net([(towns.GEO_API, answer)])
    assert towns.resolve("75101") == Town("75101", "Paris 1er Arrondissement")


@pytest.mark.parametrize(
    "code",
    ["75100", "75121", "75056", "69380", "69390", "13200", "13217", "13055", "2B033", ""],
)
def test_parent_commune_outside_ranges_is_none(code):
    assert towns.parent_commune(code) is None


def test_town_parent_property():
    assert Town("13208", "Marseille 8e Arrondissement").parent == ("13055", "Marseille")
    assert Town("13055", "Marseille").parent is None


# --- source edges -----------------------------------------------------------


def test_rents_missing_numbers_stay_none():
    row = {"loypredm2": None, "lwr.IPm2": None, "upr.IPm2": None, "TYPPRED": "maille"}
    assert rents.parse_row(row) == {
        "rent_m2": None,
        "low_m2": None,
        "high_m2": None,
        "estimated_on": "maille",
        "observations": None,
    }


def test_rents_null_body_means_no_data(fake_net):
    fake_net([("https://tabular-api.data.gouv.fr", lambda u, p: None)])
    assert set(rents.fetch("69383")["segments"].values()) == {None}


def test_risks_attach_deep_subtype_to_shortest_prefix():
    details = [
        {"num_risque": "11", "libelle_risque_long": "Inondation"},
        {"num_risque": "112", "libelle_risque_long": "Crue lente"},
        {"num_risque": "1121", "libelle_risque_long": "Deep"},
    ]
    assert risks.group(details) == [{"risk": "Inondation", "subtypes": ["Crue lente", "Deep"]}]


def test_risks_subtype_listed_before_its_parent():
    details = [
        {"num_risque": "112", "libelle_risque_long": "Crue lente"},
        {"num_risque": "11", "libelle_risque_long": "Inondation"},
    ]
    assert risks.group(details) == [{"risk": "Inondation", "subtypes": ["Crue lente"]}]


def test_risks_sibling_numbers_are_not_prefixes():
    details = [
        {"num_risque": "12", "libelle_risque_long": "Mouvement de terrain"},
        {"num_risque": "13", "libelle_risque_long": "Séisme"},
        {"num_risque": "123", "libelle_risque_long": "Sous 12"},
    ]
    assert risks.group(details) == [
        {"risk": "Mouvement de terrain", "subtypes": ["Sous 12"]},
        {"risk": "Séisme", "subtypes": []},
    ]


def test_risks_entry_without_number_or_label():
    details = [{"libelle_risque_long": "Radon"}, {"num_risque": 18}]
    assert risks.group(details) == [
        {"risk": "Radon", "subtypes": []},
        {"risk": "18", "subtypes": []},
    ]


def test_risks_merge_details_of_every_data_entry(fake_net):
    body = {
        "data": [
            {"risques_detail": [{"num_risque": "11", "libelle_risque_long": "Inondation"}]},
            {"risques_detail": None},
            {"risques_detail": [{"num_risque": "112", "libelle_risque_long": "Crue lente"}]},
        ]
    }
    fake_net([(risks.GASPAR_API, lambda u, p: body)])
    assert risks.fetch("13055")["risks"] == [{"risk": "Inondation", "subtypes": ["Crue lente"]}]


def test_water_sorts_samples_and_keeps_the_limit():
    rows = [_row(f"S{d}", f"2026-07-{d:02d}T08:00:00Z") for d in (3, 12, 7, 1, 12)]
    samples = water.latest_samples(rows, 3)
    assert [s["code_prelevement"] for s in samples] == ["S12", "S7", "S3"]


def test_water_row_without_sample_code_groups_by_date():
    rows = [
        _row(None, "2026-07-01T08:00:00Z"),
        _row(None, "2026-07-01T08:00:00Z"),
        _row(None, "2026-07-02T08:00:00Z"),
    ]
    assert len(water.latest_samples(rows, 10)) == 2


def test_water_keeps_at_most_ten_samples(fake_net):
    rows = [_row(f"S{d}", f"2026-07-{d:02d}T08:00:00Z") for d in range(1, 16)]
    fake = fake_net([(water.HUBEAU_API, lambda u, p: {"data": rows})])
    result = water.fetch("63113")
    assert result["samples"] == 10
    assert result["last_sample_date"] == "2026-07-15"
    assert fake.calls[0][1]["code_commune"] == "63113"


def test_water_tally_unknown_and_missing_verdicts():
    samples = [{"f": "D"}, {"f": "X"}, {"f": None}, {}]
    assert water.tally(samples, "f") == {"derogation": 1, "unknown": 3}


# --- orchestration edges ----------------------------------------------------


def test_commune_queries_every_source_with_its_own_code(fake_net):
    clermont = [{"code": "63113", "nom": "Clermont-Ferrand"}]
    fake = fake_net([(towns.GEO_API, lambda u, p: clermont), *_all_routes()[1:]])
    [town] = compare_towns.compare(["Clermont-Ferrand"])
    queried = {url: params for url, params in fake.calls}
    assert queried[risks.GASPAR_API]["code_insee"] == "63113"
    assert queried[water.HUBEAU_API]["code_commune"] == "63113"
    assert all("scope" not in result for result in town["sources"].values())
    assert "as a whole" not in compare_towns.render_text([town])


def test_parsing_failure_is_isolated_and_keeps_scope(fake_net):
    routes = _all_routes()
    routes[2] = (risks.GASPAR_API, lambda u, p: {"data": "not a list"})
    fake_net(routes)
    [town] = compare_towns.compare(["Lyon 3e"])
    assert town["sources"]["risks"]["error_type"] == "AttributeError"
    assert town["sources"]["risks"]["scope"] == {"code": "69123", "name": "Lyon"}
    assert town["sources"]["water"]["samples"] == 3
    assert "segments" in town["sources"]["rents"]


def test_every_source_failing_still_renders(fake_net):
    fake_net(
        [
            (towns.GEO_API, lambda u, p: LYON_ARRONDISSEMENTS),
            ("https://", lambda u, p: requests.Timeout("slow")),
        ]
    )
    [town] = compare_towns.compare(["Lyon 3e"])
    assert {r["error_type"] for r in town["sources"].values()} == {"Timeout"}
    text = compare_towns.render_text([town])
    assert text.count("error (Timeout)") == len(compare_towns.RENT_SEGMENTS) + 4


def test_one_unresolved_town_stops_before_any_source_call(fake_net):
    fake = fake_net(_all_routes())
    with pytest.raises(TownResolutionError):
        compare_towns.compare(["Lyon 3e", "Lyon"])
    assert all(url == towns.GEO_API for url, _ in fake.calls)


def test_render_empty_results_and_verdict_labels():
    report = [
        {
            "name": "Nowhere",
            "code": "00000",
            "sources": {
                "rents": {"segments": {"apartment": None, "house": {"rent_m2": None}}},
                "risks": {"risks": []},
                "water": {"samples": 0, "last_sample_date": None},
            },
        },
        {
            "name": "Somewhere",
            "code": "11111",
            "sources": {
                "rents": {
                    "segments": {
                        "apartment": {
                            "rent_m2": 9.5,
                            "low_m2": 7.0,
                            "high_m2": 12.25,
                            "estimated_on": "maille",
                            "observations": 12,
                        }
                    }
                },
                "risks": {"risks": [{"risk": "Inondation", "subtypes": ["a", "b"]}]},
                "water": {
                    "samples": 4,
                    "last_sample_date": "2026-07-20",
                    "bacteriological": {"compliant": 2, "non_compliant": 1, "not_applicable": 1},
                    "physico_chemical": {"derogation": 4},
                },
            },
        },
    ]
    text = compare_towns.render_text(report)
    assert "no data" in text
    assert "none listed" in text
    assert "no sample" in text
    assert "9.50 (7.00-12.25)" in text
    assert "n=12, maille" in text
    assert "Inondation (+2)" in text
    water_result = report[1]["sources"]["water"]
    assert compare_towns._water_cell(water_result, "bacteriological") == [
        "2/4 compliant, 1 NON-compliant, 1 n/a"
    ]
    assert "0/4 compliant, 4 derogation" in text


def test_render_commune_estimate_has_no_level_suffix():
    cell = compare_towns._rent_cell({"segments": {"apartment": rents.parse_row(RENT_ROW)}}, "apartment")
    assert cell == ["18.87 (14.58-24.43)", "n=19582"]


def test_main_text_output_has_one_column_per_town(fake_net, capsys):
    def geo(url, params):
        if params.get("nom", "").startswith("Lyon"):
            return LYON_ARRONDISSEMENTS
        return [{"code": "63113", "nom": "Clermont-Ferrand"}]

    fake_net([(towns.GEO_API, geo), *_all_routes()[1:]])
    assert compare_towns.main(["Lyon 3e", "Clermont-Ferrand"]) == 0
    out = capsys.readouterr().out
    town_line = next(line for line in out.splitlines() if line.startswith("Town"))
    assert "Lyon 3e Arrondissement" in town_line and "Clermont-Ferrand" in town_line


# --- network entry point ----------------------------------------------------


class _FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self.body


def test_get_json_sends_params_timeout_and_user_agent(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        seen.update(url=url, params=params, timeout=timeout, headers=headers)
        return _FakeResponse(200, {"ok": True})

    monkeypatch.setattr(requests, "get", fake_get)
    assert net.get_json("https://x.test/a", {"q": 1}) == {"ok": True}
    assert seen["params"] == {"q": 1}
    assert seen["timeout"] == net.TIMEOUT_SECONDS
    assert seen["headers"]["User-Agent"] == net.USER_AGENT


def test_get_json_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(404, None))
    with pytest.raises(requests.HTTPError):
        net.get_json("https://x.test/missing")
