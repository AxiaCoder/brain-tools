import pytest

from ingest.handlers.youtube import extract_video_id

VIDEO_ID = "PJRfgoYDAgk"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com/watch?v={VIDEO_ID}&t=42s",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}?t=10",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://www.youtube.com/v/{VIDEO_ID}",
    ],
)
def test_extracts_id_from_supported_url_forms(url):
    assert extract_video_id(url) == VIDEO_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/",
        "https://www.youtube.com/watch?v=short",
        "https://example.com/watch?v=PJRfgoYDAgk",
        "",
    ],
)
def test_rejects_url_without_video_id(url):
    with pytest.raises(ValueError, match="Invalid YouTube URL"):
        extract_video_id(url)


def test_extracts_id_from_shorts_url():
    assert extract_video_id(f"https://www.youtube.com/shorts/{VIDEO_ID}") == VIDEO_ID
