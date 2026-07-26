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

    Args:
        url: TikTok URL

    Returns:
        Video ID (numeric ID or short code)

    Raises:
        ValueError: If URL is not a valid TikTok URL
    """
    patterns = [
        r'tiktok\.com/@[^/]+/video/(\d+)',  # Full URL with username
        r'vm\.tiktok\.com/([a-zA-Z0-9]+)',  # Short URL
        r'tiktok\.com/t/([a-zA-Z0-9]+)',    # Short URL alternative
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
            [sys.executable, "-m", "yt_dlp", "--dump-json", url],
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
                "-x",  # Extract audio
                "--audio-format", "mp3",
                "--audio-quality", "5",  # Medium quality
                "-o", output_template,
                url
            ],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to download audio: {result.stderr}")

        # Find the downloaded file
        audio_file = output_dir / "tiktok_audio.mp3"
        if audio_file.exists():
            return audio_file

        raise RuntimeError("Audio file not found after download")

    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: pip install yt-dlp"
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
    # Extract video ID (for metadata purposes)
    video_id = extract_video_id(url)

    # Fetch video metadata
    info = fetch_video_info(url)

    # TikTok videos don't have subtitles, so always use STT
    text = ""
    lang = None
    stt_error = False

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        try:
            audio_path = download_audio(url, tmpdir_path)
            result = transcribe(str(audio_path))
            text = result.text
            lang = result.lang
        except Exception as e:
            # STT failed, continue without transcript
            stt_error = True
            text = ""
            lang = None

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
        meta={
            "stt_used": True,
            "stt_error": stt_error,
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
