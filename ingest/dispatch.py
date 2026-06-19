"""
URL dispatcher with idempotence.

Main entry point for ingestion pipeline. Routes URLs to appropriate handlers
and tracks processed URLs to avoid duplicate work.

Usage:
    from ingest.dispatch import dispatch, dispatch_batch

    # Single URL
    pivot = dispatch("https://youtube.com/watch?v=...")
    if pivot is None:
        print("Already processed")

    # Batch processing
    urls = ["url1", "url2", "url3"]
    results = dispatch_batch(urls)
    for url, pivot, error in results:
        if error:
            print(f"Error: {error}")
        elif pivot is None:
            print("Skipped (already processed)")
        else:
            print(f"Success: {pivot.title}")

State tracking:
    - Processed URLs are tracked in state/processed/{source_type}_{source_id}.json
    - Both successful and failed URLs are tracked to avoid retrying errors
    - Use skip_if_processed=False to force reprocessing
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .pivot import Pivot
from .handlers import youtube, tiktok


# State directory for idempotence
STATE_DIR = Path(__file__).parent.parent / "state" / "processed"


def detect_source_type(url: str) -> str:
    """
    Detect source type from URL.

    Returns:
        "youtube", "tiktok", or "unknown"
    """
    url_lower = url.lower()

    # YouTube
    if any(domain in url_lower for domain in ["youtube.com", "youtu.be"]):
        return "youtube"

    # TikTok
    if any(domain in url_lower for domain in ["tiktok.com", "vm.tiktok.com"]):
        return "tiktok"

    return "unknown"


def is_processed(source_type: str, source_id: str) -> bool:
    """Check if this source has already been processed."""
    state_file = STATE_DIR / f"{source_type}_{source_id}.json"
    return state_file.exists()


def mark_processed(source_type: str, source_id: str, status: str = "ok", error: str = None):
    """Mark a source as processed."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / f"{source_type}_{source_id}.json"
    state = {
        "processed_at": datetime.now().isoformat(),
        "status": status,
    }
    if error:
        state["error"] = error
    state_file.write_text(json.dumps(state, indent=2))


def extract_source_id(url: str, source_type: str) -> str:
    """Extract the source ID from URL based on type."""
    if source_type == "youtube":
        return youtube.extract_video_id(url)
    elif source_type == "tiktok":
        return tiktok.extract_video_id(url)
    else:
        raise ValueError(f"Unknown source type: {source_type}")


def dispatch(url: str, skip_if_processed: bool = True) -> Optional[Pivot]:
    """
    Main entry point: URL -> Pivot (or None if already processed).

    Args:
        url: URL to process
        skip_if_processed: If True, skip already-processed URLs

    Returns:
        Pivot object, or None if skipped

    Raises:
        ValueError: Unknown URL type
        Various handler errors
    """
    # Detect type
    source_type = detect_source_type(url)
    if source_type == "unknown":
        raise ValueError(f"Unknown URL type: {url}")

    # Extract ID for idempotence check
    source_id = extract_source_id(url, source_type)

    # Check idempotence
    if skip_if_processed and is_processed(source_type, source_id):
        return None

    # Route to handler
    try:
        if source_type == "youtube":
            pivot = youtube.extract(url)
        elif source_type == "tiktok":
            pivot = tiktok.extract(url)
        else:
            raise ValueError(f"No handler for: {source_type}")

        # Mark as processed
        mark_processed(source_type, source_id, status="ok")
        return pivot

    except Exception as e:
        # Mark as error but don't crash the batch
        mark_processed(source_type, source_id, status="error", error=str(e))
        raise


def dispatch_batch(urls: list[str], skip_if_processed: bool = True) -> list[tuple[str, Optional[Pivot], Optional[str]]]:
    """
    Process multiple URLs.

    Returns:
        List of (url, pivot_or_none, error_or_none)
    """
    results = []
    for url in urls:
        try:
            pivot = dispatch(url, skip_if_processed)
            results.append((url, pivot, None))
        except Exception as e:
            results.append((url, None, str(e)))
    return results
