import json
from types import SimpleNamespace

import pytest

from ingest import state

TIKTOK_URL = "https://www.tiktok.com/@user/video/111"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "processed")
    monkeypatch.setattr(state, "PIVOT_DIR", tmp_path / "pivots")
    return tmp_path


def write_raw(name, payload):
    state.STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = state.STATE_DIR / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_export(tmp_path, links):
    data = {
        "Likes and Favorites": {
            "Favorite Videos": {
                "FavoriteVideoList": [
                    {"Date": f"2026-09-0{i + 1} 10:00:00", "Link": link}
                    for i, link in enumerate(links)
                ]
            }
        }
    }
    path = tmp_path / "user_data_tiktok.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_mark_extracted_records_identity_and_extracted_status():
    state.mark_extracted("tiktok", "111", TIKTOK_URL, title="T", author="A", pivot_path="p.json")

    record = state.read_record("tiktok", "111")
    assert record["status"] == state.STATUS_EXTRACTED
    assert (record["url"], record["title"], record["author"]) == (TIKTOK_URL, "T", "A")
    assert record["pivot_path"] == "p.json"
    assert record["completed_at"] is None
    assert record["extracted_at"]


def test_mark_done_keeps_identity_and_records_destinations():
    state.mark_extracted("tiktok", "111", TIKTOK_URL, title="T", author="A")
    state.mark_done("tiktok", "111", brain="domains/x.md", bookmark=True, app="kitchen:slug")

    record = state.read_record("tiktok", "111")
    assert record["status"] == state.STATUS_DONE
    assert record["outputs"] == {"brain": "domains/x.md", "bookmark": True, "app": "kitchen:slug"}
    assert (record["url"], record["title"], record["author"]) == (TIKTOK_URL, "T", "A")
    assert record["completed_at"]
    assert record["discarded"] is False
    assert record["discard_reason"] is None
    assert "pivot_path" not in record


def test_mark_done_without_prior_record_creates_one():
    state.mark_done("youtube", "abc", brain="b.md")

    record = state.read_record("youtube", "abc")
    assert record["status"] == state.STATUS_DONE
    assert record["url"] is None
    assert record["outputs"]["brain"] == "b.md"


def test_mark_done_discarded_keeps_reason_only_when_discarded():
    state.mark_done("tiktok", "1", discarded=True, reason="hors sujet")
    state.mark_done("tiktok", "2", discarded=False, reason="ignored")

    assert state.read_record("tiktok", "1")["discard_reason"] == "hors sujet"
    assert state.read_record("tiktok", "1")["discarded"] is True
    assert state.read_record("tiktok", "2")["discard_reason"] is None


def test_mark_done_clears_previous_error():
    state.mark_error("tiktok", "111", TIKTOK_URL, "boom")
    state.mark_done("tiktok", "111", bookmark=True)

    record = state.read_record("tiktok", "111")
    assert record["status"] == state.STATUS_DONE
    assert record["error"] is None


def test_mark_error_records_error_status_and_message():
    state.mark_error("tiktok", "111", TIKTOK_URL, "video unavailable")

    record = state.read_record("tiktok", "111")
    assert record["status"] == state.STATUS_ERROR
    assert record["error"] == "video unavailable"
    assert record["url"] == TIKTOK_URL
    assert record["completed_at"] is None


def test_read_record_returns_none_when_absent():
    assert state.read_record("tiktok", "missing") is None


def test_read_record_treats_corrupt_file_as_unseen(capsys):
    write_raw("tiktok_111.json", {}).write_text("{not json", encoding="utf-8")

    assert state.read_record("tiktok", "111") is None
    assert state.should_skip("tiktok", "111") is False
    assert "unreadable record tiktok_111.json" in capsys.readouterr().err


def test_should_skip_unknown_link_is_false():
    assert state.should_skip("tiktok", "nope") is False


def test_should_skip_done_link():
    state.mark_done("tiktok", "111")
    assert state.should_skip("tiktok", "111") is True
    assert state.should_skip("tiktok", "111", retry_errors=True) is True


def test_should_skip_resumes_extracted_link():
    state.mark_extracted("tiktok", "111", TIKTOK_URL)
    assert state.should_skip("tiktok", "111") is False


def test_should_skip_error_only_retried_with_retry_errors():
    state.mark_error("tiktok", "111", TIKTOK_URL, "boom")
    assert state.should_skip("tiktok", "111") is True
    assert state.should_skip("tiktok", "111", retry_errors=True) is False


def test_normalize_legacy_ok_record_reads_as_done():
    path = write_raw("tiktok_7675791138401340704.json",
                     {"processed_at": "2026-08-01T10:00:00", "status": "ok"})

    record = state._normalize(json.loads(path.read_text()), path)
    assert record["source_type"] == "tiktok"
    assert record["source_id"] == "7675791138401340704"
    assert record["status"] == state.STATUS_DONE
    assert record["extracted_at"] == "2026-08-01T10:00:00"
    assert record["completed_at"] == "2026-08-01T10:00:00"
    assert record["legacy"] is True
    assert record["outputs"] == {"brain": None, "bookmark": False, "app": None}


def test_normalize_legacy_error_record_reads_as_error():
    path = write_raw("youtube_abc.json",
                     {"processed_at": "2026-08-01T10:00:00", "status": "error", "error": "x"})

    record = state._normalize(json.loads(path.read_text()), path)
    assert record["status"] == state.STATUS_ERROR
    assert record["completed_at"] is None
    assert record["error"] == "x"


def test_normalize_legacy_record_without_status_reads_as_done():
    path = write_raw("tiktok_5.json", {"processed_at": "2026-08-01T10:00:00"})

    assert state._normalize({"processed_at": "2026-08-01T10:00:00"}, path)["status"] == state.STATUS_DONE


def test_normalize_leaves_current_record_untouched():
    current = {"source_type": "tiktok", "source_id": "1", "status": "extracted", "title": "T"}
    assert state._normalize(current, state.STATE_DIR / "tiktok_1.json") is current


def test_legacy_record_is_skipped_and_never_rewritten():
    path = write_raw("tiktok_9.json", {"processed_at": "2026-08-01T10:00:00", "status": "ok"})
    before = path.read_text()

    assert state.should_skip("tiktok", "9") is True
    assert path.read_text() == before


def test_mark_done_on_legacy_record_drops_legacy_flag():
    write_raw("tiktok_9.json", {"processed_at": "2026-08-01T10:00:00", "status": "error"})
    state.mark_done("tiktok", "9", bookmark=True)

    raw = json.loads(state.state_file("tiktok", "9").read_text(encoding="utf-8"))
    assert "legacy" not in raw
    assert raw["status"] == state.STATUS_DONE


def test_save_and_load_pivot_round_trip():
    pivot = SimpleNamespace(source_type="tiktok", source_id="111", url=TIKTOK_URL,
                            transcript="bonjour é", tags=["a", "b"])

    path = state.save_pivot(pivot)

    assert path == state.pivot_file("tiktok", "111")
    assert state.load_pivot("tiktok", "111") == vars(pivot)


def test_save_pivot_serialises_non_json_values_as_strings():
    from datetime import datetime
    pivot = SimpleNamespace(source_type="tiktok", source_id="1", at=datetime(2026, 9, 1, 10, 0))

    state.save_pivot(pivot)
    assert state.load_pivot("tiktok", "1")["at"] == "2026-09-01 10:00:00"


def test_load_pivot_absent_or_corrupt_returns_none(capsys):
    assert state.load_pivot("tiktok", "none") is None

    state.PIVOT_DIR.mkdir(parents=True)
    state.pivot_file("tiktok", "bad").write_text("{oops", encoding="utf-8")
    assert state.load_pivot("tiktok", "bad") is None
    assert "unreadable pivot" in capsys.readouterr().err


def test_mark_done_deletes_pivot():
    state.save_pivot(SimpleNamespace(source_type="tiktok", source_id="111", url=TIKTOK_URL))
    state.mark_extracted("tiktok", "111", TIKTOK_URL, pivot_path="x")
    assert state.pivot_file("tiktok", "111").exists()

    state.mark_done("tiktok", "111", brain="b.md")

    assert not state.pivot_file("tiktok", "111").exists()
    assert state.load_pivot("tiktok", "111") is None


def test_mark_done_without_pivot_does_not_fail():
    state.mark_done("tiktok", "111")
    assert state.read_record("tiktok", "111")["status"] == state.STATUS_DONE


def test_all_records_sorted_most_recent_first_and_skips_corrupt(capsys):
    write_raw("tiktok_1.json", {"processed_at": "2026-08-01T10:00:00", "status": "ok"})
    write_raw("tiktok_2.json", {"processed_at": "2026-08-03T10:00:00", "status": "ok"})
    (state.STATE_DIR / "tiktok_3.json").write_text("garbage", encoding="utf-8")

    ids = [r["source_id"] for r in state.all_records()]

    assert ids == ["2", "1"]
    assert "unreadable record tiktok_3.json" in capsys.readouterr().err


def test_all_records_empty_when_state_dir_missing():
    assert state.all_records() == []


def test_read_export_returns_favourites_in_export_order(tmp_path):
    path = make_export(tmp_path, ["https://www.tiktok.com/@u/video/1",
                                  "https://www.tiktok.com/@u/video/2"])

    entries = state.read_export(path)

    assert entries == [
        {"date": "2026-09-01 10:00:00", "url": "https://www.tiktok.com/@u/video/1"},
        {"date": "2026-09-02 10:00:00", "url": "https://www.tiktok.com/@u/video/2"},
    ]


def test_read_export_drops_entries_without_link(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"Likes and Favorites": {"Favorite Videos": {
        "FavoriteVideoList": [{"Date": "d"}, {"Date": "d2", "Link": "https://www.tiktok.com/@u/video/9"}]
    }}}), encoding="utf-8")

    assert [e["url"] for e in state.read_export(path)] == ["https://www.tiktok.com/@u/video/9"]


@pytest.mark.parametrize("payload", [{}, {"Likes and Favorites": None}, []])
def test_read_export_rejects_non_tiktok_export(tmp_path, payload):
    path = tmp_path / "other.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Is this a TikTok data export"):
        state.read_export(path)


def test_pending_classifies_each_export_entry(tmp_path):
    path = make_export(tmp_path, [
        "https://www.tiktok.com/@u/video/1",  # done
        "https://www.tiktok.com/@u/video/2",  # extracted
        "https://www.tiktok.com/@u/video/3",  # error
        "https://www.tiktok.com/@u/video/4",  # new
        "https://example.com/page",           # unrecognised
    ])
    state.mark_done("tiktok", "1")
    state.mark_extracted("tiktok", "2", "https://www.tiktok.com/@u/video/2")
    state.mark_error("tiktok", "3", "https://www.tiktok.com/@u/video/3", "boom")

    result = state.pending(state.read_export(path))

    assert [(e.get("source_id"), e["reason"]) for e in result] == [
        ("2", "extracted"),
        ("4", "new"),
        (None, "unrecognised"),
    ]
    assert result[1]["source_type"] == "tiktok"
    assert result[1]["date"] == "2026-09-04 10:00:00"


def test_pending_with_retry_errors_includes_errored_links(tmp_path):
    path = make_export(tmp_path, ["https://www.tiktok.com/@u/video/3"])
    state.mark_error("tiktok", "3", "https://www.tiktok.com/@u/video/3", "boom")

    result = state.pending(state.read_export(path), retry_errors=True)

    assert [(e["source_id"], e["reason"]) for e in result] == [("3", "error")]
