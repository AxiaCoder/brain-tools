import pytest

from ingest.handlers.tiktok import extract_video_id, is_photo_url, to_video_url

VIDEO_ID = "7301234567890123456"
SHORT_CODE = "ZMabc123"


@pytest.mark.parametrize(
    "url, expected",
    [
        (f"https://www.tiktok.com/@some.user/video/{VIDEO_ID}", VIDEO_ID),
        (f"https://www.tiktok.com/@some.user/video/{VIDEO_ID}?is_from_webapp=1&sender_device=pc", VIDEO_ID),
        (f"https://www.tiktok.com/@some.user/photo/{VIDEO_ID}", VIDEO_ID),
        (f"https://www.tiktok.com/@some.user/photo/{VIDEO_ID}?is_from_webapp=1", VIDEO_ID),
        (f"https://vm.tiktok.com/{SHORT_CODE}/", SHORT_CODE),
        (f"https://vm.tiktok.com/{SHORT_CODE}", SHORT_CODE),
        (f"https://vm.tiktok.com/{SHORT_CODE}/?is_from_webapp=1", SHORT_CODE),
        (f"https://www.tiktok.com/t/{SHORT_CODE}/", SHORT_CODE),
        (f"https://www.tiktok.com/t/{SHORT_CODE}?is_from_webapp=1", SHORT_CODE),
        (f"https://www.tiktokv.com/share/video/{VIDEO_ID}/", VIDEO_ID),
        (f"https://www.tiktok.com/share/video/{VIDEO_ID}", VIDEO_ID),
        (f"https://www.tiktok.com/share/photo/{VIDEO_ID}", VIDEO_ID),
        (f"https://m.tiktokv.com/share/photo/{VIDEO_ID}/?u_code=x", VIDEO_ID),
    ],
)
def test_extracts_id_from_supported_url_forms(url, expected):
    assert extract_video_id(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://www.tiktok.com/",
        "https://www.tiktok.com/@some.user",
        "https://www.tiktok.com/@some.user/video/",
        "https://www.tiktok.com/@some.user/video/abc",
        "https://www.tiktok.com/share/video/",
        "https://example.com/@some.user/video/7301234567890123456",
        "https://www.youtube.com/watch?v=PJRfgoYDAgk",
    ],
)
def test_rejects_url_without_tiktok_id(url):
    with pytest.raises(ValueError, match="Invalid TikTok URL"):
        extract_video_id(url)


def test_error_message_names_the_rejected_url():
    with pytest.raises(ValueError, match="https://example.com/nothing"):
        extract_video_id("https://example.com/nothing")


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.tiktok.com/@some.user/photo/{VIDEO_ID}",
        f"https://www.tiktok.com/share/photo/{VIDEO_ID}",
    ],
)
def test_detects_photo_carousel_url(url):
    assert is_photo_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.tiktok.com/@some.user/video/{VIDEO_ID}",
        f"https://vm.tiktok.com/{SHORT_CODE}/",
        f"https://www.tiktok.com/@photography/video/{VIDEO_ID}",
    ],
)
def test_video_url_is_not_a_photo_url(url):
    assert is_photo_url(url) is False


def test_rewrites_photo_path_to_video_path():
    url = f"https://www.tiktok.com/@some.user/photo/{VIDEO_ID}?is_from_webapp=1"
    assert to_video_url(url) == f"https://www.tiktok.com/@some.user/video/{VIDEO_ID}?is_from_webapp=1"


def test_rewritten_url_still_yields_same_id():
    url = f"https://www.tiktok.com/@some.user/photo/{VIDEO_ID}"
    assert extract_video_id(to_video_url(url)) == extract_video_id(url)


def test_video_url_is_left_unchanged():
    url = f"https://www.tiktok.com/@some.user/video/{VIDEO_ID}"
    assert to_video_url(url) == url


def test_rewrite_is_idempotent():
    url = f"https://www.tiktok.com/share/photo/{VIDEO_ID}"
    once = to_video_url(url)
    assert to_video_url(once) == once
    assert not is_photo_url(once)


def test_rewrite_leaves_photo_in_handle_untouched():
    url = f"https://www.tiktok.com/@photography/photo/{VIDEO_ID}"
    assert to_video_url(url) == f"https://www.tiktok.com/@photography/video/{VIDEO_ID}"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/?u=tiktok.com/@a/video/1",
        "https://faketiktok.com/@a/video/1",
        "https://tiktok.com.evil.com/@a/video/1",
        f"https://evil.com/?u=https://vm.tiktok.com/{SHORT_CODE}",
        f"https://evil.com/?u=https://www.tiktokv.com/share/video/{VIDEO_ID}",
        f"ftp://www.tiktok.com/@a/video/{VIDEO_ID}",
    ],
)
def test_rejects_url_whose_host_is_not_tiktok(url):
    with pytest.raises(ValueError):
        extract_video_id(url)


@pytest.mark.parametrize(
    "url, expected",
    [
        (f"https://tiktok.com/@a/video/{VIDEO_ID}", VIDEO_ID),
        (f"https://m.tiktok.com/@a/video/{VIDEO_ID}", VIDEO_ID),
        (f"http://www.tiktok.com/@a/video/{VIDEO_ID}", VIDEO_ID),
        (f"http://vm.tiktok.com/{SHORT_CODE}/", SHORT_CODE),
        (f"https://tiktokv.com/share/video/{VIDEO_ID}", VIDEO_ID),
    ],
)
def test_accepts_allowed_hosts_and_plain_http(url, expected):
    assert extract_video_id(url) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        (f"tiktok.com/@a/video/{VIDEO_ID}", VIDEO_ID),
        (f"vm.tiktok.com/{SHORT_CODE}/", SHORT_CODE),
    ],
)
def test_accepts_url_pasted_without_scheme(url, expected):
    assert extract_video_id(url) == expected


def test_accepts_url_with_surrounding_whitespace():
    url = f" https://www.tiktokv.com/share/video/{VIDEO_ID}/ "
    assert extract_video_id(url) == VIDEO_ID


def test_rejects_schemeless_url_whose_host_is_not_tiktok():
    with pytest.raises(ValueError):
        extract_video_id(f"evil.com/tiktok.com/@a/video/{VIDEO_ID}")
