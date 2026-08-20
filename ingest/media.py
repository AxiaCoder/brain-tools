"""Where the one file that outlives a run is kept.

Deliberately not a media cache. The ingest pipeline runs in a single pass and
throws its downloads away; the only thing worth keeping is the cover image, so
that a human (or the curating model) can look at a title card with its own eyes
when OCR mangles it - stylised, curved lettering defeats it every time.

One JPEG per post, a few hundred kilobytes, gitignored.
"""

from pathlib import Path


COVERS_DIR = Path(__file__).parent.parent / "state" / "covers"


def cover_path(source_type: str, source_id: str) -> Path:
    """Destination for this post's cover image."""
    return COVERS_DIR / f"{source_type}_{source_id}.jpg"
