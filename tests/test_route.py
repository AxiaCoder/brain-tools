from datetime import datetime

import pytest

from ingest import route
from ingest.curate import CurationResult
from ingest.pivot import Pivot

FIXED_NOW = datetime(2026, 9, 30, 14, 0, 0)
DATE = "2026-09-30"
URL = "https://www.tiktok.com/@user/video/111"


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


@pytest.fixture(autouse=True)
def isolated_brain(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    brain.mkdir()
    monkeypatch.setenv("BRAIN_PATH", str(brain))
    monkeypatch.setenv("AUTO_ROUTE", "false")
    monkeypatch.setattr(route, "datetime", FrozenDatetime)
    return brain


def make_pivot(**overrides):
    fields = dict(
        source_type="tiktok",
        source_id="111",
        url=URL,
        title="Qui est la victime de ta société ?",
        author="@auteur",
        duration_s=42,
        published_at=None,
        fetched_at=FIXED_NOW,
        lang="fr",
        raw_text="",
        meta={},
    )
    fields.update(overrides)
    return Pivot(**fields)


def make_curation(**overrides):
    fields = dict(
        source_id="111",
        category="ecriture",
        keep_link=False,
        extract_knowledge=True,
        pitch="Un antagoniste façonné par sa société",
        tags=["worldbuilding", "personnage"],
        summary_md="## Idées\n\nLa société crée ses propres victimes.",
    )
    fields.update(overrides)
    return CurationResult(**fields)


# --- slugify: shape ---------------------------------------------------------

def test_slugify_lowercases_and_joins_words_with_hyphens():
    assert route.slugify("Hello World") == "hello-world"


def test_slugify_collapses_runs_of_separators_into_one_hyphen():
    assert route.slugify("a  --  b!!c") == "a-b-c"


def test_slugify_strips_leading_and_trailing_hyphens():
    assert route.slugify("  ?Hello!  ") == "hello"


def test_slugify_keeps_digits():
    assert route.slugify("Top 10 tips 2026") == "top-10-tips-2026"


def test_slugify_truncates_to_50_characters():
    assert route.slugify("a" * 80) == "a" * 50


def test_slugify_of_only_symbols_is_empty():
    assert route.slugify("?!…") == ""


# --- slugify: transliteration ------------------------------------------------

def test_slugify_transliterates_accented_title():
    assert route.slugify("Qui est la victime de ta société ?").startswith(
        "qui-est-la-victime-de-ta-societe"
    )


def test_slugify_transliterates_e_acute_without_splitting_the_word():
    assert route.slugify("Idées") == "idees"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Œuvre", "oeuvre"),
        ("cœur", "coeur"),
        ("Façon", "facon"),
        ("à la carte", "a-la-carte"),
        ("Être prêt", "etre-pret"),
        ("Noël où", "noel-ou"),
    ],
)
def test_slugify_transliterates_french_letters(text, expected):
    assert route.slugify(text) == expected


# --- generate_markdown ------------------------------------------------------

def test_generate_markdown_writes_frontmatter_fields():
    md = route.generate_markdown(make_pivot(), make_curation())
    head = md.split("---")[1]
    assert "source: tiktok" in head
    assert f"url: {URL}" in head
    assert "proposed_domain: ecriture" in head
    assert "tags: ['worldbuilding', 'personnage']" in head
    assert "status: pending" in head
    assert f"ingested_at: {DATE}" in head


def test_generate_markdown_opens_and_closes_frontmatter():
    lines = route.generate_markdown(make_pivot(), make_curation()).split("\n")
    assert lines[0] == "---"
    assert lines.count("---") == 2


def test_generate_markdown_has_title_and_pitch_after_frontmatter():
    md = route.generate_markdown(make_pivot(), make_curation())
    body = md.split("---", 2)[2]
    assert "# Qui est la victime de ta société ?" in body
    assert "> Un antagoniste façonné par sa société" in body
    assert body.index("# Qui est") < body.index("> Un antagoniste")


def test_generate_markdown_keeps_accents_in_content():
    md = route.generate_markdown(make_pivot(), make_curation())
    assert "## Idées" in md
    assert "La société crée ses propres victimes." in md


def test_generate_markdown_omits_summary_when_none():
    md = route.generate_markdown(make_pivot(), make_curation(summary_md=None))
    assert "## Idées" not in md
    assert "## Source" in md


def test_generate_markdown_source_section_lists_author_duration_link():
    md = route.generate_markdown(make_pivot(), make_curation())
    source = md.split("## Source", 1)[1]
    assert "- **Auteur** : @auteur" in source
    assert "- **Duree** : 42s" in source
    assert f"- **Lien** : {URL}" in source


def test_generate_markdown_omits_duration_when_unknown():
    md = route.generate_markdown(make_pivot(duration_s=None), make_curation())
    assert "Duree" not in md


def test_generate_markdown_lists_every_source_of_a_consolidated_capture():
    extra = ["https://www.tiktok.com/@user/video/222", "https://www.tiktok.com/@user/video/333"]
    md = route.generate_markdown(make_pivot(meta={"extra_urls": extra}), make_curation())
    head = md.split("---")[1]
    assert "sources: 3" in head
    assert f"source_urls:\n  - {URL}\n  - {extra[0]}\n  - {extra[1]}" in head


def test_generate_markdown_single_source_has_no_source_list():
    md = route.generate_markdown(make_pivot(), make_curation())
    assert "source_urls" not in md
    assert "sources:" not in md


# --- route_to_brain ---------------------------------------------------------

def test_route_to_brain_without_auto_route_writes_to_inbox_category(isolated_brain):
    pivot = make_pivot(title="Hello World")
    path = route.route_to_brain(pivot, make_curation())
    assert path == isolated_brain / "inbox" / "ecriture" / f"{DATE}-hello-world.md"
    assert path.exists()


def test_route_to_brain_writes_generated_markdown_as_utf8(isolated_brain):
    pivot, curation = make_pivot(), make_curation()
    path = route.route_to_brain(pivot, curation)
    assert path.read_text(encoding="utf-8") == route.generate_markdown(pivot, curation)


def test_route_to_brain_without_auto_route_writes_no_index(isolated_brain):
    path = route.route_to_brain(make_pivot(), make_curation())
    assert not (path.parent / "INDEX.md").exists()
    assert list(isolated_brain.rglob("INDEX.md")) == []


def test_route_to_brain_with_auto_route_uses_category_path(isolated_brain, monkeypatch):
    monkeypatch.setenv("AUTO_ROUTE", "true")
    path = route.route_to_brain(make_pivot(title="Hello World"), make_curation())
    assert path == isolated_brain / "domains" / "ecriture" / "captures" / f"{DATE}-hello-world.md"


def test_route_to_brain_auto_route_unknown_category_falls_back_to_inbox(isolated_brain, monkeypatch):
    monkeypatch.setenv("AUTO_ROUTE", "true")
    path = route.route_to_brain(make_pivot(title="X"), make_curation(category="nope"))
    assert path.parent == isolated_brain / "inbox"


def test_route_to_brain_with_auto_route_creates_index(isolated_brain, monkeypatch):
    monkeypatch.setenv("AUTO_ROUTE", "true")
    path = route.route_to_brain(make_pivot(title="Hello | World"), make_curation())
    index = (path.parent / "INDEX.md").read_text(encoding="utf-8")
    assert f"updated: {DATE}" in index
    assert "# Captures — ecriture" in index
    assert f"| {DATE} | Hello / World | tiktok | [{path.name}](./{path.name}) |" in index


def test_route_to_brain_with_auto_route_appends_to_existing_index(isolated_brain, monkeypatch):
    monkeypatch.setenv("AUTO_ROUTE", "true")
    dest = isolated_brain / "domains" / "ecriture" / "captures"
    dest.mkdir(parents=True)
    (dest / "INDEX.md").write_text(
        "---\nupdated: 2026-01-01\n---\n\n| Date | Titre | Source | Fichier |\n|---|---|---|---|\n| old | row | x | y |",
        encoding="utf-8",
    )
    path = route.route_to_brain(make_pivot(title="Hello World"), make_curation())
    index = (dest / "INDEX.md").read_text(encoding="utf-8")
    assert "updated: 2026-01-01" not in index
    assert f"updated: {DATE}" in index
    assert "| old | row | x | y |\n" in index
    assert index.endswith(f"| {DATE} | Hello World | tiktok | [{path.name}](./{path.name}) |\n")


def test_auto_route_value_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTE", "TRUE")
    assert route.is_auto_route_enabled() is True
    monkeypatch.setenv("AUTO_ROUTE", "yes")
    assert route.is_auto_route_enabled() is False


def test_route_to_brain_returns_none_and_writes_nothing_without_extract(isolated_brain):
    assert route.route_to_brain(make_pivot(), make_curation(extract_knowledge=False)) is None
    assert list(isolated_brain.iterdir()) == []


def test_get_brain_path_reads_env(isolated_brain):
    assert route.get_brain_path() == isolated_brain


# --- route_to_app -----------------------------------------------------------

def test_route_to_app_returns_none_without_target():
    assert route.route_to_app(make_pivot(), make_curation()) is None


def test_route_to_app_returns_target_and_payload_with_source_url():
    curation = make_curation(app_target="kitchen", app_payload={"name": "Tarte"})
    assert route.route_to_app(make_pivot(), curation) == {
        "target": "kitchen",
        "payload": {"name": "Tarte", "sourceUrl": URL},
    }


def test_route_to_app_keeps_explicit_source_url():
    curation = make_curation(app_target="kitchen", app_payload={"sourceUrl": "https://other"})
    assert route.route_to_app(make_pivot(), curation)["payload"]["sourceUrl"] == "https://other"


def test_route_to_app_does_not_mutate_curation_payload():
    payload = {"name": "Tarte"}
    route.route_to_app(make_pivot(), make_curation(app_target="kitchen", app_payload=payload))
    assert payload == {"name": "Tarte"}


def test_route_to_app_without_payload_carries_only_source_url():
    result = route.route_to_app(make_pivot(), make_curation(app_target="kitchen"))
    assert result["payload"] == {"sourceUrl": URL}
