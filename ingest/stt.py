"""Speech-to-text transcription via Groq Whisper API."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-large-v3"
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB
SUPPORTED_FORMATS = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".ogg", ".flac"}


@dataclass
class TranscriptResult:
    text: str
    lang: str  # Detected or provided language
    duration_s: Optional[float]  # If available from API


def transcribe(audio_path: str, lang: str = None) -> TranscriptResult:
    """
    Transcribe audio file using Groq Whisper.

    Args:
        audio_path: Path to audio file (mp3, m4a, wav, etc.)
        lang: Optional language hint (ISO 639-1, e.g. "fr", "en")

    Returns:
        TranscriptResult with transcribed text

    Raises:
        FileNotFoundError: Audio file doesn't exist
        ValueError: File too large (>25MB) or unsupported format
        RuntimeError: API error (missing key, rate limit, etc.)
    """
    # Validate file exists
    file_path = Path(audio_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Validate file size
    file_size = file_path.stat().st_size
    if file_size > MAX_FILE_SIZE:
        raise ValueError(f"File too large for Groq API (max 25MB): {file_size / (1024*1024):.1f}MB")

    # Validate file format
    if file_path.suffix.lower() not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported audio format: {file_path.suffix}. "
            f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
        )

    # Get API key
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    # Prepare request
    headers = {"Authorization": f"Bearer {api_key}"}

    data = {
        "model": WHISPER_MODEL,
        "response_format": "verbose_json",
    }

    # Add language if provided
    if lang:
        data["language"] = lang

    # Call API
    try:
        with open(file_path, "rb") as audio_file:
            files = {"file": (file_path.name, audio_file, "audio/mpeg")}

            response = requests.post(
                GROQ_API_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=60,
            )

        # Handle HTTP errors
        if response.status_code == 401:
            raise RuntimeError("Groq API authentication failed (invalid GROQ_API_KEY)")
        elif response.status_code == 429:
            raise RuntimeError("Groq API rate limit exceeded")
        elif response.status_code >= 500:
            raise RuntimeError(f"Groq API server error: {response.status_code}")
        elif response.status_code != 200:
            error_msg = response.json().get("error", {}).get("message", response.text)
            raise RuntimeError(f"Groq API error ({response.status_code}): {error_msg}")

        # Parse response
        result = response.json()

        return TranscriptResult(
            text=result.get("text", ""),
            lang=result.get("language", lang or "unknown"),
            duration_s=result.get("duration"),
        )

    except requests.exceptions.Timeout:
        raise RuntimeError("Groq API request timed out (>60s)")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Groq API request failed: {e}")
