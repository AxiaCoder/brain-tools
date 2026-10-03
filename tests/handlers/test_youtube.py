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


@pytest.mark.parametrize(
    "url",
    [
        f"https://evil.com/?u=https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://notyoutube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com.evil.com/watch?v={VIDEO_ID}",
        f"https://evil.com/youtu.be/{VIDEO_ID}",
        f"ftp://www.youtube.com/watch?v={VIDEO_ID}",
    ],
)
def test_rejects_url_whose_host_is_not_youtube(url):
    with pytest.raises(ValueError, match="Invalid YouTube URL"):
        extract_video_id(url)


@pytest.mark.parametrize(
    "url",
    [
        f"https://m.youtube.com/shorts/{VIDEO_ID}",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
        f"http://www.youtube.com/watch?v={VIDEO_ID}",
        f"http://youtu.be/{VIDEO_ID}",
    ],
)
def test_accepts_mobile_host_and_plain_http(url):
    assert extract_video_id(url) == VIDEO_ID


@pytest.mark.parametrize(
    "url",
    [
        f"www.youtube.com/watch?v={VIDEO_ID}",
        f"youtube.com/shorts/{VIDEO_ID}",
        f"youtu.be/{VIDEO_ID}",
    ],
)
def test_accepts_url_pasted_without_scheme(url):
    assert extract_video_id(url) == VIDEO_ID


def test_accepts_url_with_surrounding_whitespace():
    url = f"  https://www.youtube.com/watch?v={VIDEO_ID}  \n"
    assert extract_video_id(url) == VIDEO_ID


def test_rejects_schemeless_url_whose_host_is_not_youtube():
    with pytest.raises(ValueError, match="Invalid YouTube URL"):
        extract_video_id(f"evil.com/youtube.com/watch?v={VIDEO_ID}")
