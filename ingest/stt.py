"""Speech-to-text using Groq Whisper API."""

from pathlib import Path


def transcribe(audio_path: Path) -> str:
    """
    Transcribe audio file using Groq Whisper.

    Args:
        audio_path: Path to audio file

    Returns:
        Transcribed text
    """
    raise NotImplementedError("TODO: Implement Groq Whisper integration")
