import gzip
import json

import pytest
import requests

from datagouv import compare_towns, net, towns
from datagouv.sources import crime, fibre, rents, risks, transport, water
from datagouv.towns import Town, TownResolutionError

NO_TRANSPORT = {"modes": {mode: {"lines": [], "stops": []} for mode in transport.MODES}}

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


@pytest.fixture(autouse=True)
def crime_cache(monkeypatch, tmp_path):
    cache = tmp_path / "datagouv-cache"
    monkeypatch.setenv("DATAGOUV_CACHE", str(cache))
    return cache


@pytest.fixture(autouse=True)
def overpass_pauses(monkeypatch):
    pauses = []
    monkeypatch.setattr(transport.time, "sleep", pauses.append)
    return pauses


@pytest.fixture
def fake_net(monkeypatch):
    def install(routes):
        fake = FakeNet(routes)
        monkeypatch.setattr(net, "get_json", fake)
        monkeypatch.setattr(net, "post_json", fake)
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
    assert all(p["INSEE_C__exact"] == "69383" for u, p in fake.calls if "INSEE_C__exact" in p)
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
    assert (towns.GEO_API, {"codePostal": "63113", "type": towns.TOWN_TYPES, "fields": "nom,code"}) in fake.calls


def test_resolve_postal_code_only_suggests_the_communes(fake_net):
    digne = [{"nom": "Digne-les-Bains", "code": "04070"}, {"nom": "Entrages", "code": "04074"}]
    fake_net([(towns.GEO_API, _by_code(lambda u, p: _http_404(), digne))])
    with pytest.raises(TownResolutionError) as raised:
        towns.resolve("04000")
    message = str(raised.value)
    assert "04000 is not an INSEE code" in message
    assert "postal code 04000 = Digne-les-Bains (INSEE code 04070), Entrages (INSEE code 04074)" in message
    assert "use one of those INSEE codes" in message


def test_resolve_postal_code_of_a_single_commune_suggests_that_code(fake_net):
    aix = [{"nom": "Aix-en-Provence", "code": "13001"}]
    fake_net([(towns.GEO_API, _by_code(lambda u, p: _http_404(), aix))])
    with pytest.raises(TownResolutionError, match="use that INSEE code or the town name"):
        towns.resolve("13080")


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
    errors = {name: r["error_type"] for name, r in town["sources"].items()}
    assert errors.pop("transport") == "OverpassError"
    assert set(errors.values()) == {"Timeout"}
    text = compare_towns.render_text([town])
    assert text.count("error (Timeout)") == (
        len(compare_towns.RENT_SEGMENTS) + 4 + len(compare_towns.CRIME_INDICATORS) + 1
    )
    assert text.count("error (OverpassError)") == len(compare_towns.TRANSPORT_MODES)


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
                "crime": {"indicators": {}},
                "fibre": {"ftth_share": None}, "transport": NO_TRANSPORT,
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
                "crime": {"indicators": {}},
                "fibre": {"ftth_share": None}, "transport": NO_TRANSPORT,
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


# --- crime ------------------------------------------------------------------

CRIME_HEADER = (
    "CODGEO_2026;annee;indicateur;unite_de_compte;nombre;taux_pour_mille;est_diffuse;"
    "insee_pop;insee_pop_millesime;insee_log;insee_log_millesime;"
    "complement_info_nombre;complement_info_taux"
)


def _crime_line(code, year, indicator, count, rate, diffused="diff", complement="NA;NA"):
    return (
        f'"{code}";"{year}";"{indicator}";"Infraction";{count};{rate};"{diffused}";'
        f'"5409";"2023";"2417";"2022";{complement}'
    )


CRIME_LINES = [
    CRIME_HEADER,
    _crime_line("38185", 2025, "Cambriolages de logement", '"6"', '"2,4823480"'),
    _crime_line("38185", 2024, "Cambriolages de logement", '"9"', '"3,9000000"'),
    _crime_line("38185", 2020, "Cambriolages de logement", '"4"', '"1,7000000"'),
    _crime_line("38185", 2025, "Vols avec armes", "NA", "NA", "ndiff", '"4,5692308";"1,1234425"'),
    _crime_line("38185", 2025, "Destructions et dégradations volontaires", '"12"', '"2,2185247"'),
    _crime_line("38185", 2020, "Destructions et dégradations volontaires", "NA", "NA", "ndiff"),
    _crime_line("38185", 2025, "Vols de véhicule", '"3"', '"0,5"'),
    _crime_line("38185", 2025, "Violences physiques intrafamiliales", '"2"', '"0,4"'),
    _crime_line("69383", 2025, "Cambriolages de logement", '"500"', '"8,1"'),
]

CRIME_URL = "https://static.data.gouv.fr/resources/crime/20260709/donnee-2025.csv.gz"


def _catalog(url=CRIME_URL):
    return {
        "resources": [
            {"type": "main", "format": "parquet", "title": "COM - base (parquet)", "url": "https://x/p.parquet"},
            {"type": "main", "format": "csv", "title": "DEP - base départementale", "url": "https://x/dep.csv"},
            {"type": "main", "format": "csv.gz", "title": "COM - base communale (fichier csv compressé)", "url": url},
        ]
    }


def _gz(path, lines, encoding):
    with gzip.open(path, "wb") as out:
        out.write(("\n".join(lines) + "\n").encode(encoding))


@pytest.fixture
def crime_net(fake_net, monkeypatch):
    def install(catalog=None, lines=CRIME_LINES, encoding="utf-8"):
        state = {"catalog": catalog or _catalog(), "downloads": []}
        fake_net([(crime.CATALOG_API, lambda u, p: state["catalog"])])

        def download(url, path, timeout=None):
            state["downloads"].append(url)
            _gz(path, lines, encoding)

        monkeypatch.setattr(net, "download", download)
        return state

    return install


def test_crime_parses_retained_indicators_of_latest_year(crime_net):
    crime_net()
    result = crime.fetch("38185")
    assert result["year"] == 2025 and result["base_year"] == 2020
    assert result["file"] == CRIME_URL
    assert result["indicators"]["burglary"] == {
        "count": 6,
        "rate_per_mille": 2.48,
        "rate_base": "dwellings",
        "masked": False,
        "rate_change_points": 0.78,
    }
    assert set(result["indicators"]) == {"burglary", "armed_robbery", "vandalism"}


def test_crime_masked_value_is_not_the_comparison_average(crime_net):
    crime_net()
    armed = crime.fetch("38185")["indicators"]["armed_robbery"]
    assert armed["masked"] is True
    assert armed["count"] is None and armed["rate_per_mille"] is None
    assert armed["rate_base"] == "inhabitants"


def test_crime_change_is_none_when_base_year_masked(crime_net):
    crime_net()
    vandalism = crime.fetch("38185")["indicators"]["vandalism"]
    assert vandalism["count"] == 12 and vandalism["rate_per_mille"] == 2.22
    assert vandalism["rate_change_points"] is None


@pytest.mark.parametrize("encoding", ["utf-8", "cp1252"])
def test_crime_reads_accented_labels_in_either_encoding(crime_net, encoding):
    crime_net(encoding=encoding)
    assert "vandalism" in crime.fetch("38185")["indicators"]


def test_crime_town_absent_has_no_indicators(crime_net):
    crime_net()
    assert crime.fetch("99999")["indicators"] == {}


def test_crime_arrondissement_is_queried_with_its_own_code(crime_net):
    crime_net()
    assert crime.COMMUNE_LEVEL_ONLY is False
    assert crime.fetch("69383")["indicators"]["burglary"]["count"] == 500


def test_crime_cache_hit_does_not_download_again(crime_net, crime_cache):
    state = crime_net()
    crime.fetch("38185")
    crime.fetch("69383")
    assert state["downloads"] == [CRIME_URL]
    assert sorted(f.name for f in crime_cache.iterdir()) == [crime.INDEX_FILE]


def test_crime_new_url_in_catalog_refreshes_the_cache(crime_net):
    state = crime_net()
    crime.fetch("38185")
    newer = CRIME_URL.replace("20260709", "20270110")
    state["catalog"] = _catalog(newer)
    assert crime.fetch("38185")["file"] == newer
    assert state["downloads"] == [CRIME_URL, newer]


def test_crime_failed_download_keeps_previous_index(crime_net, monkeypatch, crime_cache):
    state = crime_net()
    crime.fetch("38185")
    state["catalog"] = _catalog(CRIME_URL + "?v2")

    def broken(url, path, timeout=None):
        raise requests.ConnectionError("static down")

    monkeypatch.setattr(net, "download", broken)
    result = crime.fetch("38185")
    assert result["stale"] is True and "static down" in result["stale_reason"]
    assert sorted(f.name for f in crime_cache.iterdir()) == [crime.INDEX_FILE]
    monkeypatch.setattr(net, "download", lambda *a, **k: pytest.fail("no download expected"))
    state["catalog"] = _catalog()
    assert crime.fetch("38185")["indicators"]["burglary"]["count"] == 6


def test_crime_catalog_without_communal_file_raises(crime_net):
    crime_net(catalog={"resources": [{"type": "main", "format": "csv", "title": "DEP - x", "url": "u"}]})
    with pytest.raises(LookupError):
        crime.fetch("38185")


def test_crime_cache_dir_follows_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATAGOUV_CACHE", str(tmp_path / "c"))
    assert crime.cache_dir() == tmp_path / "c"
    monkeypatch.delenv("DATAGOUV_CACHE")
    assert crime.cache_dir().parts[-2:] == ("brain-tools", "datagouv")


def test_render_crime_cells_and_header():
    result = {
        "year": 2025,
        "base_year": 2020,
        "indicators": {
            "burglary": {"count": 6, "rate_per_mille": 2.48, "masked": False, "rate_change_points": 0.78},
            "armed_robbery": {"count": None, "rate_per_mille": None, "masked": True, "rate_change_points": None},
            "vandalism": {"count": 12, "rate_per_mille": 2.22, "masked": False, "rate_change_points": None},
        },
    }
    assert compare_towns._crime_cell(result, "burglary") == ["6 · 2.48‰ (+0.78 pts)"]
    assert compare_towns._crime_cell(result, "armed_robbery") == ["masked"]
    assert compare_towns._crime_cell(result, "vandalism") == ["12 · 2.22‰"]
    assert compare_towns._crime_cell(result, "assault_outside_family") == ["no data"]
    header = "\n".join(compare_towns._crime_header([{"sources": {"crime": result}}]))
    assert "2025" in header and "since 2020" in header and "per 1,000 dwellings" in header


# --- fibre ------------------------------------------------------------------

FIBRE_ROW = {
    "insee_com": "69123",
    "com_lib": "Lyon",
    "locaux_arcep": 385933,
    "locaux_ftth": 381720,
    "ferm_cu26": 40090.17,
    "trimestre": "2026 T2",
}


def test_fibre_share_of_ftth_premises(fake_net):
    fake = fake_net([(fibre.TABULAR_API, lambda u, p: {"data": [FIBRE_ROW]})])
    result = fibre.fetch("69123")
    assert fake.calls[0][1] == {"insee_com__exact": "69123", "page_size": 1}
    assert result["ftth_share"] == 98.9
    assert (result["ftth_premises"], result["premises"], result["quarter"]) == (381720, 385933, "2026 T2")


def test_fibre_town_not_listed(fake_net):
    fake_net([(fibre.TABULAR_API, lambda u, p: {"data": []})])
    result = fibre.fetch("69383")
    assert result["ftth_share"] is None and result["quarter"] is None


def test_fibre_zero_premises_has_no_share(fake_net):
    fake_net([(fibre.TABULAR_API, lambda u, p: {"data": [dict(FIBRE_ROW, locaux_arcep=0, locaux_ftth=0)]})])
    assert fibre.fetch("12345")["ftth_share"] is None


def test_fibre_for_arrondissement_uses_parent_commune(fake_net):
    routes = _all_routes()
    routes.insert(1, (fibre.TABULAR_API, lambda u, p: {"data": [FIBRE_ROW]}))
    fake = fake_net(routes)
    [town] = compare_towns.compare(["Lyon 3e"])
    assert [p for u, p in fake.calls if u == fibre.TABULAR_API] == [{"insee_com__exact": "69123", "page_size": 1}]
    assert town["sources"]["fibre"]["scope"] == {"code": "69123", "name": "Lyon"}
    assert compare_towns._fibre_cell(town["sources"]["fibre"]) == [
        "[data for Lyon as a whole]",
        "98.9% (381720/385933)",
        "2026 T2",
    ]


def test_crime_masked_row_drops_its_published_figures(crime_net):
    crime_net(lines=[
        CRIME_HEADER,
        _crime_line("38185", 2025, "Vols avec armes", '"7"', '"1,3"', "ndiff", '"4,5692308";"1,1234425"'),
    ])
    armed = crime.fetch("38185")["indicators"]["armed_robbery"]
    assert (armed["masked"], armed["count"], armed["rate_per_mille"]) == (True, None, None)


def test_crime_change_is_none_when_latest_year_masked(crime_net):
    crime_net(lines=[
        CRIME_HEADER,
        _crime_line("38185", 2025, "Vols avec armes", "NA", "NA", "ndiff"),
        _crime_line("38185", 2020, "Vols avec armes", '"3"', '"0,6"'),
    ])
    assert crime.fetch("38185")["indicators"]["armed_robbery"]["rate_change_points"] is None


def test_crime_change_is_none_when_base_year_absent(crime_net):
    crime_net(lines=[CRIME_HEADER, _crime_line("38185", 2025, "Vols avec armes", '"3"', '"0,6"')])
    assert crime.fetch("38185")["indicators"]["armed_robbery"]["rate_change_points"] is None


def test_crime_unreadable_index_is_rebuilt(crime_net, crime_cache):
    state = crime_net()
    crime_cache.mkdir(parents=True)
    (crime_cache / crime.INDEX_FILE).write_bytes(b"not a sqlite database")
    assert crime.fetch("38185")["indicators"]["burglary"]["count"] == 6
    assert state["downloads"] == [CRIME_URL]


def test_crime_file_without_retained_indicator_raises_and_leaves_only_the_failure(crime_net, crime_cache):
    crime_net(lines=[CRIME_HEADER, _crime_line("38185", 2025, "Vols de véhicule", '"3"', '"0,5"')])
    with pytest.raises(ValueError):
        crime.fetch("38185")
    assert [f.name for f in crime_cache.iterdir()] == [crime.FAILED_FILE]


class _FakeStream(_FakeResponse):
    def __init__(self, status, chunks):
        super().__init__(status, None)
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        return iter(self.chunks)


def test_download_streams_body_with_timeout_and_user_agent(monkeypatch, tmp_path):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs, url=url)
        return _FakeStream(200, [b"ab", b"cd"])

    monkeypatch.setattr(requests, "get", fake_get)
    target = tmp_path / "out.bin"
    target.write_bytes(b"previous content, longer")
    net.download("https://x.test/f.gz", str(target), timeout=7)
    assert target.read_bytes() == b"abcd"
    assert (seen["url"], seen["stream"], seen["timeout"]) == ("https://x.test/f.gz", True, 7)
    assert seen["headers"]["User-Agent"] == net.USER_AGENT


def test_download_raises_on_http_error_before_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeStream(503, [b"x"]))
    target = tmp_path / "out.bin"
    with pytest.raises(requests.HTTPError):
        net.download("https://x.test/f.gz", str(target))
    assert not target.exists()


def test_fibre_missing_ftth_count_has_no_share(fake_net):
    fake_net([(fibre.TABULAR_API, lambda u, p: {"data": [dict(FIBRE_ROW, locaux_ftth=None)]})])
    result = fibre.fetch("69123")
    assert result["ftth_share"] is None and result["premises"] == 385933


def test_render_fibre_cell_without_data_and_crime_header_without_year():
    assert compare_towns._fibre_cell({"ftth_share": None}) == ["no data"]
    assert compare_towns._crime_header([{"sources": {"crime": {"error": "x", "error_type": "Timeout"}}}]) == [
        "Crime: recorded offences (Ministère de l'Intérieur)."
    ]


def test_crime_catalog_skips_non_main_resources(crime_net):
    doc = {"type": "documentation", "format": "csv.gz", "title": "COM - notice", "url": "https://x/doc.csv.gz"}
    catalog = _catalog()
    catalog["resources"].insert(0, doc)
    crime_net(catalog=catalog)
    assert crime.fetch("38185")["file"] == CRIME_URL


def test_render_text_states_crime_years(crime_net):
    crime_net()
    town = {"query": "x", "code": "38185", "name": "Grenoble", "sources": {
        "rents": {"segments": {}}, "risks": {"risks": []},
        "water": {"samples": 0, "last_sample_date": None},
        "crime": crime.fetch("38185"), "fibre": {"ftth_share": None}, "transport": NO_TRANSPORT,
    }}
    text = compare_towns.render_text([town])
    assert "offences recorded in 2025" in text and "since 2020" in text
    assert "6 · 2.48‰ (+0.78 pts)" in text


# --- optional numbers, geography vintage, stale cache ------------------------


def _report_with(crime_result, rents_result=None):
    return [{"query": "x", "code": "55039", "name": "Somewhere", "sources": {
        "rents": rents_result or {"segments": {}}, "risks": {"risks": []},
        "water": {"samples": 0, "last_sample_date": None},
        "crime": crime_result, "fibre": {"ftth_share": None}, "transport": NO_TRANSPORT,
    }}]


def test_render_crime_diffused_count_without_rate():
    result = {"indicators": {
        "burglary": {"count": 0, "rate_per_mille": None, "masked": False, "rate_change_points": None},
        "vandalism": {"count": None, "rate_per_mille": None, "masked": False, "rate_change_points": None},
    }}
    assert compare_towns._crime_cell(result, "burglary") == ["0 · no rate"]
    assert compare_towns._crime_cell(result, "vandalism") == ["no count · no rate"]


def test_render_text_town_with_zero_population(crime_net):
    crime_net(lines=[CRIME_HEADER, _crime_line("55039", 2025, "Cambriolages de logement", '"0"', "NA")])
    text = compare_towns.render_text(_report_with(crime.fetch("55039")))
    assert "0 · no rate" in text


def test_render_rent_without_interval_or_observations():
    segment = {"rent_m2": 9.5, "low_m2": None, "high_m2": 12.0, "estimated_on": "maille", "observations": None}
    assert compare_towns._rent_cell({"segments": {"apartment": segment}}, "apartment") == ["9.50", "maille"]
    segment = dict(segment, estimated_on="commune")
    assert compare_towns._rent_cell({"segments": {"apartment": segment}}, "apartment") == ["9.50"]


def test_crime_code_column_follows_geography_vintage(crime_net):
    header = CRIME_HEADER.replace("CODGEO_2026", "CODGEO_2027")
    crime_net(lines=[header, _crime_line("38185", 2026, "Cambriolages de logement", '"5"', '"2,0"')])
    assert crime.fetch("38185")["indicators"]["burglary"]["count"] == 5


@pytest.mark.parametrize("header", [
    CRIME_HEADER.replace("CODGEO_2026", "code_commune"),
    CRIME_HEADER.replace("CODGEO_2026", "CODGEO_2026;CODGEO_2027"),
])
def test_crime_code_column_must_be_unique(crime_net, header):
    crime_net(lines=[header, _crime_line("38185", 2025, "Cambriolages de logement", '"5"', '"2,0"')])
    with pytest.raises(ValueError, match="CODGEO_"):
        crime.fetch("38185")


def test_crime_failed_build_is_not_downloaded_again(crime_net):
    state = crime_net(lines=[CRIME_HEADER.replace("CODGEO_2026", "code_commune")])
    with pytest.raises(ValueError):
        crime.fetch("38185")
    with pytest.raises(crime.IndexBuildError, match="CODGEO_"):
        crime.fetch("38185")
    assert state["downloads"] == [CRIME_URL]


def test_crime_failed_build_is_retried_once_expired(crime_net, crime_cache, monkeypatch):
    state = crime_net(lines=[CRIME_HEADER.replace("CODGEO_2026", "code_commune")])
    with pytest.raises(ValueError):
        crime.fetch("38185")
    later = crime.time.time() + crime.FAILED_RETRY_SECONDS + 1
    monkeypatch.setattr(crime.time, "time", lambda: later)
    with pytest.raises(ValueError):
        crime.fetch("38185")
    assert state["downloads"] == [CRIME_URL, CRIME_URL]


def test_crime_new_file_that_fails_to_build_keeps_cached_index(crime_net, monkeypatch):
    state = crime_net()
    crime.fetch("38185")
    newer = CRIME_URL.replace("20260709", "20270110")
    state["catalog"] = _catalog(newer)

    def broken_file(url, path, timeout=None):
        state["downloads"].append(url)
        _gz(path, [CRIME_HEADER.replace("CODGEO_2026", "code_commune")], "utf-8")

    monkeypatch.setattr(net, "download", broken_file)
    for _ in range(2):
        result = crime.fetch("38185")
        assert result["stale"] is True and "CODGEO_" in result["stale_reason"]
        assert result["file"] == CRIME_URL and result["indicators"]["burglary"]["count"] == 6
    assert state["downloads"] == [CRIME_URL, newer]


def test_crime_catalog_down_falls_back_to_cached_index(crime_net):
    state = crime_net()
    fresh = crime.fetch("38185")
    assert "stale" not in fresh
    state["catalog"] = requests.ConnectionError("catalog down")
    result = crime.fetch("38185")
    assert result["stale"] is True and "catalog down" in result["stale_reason"]
    assert result["indicators"] == fresh["indicators"] and result["file"] == CRIME_URL
    text = compare_towns.render_text(_report_with(result))
    assert "catalog down" in text and "cached" in text


def test_crime_catalog_down_without_cache_raises(crime_net, crime_cache):
    state = crime_net()
    state["catalog"] = requests.ConnectionError("catalog down")
    with pytest.raises(requests.ConnectionError):
        crime.fetch("38185")
    assert not (crime_cache / crime.INDEX_FILE).exists()


# --- transport --------------------------------------------------------------


def _node(**tags):
    return {"type": "node", "id": 1, "tags": tags}


def _route(route, ref=None, name=None, **tags):
    tags = dict(tags, type="route", route=route)
    if ref:
        tags["ref"] = ref
    if name:
        tags["name"] = name
    return {"type": "relation", "id": 1, "tags": tags}


OVERPASS = {
    "elements": [
        {"type": "area", "id": 3600000001},
        _node(railway="station", station="subway", name="Saxe - Gambetta"),
        _node(railway="stop", station="subway", name="Saxe - Gambetta"),
        _node(railway="station", station="subway", name="Garibaldi"),
        _node(railway="tram_stop", name="Liberté"),
        _node(railway="tram_stop", name="Liberté"),
        _node(railway="tram_stop"),
        _node(highway="bus_stop", name="Garibaldi"),
        _node(highway="bus_stop", name="Garibaldi"),
        _node(highway="bus_stop", name="Part-Dieu"),
        _node(railway="station", train="yes", name="Lyon Part-Dieu"),
        _node(railway="halt", name="Jean Macé"),
        _route("subway", "D", "Ligne D : Vaise ⇒ Vénissieux"),
        _route("subway", "D", "Ligne D : Vénissieux ⇒ Vaise"),
        _route("subway", "B"),
        _route("tram", "T1"),
        _route("tram", "T1"),
        _route("light_rail", name="Rhônexpress"),
        _route("bus", "C9"),
        _route("trolleybus", "C3"),
        _route("bus", "10"),
        _route("bus", "2"),
        _route("bus"),
        _route("train", "TER 01", service="regional"),
        _route("train", "TER 01", service="regional"),
        _route("train", "A", network="RER"),
        _route("train", "6821", network="TGV InOui", service="national"),
        _route("train", "9241", network="TGV"),
        _route("ferry", "F1"),
    ]
}


def test_transport_counts_lines_by_ref_and_stops_by_name(fake_net):
    fake = fake_net([(transport.ENDPOINTS[0], lambda u, p: OVERPASS)])
    result = transport.fetch("69383")
    modes = result["modes"]
    assert list(modes) == ["subway", "tram", "bus", "train"]
    assert modes["subway"] == {"lines": ["B", "D"], "stops": ["Garibaldi", "Saxe - Gambetta"]}
    assert modes["tram"] == {"lines": ["Rhônexpress", "T1"], "stops": ["Liberté"]}
    assert modes["bus"] == {"lines": ["2", "10", "C3", "C9"], "stops": ["Garibaldi", "Part-Dieu"]}
    assert modes["train"] == {"lines": ["A", "TER 01"], "stops": ["Jean Macé", "Lyon Part-Dieu"]}
    assert result["endpoint"] == transport.ENDPOINTS[0]
    assert "OpenStreetMap contributors" in result["attribution"]
    [(url, params)] = fake.calls
    assert '"ref:INSEE"="69383"' in params["data"]


def test_transport_falls_back_to_the_next_endpoint(fake_net):
    def answer(url, params):
        if url == transport.ENDPOINTS[0]:
            return _http_504()
        if url == transport.ENDPOINTS[1]:
            return {"elements": [], "remark": "runtime error: Query timed out"}
        return OVERPASS

    fake = fake_net([("https://", answer)])
    result = transport.fetch("63113")
    assert [url for url, _ in fake.calls] == transport.ENDPOINTS
    assert result["endpoint"] == transport.ENDPOINTS[2]
    assert result["modes"]["subway"]["lines"] == ["B", "D"]


def test_transport_every_endpoint_failing_is_an_error_cell(fake_net):
    fake_net([(towns.GEO_API, lambda u, p: LYON_ARRONDISSEMENTS), *_all_routes()[1:],
              ("https://", lambda u, p: _http_504())])
    [town] = compare_towns.compare(["Lyon 3e"])
    result = town["sources"]["transport"]
    assert result["error_type"] == "OverpassError"
    assert all(endpoint in result["error"] for endpoint in transport.ENDPOINTS)
    assert "scope" not in result
    assert compare_towns._transport_cell(result, "subway") == ["error (OverpassError), see --json"]
    assert "segments" in town["sources"]["rents"]


def test_transport_unknown_boundary_is_an_error(fake_net):
    fake_net([(transport.ENDPOINTS[0], lambda u, p: {"elements": []})])
    with pytest.raises(transport.OverpassError, match="ref:INSEE=99999"):
        transport.fetch("99999")


def test_transport_arrondissement_is_queried_with_its_own_code(fake_net):
    fake = fake_net([(towns.GEO_API, lambda u, p: LYON_ARRONDISSEMENTS), *_all_routes()[1:],
                     (transport.ENDPOINTS[0], lambda u, p: OVERPASS)])
    [town] = compare_towns.compare(["Lyon 3e"])
    [data] = [p["data"] for u, p in fake.calls if u == transport.ENDPOINTS[0]]
    assert '"ref:INSEE"="69383"' in data
    assert "scope" not in town["sources"]["transport"]


def test_render_transport_cells():
    result = {"modes": {
        "subway": {"lines": ["B", "D"], "stops": [f"S{i}" for i in range(9)]},
        "tram": {"lines": ["A"], "stops": ["Jaude"]},
        "bus": {"lines": ["1", "2"], "stops": ["Gare", "Jaude"]},
        "train": {"lines": [], "stops": []},
    }}
    assert compare_towns._transport_cell(result, "subway") == ["2 lines: B, D", "9 stations"]
    assert compare_towns._transport_cell(result, "tram") == ["1 line: A", "1 stop: Jaude"]
    assert compare_towns._transport_cell(result, "bus") == ["2 lines", "2 stops"]
    assert compare_towns._transport_cell(result, "train") == ["none"]
    text = compare_towns.render_text(_report_with({"indicators": {}}))
    assert "© OpenStreetMap contributors" in text and "Transport, metro" in text


def _http_504():
    response = requests.Response()
    response.status_code = 504
    return requests.HTTPError("504 Server Error: Gateway Timeout", response=response)


def test_post_json_sends_form_timeout_and_user_agent(monkeypatch):
    seen = {}

    def fake_post(url, data=None, timeout=None, headers=None):
        seen.update(url=url, data=data, timeout=timeout, headers=headers)
        return _FakeResponse(200, {"ok": True})

    monkeypatch.setattr(requests, "post", fake_post)
    assert net.post_json("https://x.test/a", {"data": "q"}, timeout=5) == {"ok": True}
    assert seen == {"url": "https://x.test/a", "data": {"data": "q"}, "timeout": 5,
                    "headers": {"User-Agent": net.USER_AGENT}}


def test_transport_retries_busy_endpoints_once_after_a_pause(fake_net, overpass_pauses):
    answers = {transport.ENDPOINTS[0]: [_http_504(), OVERPASS]}

    def answer(url, params):
        if url in answers:
            return answers[url].pop(0)
        return requests.ReadTimeout("slow")

    fake = fake_net([("https://", answer)])
    result = transport.fetch("38185")
    assert [url for url, _ in fake.calls] == [*transport.ENDPOINTS, transport.ENDPOINTS[0]]
    assert overpass_pauses == [transport.RETRY_PAUSE_SECONDS]
    assert result["endpoint"] == transport.ENDPOINTS[0]


def test_transport_gives_up_after_the_second_pass(fake_net, overpass_pauses):
    slow = transport.ENDPOINTS[1]
    fake = fake_net([(slow, lambda u, p: requests.ReadTimeout("slow")), ("https://", lambda u, p: _http_504())])
    with pytest.raises(transport.OverpassError):
        transport.fetch("38185")
    retried = [url for url in transport.ENDPOINTS if url != slow]
    assert [url for url, _ in fake.calls] == [*transport.ENDPOINTS, *retried]
    assert overpass_pauses == [transport.RETRY_PAUSE_SECONDS]


def test_post_json_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(429, None))
    with pytest.raises(requests.HTTPError):
        net.post_json("https://x.test/busy", {"data": "q"})


@pytest.mark.parametrize("tags, mode", [
    ({"route": "train", "service": "regional"}, "train"),
    ({"route": "train", "service": "commuter"}, "train"),
    ({"route": "train", "service": "suburban"}, "train"),
    ({"route": "train", "network": "TER Auvergne-Rhône-Alpes"}, "train"),
    ({"route": "train", "network": "RER"}, "train"),
    ({"route": "train", "network": "Transilien"}, "train"),
    ({"route": "train", "service": "long_distance", "network": "Intercités"}, None),
    ({"route": "train", "network": "TERRA"}, None),
    ({"route": "train"}, None),
    ({"route": "light_rail"}, "tram"),
    ({"route": "trolleybus"}, "bus"),
    ({"route": "ferry"}, None),
    ({}, None),
])
def test_route_mode_keeps_regional_trains_and_groups_modes(tags, mode):
    assert transport.route_mode(tags) == mode


@pytest.mark.parametrize("tags, mode", [
    ({"railway": "station", "station": "subway"}, "subway"),
    ({"railway": "tram_stop"}, "tram"),
    ({"railway": "station", "station": "light_rail"}, "tram"),
    ({"railway": "station", "station": "tram"}, "tram"),
    ({"highway": "bus_stop"}, "bus"),
    ({"railway": "station"}, "train"),
    ({"railway": "station", "station": "train"}, "train"),
    ({"railway": "halt"}, "train"),
    ({"railway": "station", "station": "funicular"}, None),
    ({"railway": "stop"}, None),
    ({}, None),
])
def test_stop_mode_classifies_stop_nodes(tags, mode):
    assert transport.stop_mode(tags) == mode


def test_natural_key_orders_digit_runs_as_numbers():
    assert sorted(["T10", "T2", "B", "10", "2", "C3"], key=transport.natural_key) == [
        "2", "10", "B", "C3", "T2", "T10"
    ]


def test_transport_harmless_remark_is_accepted(fake_net):
    body = dict(OVERPASS, remark="runtime remark: Timeout is unused")
    fake = fake_net([("https://", lambda u, p: body)])
    assert transport.fetch("69383")["endpoint"] == transport.ENDPOINTS[0]
    assert len(fake.calls) == 1


def test_transport_non_json_body_falls_back(fake_net):
    def answer(url, params):
        if url == transport.ENDPOINTS[0]:
            return ValueError("Expecting value: line 1 column 1 (char 0)")
        return OVERPASS

    fake = fake_net([("https://", answer)])
    assert transport.fetch("69383")["endpoint"] == transport.ENDPOINTS[1]
    assert len(fake.calls) == 2


def test_transport_timeouts_only_are_not_retried(fake_net, overpass_pauses):
    fake = fake_net([("https://", lambda u, p: requests.ReadTimeout("slow"))])
    with pytest.raises(transport.OverpassError, match="ReadTimeout"):
        transport.fetch("69383")
    assert [url for url, _ in fake.calls] == transport.ENDPOINTS
    assert overpass_pauses == []


def test_render_transport_name_threshold_is_inclusive():
    eight = [f"S{i}" for i in range(compare_towns.TRANSPORT_LISTED_NAMES)]
    result = {"modes": {"train": {"lines": [], "stops": eight}, "bus": {"lines": ["1"], "stops": ["Gare"]}}}
    assert compare_towns._transport_cell(result, "train") == ["0 lines", "8 stations: " + ", ".join(eight)]
    assert compare_towns._transport_cell(result, "bus") == ["1 line", "1 stop"]


def test_render_text_empty_transport_says_none():
    text = compare_towns.render_text(_report_with({"indicators": {}}))
    rows = [line for line in text.splitlines() if line.startswith("Transport, ")]
    assert len(rows) == len(compare_towns.TRANSPORT_MODES)
    assert all("none" in row for row in rows)


def test_summarize_ignores_stops_with_an_empty_name():
    modes = transport.summarize([_node(highway="bus_stop", name=""), _node(highway="bus_stop", name="Gare")])
    assert modes["bus"]["stops"] == ["Gare"]
