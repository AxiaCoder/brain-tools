import json
from types import SimpleNamespace

import pytest

from ingest import state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "processed")
    monkeypatch.setattr(state, "PIVOT_DIR", tmp_path / "pivots")
    return tmp_path


def url_of(source_id):
    return f"https://www.tiktok.com/@user/video/{source_id}"


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


def save_pivot(source_id, title="Titre"):
    pivot = SimpleNamespace(source_type="tiktok", source_id=source_id, url=url_of(source_id), title=title)
    return state.save_pivot(pivot)


def extracted_with_pivot(source_id, title="Titre"):
    path = save_pivot(source_id, title)
    state.mark_extracted("tiktok", source_id, url_of(source_id),
                         title=title, author="auteur", pivot_path=str(path))


# --- _fmt -------------------------------------------------------------------

def test_fmt_shows_date_status_title_author_and_every_destination():
    record = {"completed_at": "2026-09-30T14:00:00", "status": "done", "title": "T", "author": "a",
              "outputs": {"brain": "inbox/x.md", "bookmark": True, "app": "kitchen"}}
    line, tail = state._fmt(record).split("\n")
    assert line == "2026-09-30T14:00  [done     ] T @a"
    assert tail.strip() == "brain:inbox/x.md | bookmark | app:kitchen"


def test_fmt_without_title_author_or_outputs_uses_placeholders():
    out = state._fmt({"status": "extracted"})
    assert out.startswith("?  [extracted] (titre inconnu)\n")
    assert "@" not in out
    assert out.endswith("-")


def test_fmt_falls_back_to_extracted_at():
    assert state._fmt({"status": "extracted", "extracted_at": "2026-09-01T08:30:00"}).startswith("2026-09-01T08:30")


def test_fmt_discarded_shows_reason():
    assert state._fmt({"status": "done", "discarded": True, "discard_reason": "doublon"}).endswith("ecarte (doublon)")


def test_fmt_discarded_without_reason():
    assert state._fmt({"status": "done", "discarded": True}).endswith("ecarte")


def test_fmt_error_is_truncated_to_60_chars():
    assert state._fmt({"status": "error", "error": "x" * 100}).endswith("erreur:" + "x" * 60)


# --- last -------------------------------------------------------------------

def test_last_without_records_says_none(capsys):
    assert state.main(["last"]) == 0
    assert capsys.readouterr().out.strip() == "Aucun lien traite."


def test_last_lists_most_recent_first_and_honours_n(capsys):
    write_raw("tiktok_1.json", {"source_type": "tiktok", "source_id": "1", "status": "done",
                                "title": "Ancien", "completed_at": "2026-09-01T10:00:00"})
    write_raw("tiktok_2.json", {"source_type": "tiktok", "source_id": "2", "status": "done",
                                "title": "Recent", "completed_at": "2026-09-30T10:00:00"})
    assert state.main(["last"]) == 0
    out = capsys.readouterr().out
    assert out.index("Recent") < out.index("Ancien")

    assert state.main(["last", "1"]) == 0
    out = capsys.readouterr().out
    assert "Recent" in out and "Ancien" not in out


def test_last_rejects_non_integer_count():
    with pytest.raises(SystemExit) as exc:
        state.main(["last", "dix"])
    assert exc.value.code == 2


# --- pending ----------------------------------------------------------------

def test_pending_lists_unprocessed_urls_and_counts_on_stderr(tmp_path, capsys):
    state.mark_done("tiktok", "1")
    export = make_export(tmp_path, [url_of(1), url_of(2)])

    assert state.main(["pending", "--export", str(export)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [url_of(2)]
    assert captured.err.strip() == "# 1 a faire sur 2 favoris"


def test_pending_with_everything_done_lists_nothing(tmp_path, capsys):
    state.mark_done("tiktok", "1")
    export = make_export(tmp_path, [url_of(1)])
    assert state.main(["pending", "--export", str(export)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "# 0 a faire sur 1 favoris"


def test_pending_order_old_reverses_export(tmp_path, capsys):
    urls = [url_of(i) for i in (1, 2, 3)]
    export = make_export(tmp_path, urls)
    assert state.main(["pending", "--export", str(export), "--order", "old"]) == 0
    assert capsys.readouterr().out.splitlines() == list(reversed(urls))


def test_pending_limit_caps_listing_but_reports_full_total(tmp_path, capsys):
    urls = [url_of(i) for i in (1, 2, 3)]
    export = make_export(tmp_path, urls)
    assert state.main(["pending", "--export", str(export), "--limit", "2"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == urls[:2]
    assert captured.err.strip() == "# 3 a faire sur 3 favoris, 2 affiches (--limit)"


def test_pending_verbose_prints_url_date_and_reason(tmp_path, capsys):
    export = make_export(tmp_path, [url_of(1)])
    assert state.main(["pending", "--export", str(export), "--verbose"]) == 0
    assert capsys.readouterr().out.strip() == f"{url_of(1)}\t2026-09-01 10:00:00\tnew"


def test_pending_hides_errors_unless_retry_errors(tmp_path, capsys):
    state.mark_error("tiktok", "1", url_of(1), "boom")
    export = make_export(tmp_path, [url_of(1)])

    assert state.main(["pending", "--export", str(export)]) == 0
    assert capsys.readouterr().out == ""

    assert state.main(["pending", "--export", str(export), "--retry-errors"]) == 0
    assert capsys.readouterr().out.splitlines() == [url_of(1)]


def test_pending_requires_export():
    with pytest.raises(SystemExit) as exc:
        state.main(["pending"])
    assert exc.value.code == 2


def test_pending_rejects_unknown_order(tmp_path):
    export = make_export(tmp_path, [])
    with pytest.raises(SystemExit) as exc:
        state.main(["pending", "--export", str(export), "--order", "random"])
    assert exc.value.code == 2


# --- report -----------------------------------------------------------------

def test_report_without_records_says_none(capsys):
    assert state.main(["report"]) == 0
    assert capsys.readouterr().out.strip() == "0 liens en etat : aucun"


def test_report_counts_statuses_and_lists_stuck_and_errors(capsys):
    state.mark_done("tiktok", "1")
    extracted_with_pivot("2", title="En attente")
    state.mark_extracted("tiktok", "3", url_of(3))
    state.mark_error("tiktok", "4", url_of(4), "boom")

    assert state.main(["report"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "4 liens en etat : done=1, error=1, extracted=2"
    assert "1 pivot(s) en attente de curation" in out
    assert "tiktok_2  En attente" in out
    assert "1 extrait(s) sans pivot - a re-extraire :" in out
    assert f"  {url_of(3)}" in out
    assert "1 en erreur - rejouables avec --retry-errors :" in out
    assert f"{url_of(4)} : boom" in out


def test_report_with_only_done_lists_no_section(capsys):
    state.mark_done("tiktok", "1")
    assert state.main(["report"]) == 0
    assert capsys.readouterr().out.strip() == "1 liens en etat : done=1"


# --- ready ------------------------------------------------------------------

def test_ready_without_pivots_says_none(capsys):
    assert state.main(["ready"]) == 0
    assert capsys.readouterr().out.strip() == "Aucun pivot en attente."


def test_ready_lists_pivots_oldest_first_and_skips_those_without_pivot(capsys):
    extracted_with_pivot("1", title="Premier")
    extracted_with_pivot("2", title="Second")
    state.mark_extracted("tiktok", "3", url_of(3))
    for source_id, when in (("1", "2026-09-30T10:00:00"), ("2", "2026-09-01T10:00:00")):
        record = state.read_record("tiktok", source_id)
        record["extracted_at"] = when
        state._write(record)

    assert state.main(["ready"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["tiktok\t2\tSecond", "tiktok\t1\tPremier"]
    assert captured.err.strip() == "# 2 en attente"

    assert state.main(["ready", "1"]) == 0
    assert capsys.readouterr().out.splitlines() == ["tiktok\t2\tSecond"]


# --- show -------------------------------------------------------------------

def test_show_prints_pivot_json_unescaped(capsys):
    save_pivot("1", title="Société")
    assert state.main(["show", "--type", "tiktok", "--id", "1"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["title"] == "Société"
    assert "Société" in out


def test_show_without_pivot_fails_with_message(capsys):
    assert state.main(["show", "--type", "tiktok", "--id", "404"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Aucun pivot pour tiktok_404" in captured.err


def test_show_requires_id():
    with pytest.raises(SystemExit) as exc:
        state.main(["show", "--type", "tiktok"])
    assert exc.value.code == 2


# --- done -------------------------------------------------------------------

def test_done_writes_record_with_every_output(capsys):
    assert state.main(["done", "--type", "tiktok", "--id", "1", "--brain", "inbox/x.md",
                       "--bookmark", "--app", "kitchen"]) == 0
    record = state.read_record("tiktok", "1")
    assert record["status"] == state.STATUS_DONE
    assert record["outputs"] == {"brain": "inbox/x.md", "bookmark": True, "app": "kitchen"}
    assert record["discarded"] is False
    assert capsys.readouterr().out.strip() == f"done -> {state.state_file('tiktok', '1')}"


def test_done_without_destination_records_empty_outputs():
    assert state.main(["done", "--type", "tiktok", "--id", "1"]) == 0
    assert state.read_record("tiktok", "1")["outputs"] == {"brain": None, "bookmark": False, "app": None}


def test_done_discard_records_reason():
    assert state.main(["done", "--type", "tiktok", "--id", "1", "--discard", "--reason", "doublon"]) == 0
    record = state.read_record("tiktok", "1")
    assert record["discarded"] is True
    assert record["discard_reason"] == "doublon"


def test_done_keeps_identity_and_consumes_pivot():
    extracted_with_pivot("1", title="Gardé")
    assert state.main(["done", "--type", "tiktok", "--id", "1", "--bookmark"]) == 0
    record = state.read_record("tiktok", "1")
    assert record["title"] == "Gardé"
    assert "pivot_path" not in record
    assert not state.pivot_file("tiktok", "1").exists()


def test_done_requires_type():
    with pytest.raises(SystemExit) as exc:
        state.main(["done", "--id", "1"])
    assert exc.value.code == 2


# --- parser -----------------------------------------------------------------

def test_no_subcommand_is_rejected():
    with pytest.raises(SystemExit) as exc:
        state.main([])
    assert exc.value.code == 2


def test_unknown_subcommand_is_rejected():
    with pytest.raises(SystemExit) as exc:
        state.main(["purge"])
    assert exc.value.code == 2
