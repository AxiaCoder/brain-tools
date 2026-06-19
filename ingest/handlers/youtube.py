"""YouTube video handler."""

import re
import subprocess
import json
import tempfile
import os
from datetime import datetime
from typing import Optional
from pathlib import Path

from ..pivot import Pivot
from ..stt import transcribe, TranscriptResult


class VideoUnavailableError(Exception):
    """Video is private, deleted, or geo-blocked."""
    pass


def extract_video_id(url: str) -> str:
    """
    Extract YouTube video ID from URL.

    Supports:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - https://www.youtube.com/v/VIDEO_ID

    Args:
        url: YouTube URL

    Returns:
        Video ID (e.g., "dQw4w9WgXcQ")

    Raises:
        ValueError: If URL is not a valid YouTube URL
    """
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})',
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    raise ValueError(f"Invalid YouTube URL: {url}")


def fetch_video_info(video_id: str) -> dict:
    """
    Fetch video metadata using yt-dlp.

    Args:
        video_id: YouTube video ID

    Returns:
        Dictionary with video metadata

    Raises:
        RuntimeError: If yt-dlp is not installed
        VideoUnavailableError: If video is unavailable
    """
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            error_msg = result.stderr.lower()
            if any(keyword in error_msg for keyword in ["private", "unavailable", "deleted", "blocked"]):
                raise VideoUnavailableError(f"Video {video_id} is unavailable: {result.stderr}")
            raise RuntimeError(f"yt-dlp failed: {result.stderr}")

        return json.loads(result.stdout)

    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: pip install yt-dlp"
        )


def fetch_subtitles(video_id: str, langs: list[str] = ["fr", "en"]) -> tuple[str, Optional[str]]:
    """
    Fetch and parse subtitles.

    Priority:
    1. Manual subtitles in requested languages
    2. Auto-generated subtitles in requested languages

    Args:
        video_id: YouTube video ID
        langs: List of language codes to try (in order of preference)

    Returns:
        Tuple of (plain_text, lang_code) or ("", None) if no subtitles found

    Raises:
        RuntimeError: If yt-dlp is not installed
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Try to download subtitles
        lang_string = ",".join(langs)
        try:
            subprocess.run(
                [
                    "yt-dlp",
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs", lang_string,
                    "--skip-download",
                    "--sub-format", "vtt",
                    "-o", str(tmpdir_path / video_id),
                    f"https://www.youtube.com/watch?v={video_id}"
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False  # Don't raise on non-zero exit (subtitles might not exist)
            )
        except FileNotFoundError:
            raise RuntimeError(
                "yt-dlp is not installed. Install it with: pip install yt-dlp"
            )

        # Look for downloaded subtitle files
        # Priority: manual subs first, then auto-generated
        for lang in langs:
            # Try manual subtitles first
            manual_file = tmpdir_path / f"{video_id}.{lang}.vtt"
            if manual_file.exists():
                text = parse_vtt(manual_file.read_text())
                return (text, lang)

            # Try auto-generated
            auto_file = tmpdir_path / f"{video_id}.{lang}-orig.vtt"
            if auto_file.exists():
                text = parse_vtt(auto_file.read_text())
                return (text, lang)

        # No subtitles found
        return ("", None)


def download_audio(video_id: str, output_dir: Path) -> Path:
    """
    Download audio from YouTube video.

    Args:
        video_id: YouTube video ID
        output_dir: Directory to save the audio file

    Returns:
        Path to downloaded audio file

    Raises:
        RuntimeError: If download fails
    """
    output_template = str(output_dir / f"{video_id}.%(ext)s")

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "-x",  # Extract audio
                "--audio-format", "mp3",
                "--audio-quality", "5",  # Medium quality (smaller file)
                "-o", output_template,
                f"https://www.youtube.com/watch?v={video_id}"
            ],
            capture_output=True,
            text=True,
            timeout=120  # Audio download can take longer
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to download audio: {result.stderr}")

        # Find the downloaded file
        audio_file = output_dir / f"{video_id}.mp3"
        if audio_file.exists():
            return audio_file

        raise RuntimeError(f"Audio file not found after download")

    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: pip install yt-dlp"
        )


def parse_vtt(content: str) -> str:
    """
    Strip VTT formatting, return plain text.

    Removes:
    - WEBVTT header
    - Timestamps
    - Cue identifiers
    - HTML tags
    - Duplicate lines

    Args:
        content: Raw VTT file content

    Returns:
        Clean plain text
    """
    lines = []
    for line in content.split("\n"):
        line = line.strip()

        # Skip VTT header
        if line.startswith("WEBVTT"):
            continue

        # Skip timestamps (format: 00:00:00.000 --> 00:00:00.000)
        if "-->" in line:
            continue

        # Skip cue identifiers (numeric IDs)
        if line.isdigit():
            continue

        # Skip empty lines
        if not line:
            continue

        # Remove HTML tags
        line = re.sub(r"<[^>]+>", "", line)

        # Skip if duplicate of previous line (common in subtitles)
        if lines and line == lines[-1]:
            continue

        lines.append(line)

    return "\n".join(lines)


def extract(url: str, use_stt_fallback: bool = True) -> Pivot:
    """
    Main entry point: URL -> Pivot.

    Args:
        url: YouTube video URL
        use_stt_fallback: If True, use STT when subtitles unavailable

    Returns:
        Pivot object with video metadata and subtitles/transcript

    Raises:
        ValueError: If URL is invalid
        VideoUnavailableError: If video is unavailable
        RuntimeError: If yt-dlp is not installed or STT fails
    """
    # Extract video ID
    video_id = extract_video_id(url)

    # Fetch video metadata
    info = fetch_video_info(video_id)

    # Fetch subtitles
    text, lang = fetch_subtitles(video_id)

    # Fallback to STT if no subtitles
    stt_used = False
    if not text and use_stt_fallback:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            try:
                audio_path = download_audio(video_id, tmpdir_path)
                result = transcribe(str(audio_path))
                text = result.text
                lang = result.lang
                stt_used = True
            except Exception as e:
                # STT failed, continue without transcript
                text = ""
                lang = None

    # Parse published date
    published_at = None
    if "upload_date" in info:
        # Format: YYYYMMDD
        date_str = info["upload_date"]
        published_at = datetime.strptime(date_str, "%Y%m%d")

    # Build Pivot
    return Pivot(
        source_type="youtube",
        source_id=video_id,
        url=url,
        title=info.get("title", ""),
        author=info.get("channel", info.get("uploader", "")),
        duration_s=info.get("duration"),
        published_at=published_at,
        fetched_at=datetime.now(),
        lang=lang,
        raw_text=text,
        meta={
            "subtitles_available": bool(text) and not stt_used,
            "stt_used": stt_used,
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "channel_id": info.get("channel_id"),
            "description": info.get("description", ""),
            "tags": info.get("tags", []),
        }
    )


def handle_youtube(url: str) -> Pivot:
    """
    Extract content from YouTube video.

    Args:
        url: YouTube video URL

    Returns:
        Pivot object with extracted content

    Raises:
        ValueError: If URL is invalid
        VideoUnavailableError: If video is unavailable
        RuntimeError: If yt-dlp is not installed
    """
    return extract(url)
