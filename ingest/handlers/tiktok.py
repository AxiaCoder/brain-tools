"""TikTok video handler."""

import re
import subprocess
import sys
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..pivot import Pivot
from ..stt import transcribe
from ..screen import ScreenText, read_images, read_video
from ..media import cover_path


class VideoUnavailableError(Exception):
    """Video is private, deleted, or unavailable."""
    pass


def extract_video_id(url: str) -> str:
    """
    Extract TikTok video ID from URL.

    Supports:
    - https://www.tiktok.com/@user/video/1234567890
    - https://vm.tiktok.com/ABC123/
    - https://www.tiktok.com/t/ABC123/
    - https://www.tiktokv.com/share/video/1234567890/   (data export form)

    Args:
        url: TikTok URL

    Returns:
        Video ID (numeric ID or short code)

    Raises:
        ValueError: If URL is not a valid TikTok URL
    """
    patterns = [
        r'tiktok\.com/@[^/]+/(?:video|photo)/(\d+)',  # Video or photo carousel
        r'vm\.tiktok\.com/([a-zA-Z0-9]+)',  # Short URL
        r'tiktok\.com/t/([a-zA-Z0-9]+)',    # Short URL alternative
        # The form used by every link in the TikTok data export. The id is
        # written in clear, so no redirect has to be resolved: the HTTP hop
        # only rewrites tiktokv.com -> tiktok.com and never reaches the
        # canonical @user/video/<id>, which the page builds in JavaScript.
        # yt-dlp and gallery-dl both accept this URL as-is.
        r'tiktokv?\.com/share/(?:video|photo)/(\d+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    raise ValueError(f"Invalid TikTok URL: {url}")


def fetch_video_info(url: str) -> dict:
    """
    Fetch video metadata using yt-dlp.

    Args:
        url: Full TikTok video URL

    Returns:
        Dictionary with video metadata

    Raises:
        RuntimeError: If yt-dlp is not installed
        VideoUnavailableError: If video is unavailable
    """
    try:
        result = subprocess.run(
            # --ignore-no-formats-error: some photo carousels make yt-dlp bail
            # out with "No video formats found!" instead of listing a lone
            # audio track. Without the flag the post never reaches the carousel
            # detection below - it dies as a hard failure while gallery-dl can
            # read it perfectly well. Measured on 7657081405653962017.
            [sys.executable, "-m", "yt_dlp", "--dump-json",
             "--ignore-no-formats-error", url],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            error_msg = result.stderr.lower()
            if any(keyword in error_msg for keyword in ["private", "unavailable", "deleted", "blocked"]):
                raise VideoUnavailableError(f"TikTok video is unavailable: {result.stderr}")
            raise RuntimeError(f"yt-dlp failed: {result.stderr}")

        return json.loads(result.stdout)

    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: pip install yt-dlp"
        )


def download_audio(url: str, output_dir: Path) -> Path:
    """
    Download audio from TikTok video.

    Args:
        url: Full TikTok video URL
        output_dir: Directory to save the audio file

    Returns:
        Path to downloaded audio file

    Raises:
        RuntimeError: If download fails
    """
    output_template = str(output_dir / "tiktok_audio.%(ext)s")

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                # Muxed file, smallest first - NOT "bestaudio". TikTok's
                # audio-only stream is the attached music track, without the
                # creator's voice-over: transcribing it returns song lyrics.
                # The real soundtrack only exists inside the video file.
                # Deliberately no "-x" either: converting to mp3 would drag in
                # ffmpeg as a system dependency, and Whisper decodes mp4
                # natively through PyAV.
                "-f", "b",
                "-S", "+size",
                "-o", output_template,
                url
            ],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to download audio: {result.stderr}")

        # Extension depends on the stream TikTok served.
        for audio_file in sorted(output_dir.glob("tiktok_audio.*")):
            if audio_file.is_file():
                return audio_file

        raise RuntimeError("Audio file not found after download")

    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: pip install yt-dlp"
        )


def is_photo_url(url: str) -> bool:
    """True for a TikTok photo carousel (slideshow) rather than a video."""
    return "/photo/" in url


def to_video_url(url: str) -> str:
    """Rewrite a /photo/ URL into the /video/ form.

    yt-dlp refuses ``/photo/`` outright ("Unsupported URL") but serves the very
    same post's metadata under ``/video/``. It still will not hand over the
    carousel images - only the audio track and a cover - which is why the
    images come from gallery-dl instead.
    """
    return url.replace("/photo/", "/video/")


def download_carousel(url: str, output_dir: Path) -> list:
    """Download the images of a photo post. Returns them in order.

    gallery-dl, not yt-dlp: yt-dlp exposes a photo post as a lone audio stream
    plus a cover thumbnail, and never the slides. Scraping the page instead is
    a dead end - it answers 200 with a shell containing no ``imagePost``, no
    ``itemStruct`` and no image URL, because the carousel only exists after the
    page's JavaScript has run.
    """
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "gallery_dl",
                "-D", str(output_dir),
                "-f", "{num:>02}.{extension}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "gallery-dl is not installed. Install it with: pip install gallery-dl"
        )

    images = sorted(p for p in output_dir.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    if not images:
        raise RuntimeError(f"No carousel image downloaded: {result.stderr[-500:]}")

    return images


def extract_photo(url: str) -> Pivot:
    """Photo carousel -> Pivot. The screen is the only channel that matters."""
    video_id = extract_video_id(to_video_url(url))
    info = fetch_video_info(to_video_url(url))

    text = ""
    lang = None
    stt_error_message = None
    stt_backend = None
    stt_device = None
    screen = ScreenText()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        images = download_carousel(url, tmpdir_path)
        screen = read_images(images, cover_dest=cover_path("tiktok", video_id))
        if screen.error:
            print(f"[screen] OCR failed for {url}: {screen.error}", file=sys.stderr)

        # The audio of a photo post is almost always the attached track, so the
        # transcript is usually song lyrics. It is transcribed anyway and handed
        # over next to `track`: deciding what is music and what is a voice-over
        # is the curator's call, not a heuristic's.
        audio = sorted(p for p in tmpdir_path.glob("*") if p.suffix.lower() in {".mp3", ".m4a"})
        if audio:
            try:
                result = transcribe(str(audio[0]))
                text = result.text
                lang = result.lang
                stt_backend = result.backend
                stt_device = result.device
            except Exception as e:
                stt_error_message = f"{type(e).__name__}: {e}"
                print(f"[stt] transcription failed for {url}: {stt_error_message}",
                      file=sys.stderr)

    published_at = None
    if "timestamp" in info:
        published_at = datetime.fromtimestamp(info["timestamp"])
    elif "upload_date" in info:
        published_at = datetime.strptime(info["upload_date"], "%Y%m%d")

    return Pivot(
        source_type="tiktok",
        source_id=video_id,
        url=url,
        title=info.get("title", ""),
        author=info.get("uploader", info.get("creator", "")),
        duration_s=info.get("duration"),
        published_at=published_at,
        fetched_at=datetime.now(),
        lang=lang,
        raw_text=text,
        description=info.get("description", "") or "",
        screen_text=screen.text,
        cover_path=screen.cover_path,
        meta={
            "post_type": "photo",
            "slides": screen.frames_sampled,
            "stt_used": bool(text),
            "stt_error": stt_error_message is not None,
            "stt_error_message": stt_error_message,
            "stt_backend": stt_backend,
            "stt_device": stt_device,
            "screen_frames_read": screen.frames_read,
            "screen_ocr_s": round(screen.ocr_s, 2),
            "screen_error": screen.error,
            # Name the soundtrack: it is what tells a curator that a fluent
            # transcript is a song and not the author speaking.
            "track": info.get("track"),
            "artist": info.get("artist"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "uploader_id": info.get("uploader_id"),
            "uploader_url": info.get("uploader_url"),
        },
    )


def extract(url: str) -> Pivot:
    """
    Main entry point: URL -> Pivot.

    Args:
        url: TikTok video URL

    Returns:
        Pivot object with video metadata and transcript

    Raises:
        ValueError: If URL is invalid
        VideoUnavailableError: If video is unavailable
        RuntimeError: If yt-dlp is not installed
    """
    # A photo carousel has no video stream at all: different download, no frame
    # sampling, and the screen is the only channel that carries anything.
    if is_photo_url(url):
        return extract_photo(url)

    # Extract video ID (for metadata purposes)
    video_id = extract_video_id(url)

    # Fetch video metadata
    info = fetch_video_info(url)

    # A photo post reached through its /video/ URL: TikTok serves it with an
    # audio stream and nothing else. Detected here rather than by the URL alone,
    # because both forms of the link are in circulation.
    formats = info.get("formats") or []
    if not formats or all(f.get("vcodec") in (None, "none") for f in formats):
        return extract_photo(url)

    # One pass, three channels. The file is downloaded once and both the audio
    # and the frames are read from it before it is thrown away - there is no
    # second visit, so nothing has to be cached and no heuristic has to guess
    # in advance whether the screen is worth looking at.
    text = ""
    lang = None
    stt_error = False
    stt_error_message = None
    stt_backend = None
    stt_device = None
    screen = ScreenText()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        media_path = None

        try:
            media_path = download_audio(url, tmpdir_path)
        except Exception as e:
            stt_error = True
            stt_error_message = f"{type(e).__name__}: {e}"
            print(f"[media] download failed for {url}: {stt_error_message}",
                  file=sys.stderr)

        if media_path is not None:
            # Channel 1 - the voice.
            try:
                result = transcribe(str(media_path))
                text = result.text
                lang = result.lang
                stt_backend = result.backend
                stt_device = result.device
            except Exception as e:
                # Keep going on the other channels, but say so. An empty
                # transcript that looks like a silent video would be curated
                # as if it were one.
                stt_error = True
                stt_error_message = f"{type(e).__name__}: {e}"
                print(f"[stt] transcription failed for {url}: {stt_error_message}",
                      file=sys.stderr)

            # Channel 3 - the screen. Always read: OCR is local and cheap, and
            # a gate here would have to guess what only the transcript reveals.
            screen = read_video(media_path, cover_dest=cover_path("tiktok", video_id))
            if screen.error:
                print(f"[screen] OCR failed for {url}: {screen.error}", file=sys.stderr)

    # Parse published date
    published_at = None
    if "timestamp" in info:
        # TikTok uses Unix timestamp
        published_at = datetime.fromtimestamp(info["timestamp"])
    elif "upload_date" in info:
        # Fallback to upload_date format (YYYYMMDD)
        date_str = info["upload_date"]
        published_at = datetime.strptime(date_str, "%Y%m%d")

    # Build Pivot
    return Pivot(
        source_type="tiktok",
        source_id=video_id,
        url=url,
        title=info.get("title", ""),
        author=info.get("uploader", info.get("creator", "")),  # Username with @
        duration_s=info.get("duration"),
        published_at=published_at,
        fetched_at=datetime.now(),
        lang=lang,
        raw_text=text,
        description=info.get("description", "") or "",
        screen_text=screen.text,
        cover_path=screen.cover_path,
        meta={
            "stt_used": True,
            "stt_error": stt_error,
            "stt_error_message": stt_error_message,
            "stt_backend": stt_backend,
            "stt_device": stt_device,
            "screen_frames_read": screen.frames_read,
            "screen_ocr_s": round(screen.ocr_s, 2),
            "screen_error": screen.error,
            "track": info.get("track"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
            "description": info.get("description", ""),
            "uploader_id": info.get("uploader_id"),
            "uploader_url": info.get("uploader_url"),
        }
    )


def handle_tiktok(url: str) -> Pivot:
    """
    Extract content from TikTok video.

    Args:
        url: TikTok video URL

    Returns:
        Pivot object with extracted content

    Raises:
        ValueError: If URL is invalid
        VideoUnavailableError: If video is unavailable
        RuntimeError: If yt-dlp is not installed
    """
    return extract(url)
