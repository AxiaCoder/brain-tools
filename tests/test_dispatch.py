import pytest

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
