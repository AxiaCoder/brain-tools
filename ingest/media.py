"""Where the one file that outlives a run is kept.

Deliberately not a media cache. The ingest pipeline runs in a single pass and
throws its downloads away; the only thing worth keeping is the cover image, so
that a human (or the curating model) can look at a title card with its own eyes
when OCR mangles it - stylised, curved lettering defeats it every time.

One JPEG per post, a few hundred kilobytes, under ``<STATE_PATH>/covers``.
"""

from pathlib import Path

from .state import covers_dir


def cover_path(source_type: str, source_id: str) -> Path:
    """Destination for this post's cover image."""
    return covers_dir() / f"{source_type}_{source_id}.jpg"
