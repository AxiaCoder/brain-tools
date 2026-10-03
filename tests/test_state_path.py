import pytest

from ingest import media, state


def test_unset_variable_raises(monkeypatch):
    monkeypatch.delenv("STATE_PATH", raising=False)
    with pytest.raises(state.StatePathError, match="STATE_PATH is not set.*\\.env"):
        state.state_root()


def test_empty_variable_raises(monkeypatch):
    monkeypatch.setenv("STATE_PATH", "  ")
    with pytest.raises(state.StatePathError, match="STATE_PATH is not set"):
        state.processed_dir()


def test_missing_directory_raises_and_is_not_created(tmp_path, monkeypatch):
    missing = tmp_path / "nowhere"
    monkeypatch.setenv("STATE_PATH", str(missing))
    with pytest.raises(state.StatePathError, match="not an existing directory"):
        state.pivots_dir()
    assert not missing.exists()


def test_paths_resolve_under_state_path(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_PATH", str(tmp_path))
    assert state.processed_dir() == tmp_path / "processed"
    assert state.pivots_dir() == tmp_path / "pivots"
    assert state.covers_dir() == tmp_path / "covers"
    assert state.state_file("tiktok", "1") == tmp_path / "processed" / "tiktok_1.json"
    assert media.cover_path("tiktok", "1") == tmp_path / "covers" / "tiktok_1.jpg"


def test_resolution_follows_the_variable_at_call_time(tmp_path, monkeypatch):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("STATE_PATH", str(first))
    assert state.processed_dir() == first / "processed"
    monkeypatch.setenv("STATE_PATH", str(second))
    assert state.processed_dir() == second / "processed"


def test_help_does_not_need_the_variable(monkeypatch, capsys):
    monkeypatch.delenv("STATE_PATH", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        state.main(["--help"])
    assert exit_info.value.code == 0
    assert "usage" in capsys.readouterr().out
