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
    raw_text: str  # Channel 1 - the voice: transcript or subtitles
    meta: dict  # Platform-specific metadata

    # Three channels, not one. Any of them can carry the whole content and any
    # of them can be empty: a recipe puts its quantities in the description, a
    # silent carousel puts everything on screen and leaves the audio to a song.
    # Merging them is the curator's job - the pivot only has to hand them over.
    description: str = ""  # Channel 2 - what the author wrote under the post
    screen_text: str = ""  # Channel 3 - text burned into the images or frames
    cover_path: Optional[str] = None  # First image, kept so it can be eyeballed
