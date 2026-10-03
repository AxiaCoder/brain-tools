from types import SimpleNamespace

import pytest

from ingest import dispatch as dispatch_module
from ingest import state
from ingest.dispatch import detect_source_type, extract_source_id

SHORTS_URL = "https://www.youtube.com/shorts/PJRfgoYDAgk"


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.youtube.com/watch?v=PJRfgoYDAgk", "youtube"),
        ("https://youtu.be/PJRfgoYDAgk", "youtube"),
        (SHORTS_URL, "youtube"),
        ("https://www.tiktok.com/@user/video/123", "tiktok"),
        ("https://example.com/page", "unknown"),
    ],
)
def test_detects_source_type_from_domain(url, expected):
    assert detect_source_type(url) == expected


def test_extract_source_id_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unknown source type"):
        extract_source_id("https://example.com/page", "unknown")


def test_extract_source_id_returns_id_for_youtube_shorts():
    assert extract_source_id(SHORTS_URL, "youtube") == "PJRfgoYDAgk"


PHOTO_ID = "7301234567890123456"
CLEAN_PHOTO_URL = f"https://www.tiktokv.com/share/photo/{PHOTO_ID}/"
PADDED_PHOTO_URL = f" {CLEAN_PHOTO_URL} \n"


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture
def received_urls(monkeypatch, isolated_state):
    received = []

    def fake_extract(url):
        received.append(url)
        return SimpleNamespace(title="A title", author="someone")

    monkeypatch.setattr(dispatch_module.tiktok, "extract", fake_extract)
    monkeypatch.setattr(dispatch_module.youtube, "extract", fake_extract)
    return received


@pytest.fixture
def failing_handler(monkeypatch, isolated_state):
    received = []

    def fake_extract(url):
        received.append(url)
        raise RuntimeError("Unsupported URL")

    monkeypatch.setattr(dispatch_module.tiktok, "extract", fake_extract)
    return received


def test_dispatch_passes_stripped_url_to_handler(received_urls):
    dispatch_module.dispatch(PADDED_PHOTO_URL)

    assert received_urls == [CLEAN_PHOTO_URL]


def test_dispatch_records_stripped_url_on_extraction(received_urls):
    dispatch_module.dispatch(PADDED_PHOTO_URL)

    assert state.read_record("tiktok", PHOTO_ID)["url"] == CLEAN_PHOTO_URL


def test_dispatch_records_stripped_url_on_handler_error(failing_handler):
    with pytest.raises(RuntimeError):
        dispatch_module.dispatch(PADDED_PHOTO_URL)

    record = state.read_record("tiktok", PHOTO_ID)
    assert record["status"] == state.STATUS_ERROR
    assert record["url"] == CLEAN_PHOTO_URL


def test_dispatch_passes_clean_url_unchanged(received_urls):
    dispatch_module.dispatch(CLEAN_PHOTO_URL)

    assert received_urls == [CLEAN_PHOTO_URL]
    assert state.read_record("tiktok", PHOTO_ID)["url"] == CLEAN_PHOTO_URL


@pytest.fixture
def unconfigured_handler(monkeypatch):
    monkeypatch.delenv("STATE_PATH", raising=False)
    calls = []
    monkeypatch.setattr(dispatch_module.tiktok, "extract", calls.append)
    return calls


def test_dispatch_without_state_path_fails_before_any_download(unconfigured_handler):
    with pytest.raises(state.StatePathError):
        dispatch_module.dispatch(CLEAN_PHOTO_URL, skip_if_processed=False)
    assert unconfigured_handler == []


def test_dispatch_batch_without_state_path_stops_instead_of_failing_each_link(unconfigured_handler):
    with pytest.raises(state.StatePathError):
        dispatch_module.dispatch_batch([CLEAN_PHOTO_URL, CLEAN_PHOTO_URL], skip_if_processed=False)
    assert unconfigured_handler == []
