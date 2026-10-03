import pytest

from ingest.curate import (
    VALID_APP_TARGETS,
    VALID_CATEGORIES,
    CurationResult,
    validate_curation,
)


def make_result(**overrides):
    fields = {
        "source_id": "tiktok_111",
        "category": "dev",
        "keep_link": True,
        "extract_knowledge": True,
        "pitch": "One-line pitch",
        "tags": ["python"],
        "summary_md": "# Summary",
        "app_target": None,
        "app_payload": None,
    }
    fields.update(overrides)
    return CurationResult(**fields)


def make_app_result(app_target="kitchen", **overrides):
    fields = {
        "extract_knowledge": False,
        "summary_md": None,
        "app_target": app_target,
        "app_payload": {"title": "Ratatouille"},
    }
    fields.update(overrides)
    return make_result(**fields)


@pytest.mark.parametrize("category", sorted(VALID_CATEGORIES))
def test_valid_note_curation_has_no_errors(category):
    assert validate_curation(make_result(category=category)) == []


def test_link_only_curation_without_summary_is_valid():
    result = make_result(extract_knowledge=False, summary_md=None)
    assert validate_curation(result) == []


@pytest.mark.parametrize("app_target", sorted(VALID_APP_TARGETS))
def test_valid_app_curation_has_no_errors(app_target):
    assert validate_curation(make_app_result(app_target)) == []


@pytest.mark.parametrize("category", ["unknown", "", "Dev", "recipes"])
def test_category_outside_list_is_the_only_error(category):
    assert validate_curation(make_result(category=category)) == [
        f"Invalid category: {category}"
    ]


@pytest.mark.parametrize("summary_md", [None, ""])
def test_extract_knowledge_without_summary_is_the_only_error(summary_md):
    assert validate_curation(make_result(summary_md=summary_md)) == [
        "summary_md required when extract_knowledge=True"
    ]


@pytest.mark.parametrize("pitch", ["", None])
def test_missing_pitch_is_the_only_error(pitch):
    assert validate_curation(make_result(pitch=pitch)) == ["pitch is required"]


@pytest.mark.parametrize("app_target", ["notion", "", "Kitchen"])
def test_unknown_app_target_is_the_only_error(app_target):
    assert validate_curation(make_app_result(app_target)) == [
        f"Invalid app_target: {app_target}"
    ]


@pytest.mark.parametrize("app_target", sorted(VALID_APP_TARGETS))
@pytest.mark.parametrize("app_payload", [None, {}])
def test_app_target_without_payload_is_the_only_error(app_target, app_payload):
    result = make_app_result(app_target, app_payload=app_payload)
    assert validate_curation(result) == [
        "app_payload required when app_target is set"
    ]


@pytest.mark.parametrize("app_target", sorted(VALID_APP_TARGETS))
def test_app_target_with_extract_knowledge_is_the_only_error(app_target):
    result = make_app_result(app_target, extract_knowledge=True, summary_md="# Note")
    errors = validate_curation(result)
    assert len(errors) == 1
    assert errors[0].startswith("extract_knowledge must be False when app_target is set")


def test_app_payload_without_app_target_is_the_only_error():
    result = make_result(app_payload={"title": "Ratatouille"})
    assert validate_curation(result) == ["app_payload set without app_target"]


def test_empty_app_payload_without_app_target_is_valid():
    assert validate_curation(make_result(app_payload={})) == []


def test_independent_violations_are_all_reported_in_order():
    result = make_result(category="unknown", summary_md=None, pitch="")
    assert validate_curation(result) == [
        "Invalid category: unknown",
        "summary_md required when extract_knowledge=True",
        "pitch is required",
    ]


def test_app_target_violations_are_all_reported():
    result = make_app_result(
        "notion", app_payload=None, extract_knowledge=True, summary_md="# Note"
    )
    errors = validate_curation(result)
    assert errors[:2] == [
        "Invalid app_target: notion",
        "app_payload required when app_target is set",
    ]
    assert errors[2].startswith("extract_knowledge must be False")
    assert len(errors) == 3
