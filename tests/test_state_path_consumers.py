import json
from datetime import datetime
from pathlib import Path

import pytest

from ingest import batch, state, triage
from ingest.pivot import Pivot

REPO_STATE = Path(state.__file__).resolve().parent.parent / "state"
VIDEO_ID = "7400000000000000001"
VIDEO_URL = f"https://www.tiktok.com/@user/video/{VIDEO_ID}"


def make_pivot(source_id=VIDEO_ID, description="Une recette de pâtes"):
    return Pivot(
        source_type="tiktok", source_id=source_id, url=VIDEO_URL, title="Titre",
        author="auteur", duration_s=30, published_at=None, fetched_at=datetime(2026, 9, 1),
        lang="fr", raw_text="voix", meta={}, description=description,
    )


@pytest.fixture
def fake_tiktok(monkeypatch):
    class FakeTiktok:
        @staticmethod
        def extract_video_id(url):
            return VIDEO_ID

        @staticmethod
        def extract(url):
            return make_pivot()

    monkeypatch.setitem(batch._HANDLERS, "tiktok", FakeTiktok)


def test_default_state_path_is_a_temporary_directory_not_the_repo_state():
    assert REPO_STATE not in state.state_root().resolve().parents
    assert state.state_root().resolve() != REPO_STATE


def test_triage_collects_pivots_from_state_path(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_PATH", str(tmp_path))
    state.save_pivot(make_pivot())

    rows = triage.collect(None)

    assert [r["id"] for r in rows] == [VIDEO_ID]
    assert rows[0]["theme"] == "recette"


def test_triage_ignores_pivots_of_another_state_directory(tmp_path, monkeypatch):
    other, current = tmp_path / "other", tmp_path / "current"
    other.mkdir()
    current.mkdir()
    monkeypatch.setenv("STATE_PATH", str(other))
    state.save_pivot(make_pivot())
    monkeypatch.setenv("STATE_PATH", str(current))

    assert triage.collect(None) == []


def test_triage_without_state_path_raises(monkeypatch):
    monkeypatch.delenv("STATE_PATH", raising=False)
    with pytest.raises(state.StatePathError):
        triage.collect(None)


def test_batch_extract_one_writes_pivot_and_record_under_state_path(tmp_path, monkeypatch, fake_tiktok):
    monkeypatch.setenv("STATE_PATH", str(tmp_path))

    row = batch.extract_one(VIDEO_URL)

    assert row["status"] == "extracted"
    pivot_file = tmp_path / "pivots" / f"tiktok_{VIDEO_ID}.json"
    record_file = tmp_path / "processed" / f"tiktok_{VIDEO_ID}.json"
    assert pivot_file.exists()
    record = json.loads(record_file.read_text(encoding="utf-8"))
    assert record["status"] == state.STATUS_EXTRACTED
    assert Path(record["pivot_path"]) == pivot_file


def test_batch_select_skips_link_whose_pivot_is_under_state_path(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_PATH", str(tmp_path))
    pivot_path = state.save_pivot(make_pivot())
    state.mark_extracted("tiktok", VIDEO_ID, VIDEO_URL, title="Titre", author="auteur",
                         pivot_path=str(pivot_path))
    export = tmp_path / "user_data_tiktok.json"
    export.write_text(json.dumps({"Likes and Favorites": {"Favorite Videos": {
        "FavoriteVideoList": [{"Date": "2026-09-01 10:00:00", "Link": VIDEO_URL}]}}}),
        encoding="utf-8")

    assert batch.select(export) == []

    pivot_path.unlink()
    assert [e["source_id"] for e in batch.select(export)] == [VIDEO_ID]


def test_writing_a_record_never_creates_a_missing_state_path(tmp_path, monkeypatch):
    missing = tmp_path / "nowhere"
    monkeypatch.setenv("STATE_PATH", str(missing))

    with pytest.raises(state.StatePathError):
        state.mark_extracted("tiktok", VIDEO_ID, VIDEO_URL, title="T", author="a")
    with pytest.raises(state.StatePathError):
        state.save_pivot(make_pivot())

    assert not missing.exists()
