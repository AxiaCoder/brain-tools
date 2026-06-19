"""Pivot format dataclass for ingestion pipeline."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Pivot:
    """Unified intermediate format for all content sources."""

    source_type: str  # "youtube", "tiktok", etc.
    source_id: str  # Unique ID from platform
    url: str  # Original URL
    title: str
    author: str
    duration_s: Optional[int]  # Duration in seconds (None for non-video)
    published_at: Optional[datetime]
    fetched_at: datetime
    lang: Optional[str]  # ISO 639-1 code (e.g., "fr", "en")
    raw_text: str  # Transcription or extracted text
    meta: dict  # Platform-specific metadata
