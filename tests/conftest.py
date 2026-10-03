import pytest


@pytest.fixture(autouse=True)
def state_path_in_tmp(tmp_path_factory, monkeypatch):
    """Point STATE_PATH at a fresh temporary directory for every test.

    ``ingest`` loads the repo ``.env`` at import, so without this a test that
    forgets to isolate itself would read and write the real state directory.
    """
    root = tmp_path_factory.mktemp("state")
    monkeypatch.setenv("STATE_PATH", str(root))
    return root
