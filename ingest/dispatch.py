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

State tracking (see ingest/state.py):
    - One record per link, <STATE_PATH>/processed/{source_type}_{source_id}.json
    - Marked ``extracted`` here; only routing marks it ``done``. An unfinished
      link is therefore re-processed rather than skipped for good.
    - Errors are recorded and skipped, unless retry_errors=True
    - Use skip_if_processed=False to force reprocessing
"""

from typing import Optional

from .pivot import Pivot
from .handlers import youtube, tiktok
from . import state


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

    # TikTok. "tiktokv.com" is not a suffix of "tiktok.com" - the v sits in
    # between - so it needs its own entry. Every link in the TikTok data export
    # uses that domain, which is why none of them were recognised before.
    if any(domain in url_lower for domain in ["tiktok.com", "vm.tiktok.com", "tiktokv.com"]):
        return "tiktok"

    return "unknown"


def extract_source_id(url: str, source_type: str) -> str:
    """Extract the source ID from URL based on type."""
    if source_type == "youtube":
        return youtube.extract_video_id(url)
    elif source_type == "tiktok":
        return tiktok.extract_video_id(url)
    else:
        raise ValueError(f"Unknown source type: {source_type}")


def dispatch(url: str, skip_if_processed: bool = True,
             retry_errors: bool = False) -> Optional[Pivot]:
    """
    Main entry point: URL -> Pivot (or None if already handled).

    Args:
        url: URL to process
        skip_if_processed: If True, skip links already routed to their outputs
        retry_errors: If True, replay links whose extraction previously failed

    Returns:
        Pivot object, or None if skipped

    Raises:
        StatePathError: STATE_PATH is not configured - raised before any download
        ValueError: Unknown URL type
        Various handler errors
    """
    state.state_root()
    url = url.strip()
    source_type = detect_source_type(url)
    if source_type == "unknown":
        raise ValueError(f"Unknown URL type: {url}")

    # Extract ID for idempotence check
    source_id = extract_source_id(url, source_type)

    # Check idempotence. A link left in ``extracted`` is not skipped: it was
    # downloaded and transcribed but never routed anywhere, and the media was
    # thrown away, so resuming it means doing the pass again.
    if skip_if_processed and state.should_skip(source_type, source_id, retry_errors):
        return None

    # Route to handler
    try:
        if source_type == "youtube":
            pivot = youtube.extract(url)
        elif source_type == "tiktok":
            pivot = tiktok.extract(url)
        else:
            raise ValueError(f"No handler for: {source_type}")

        # Extracted, not finished. route() has not run yet; whoever routes is
        # responsible for calling state.mark_done with the real destinations.
        state.mark_extracted(source_type, source_id, url,
                             title=pivot.title, author=pivot.author)
        return pivot

    except Exception as e:
        # Mark as error but don't crash the batch
        state.mark_error(source_type, source_id, url, f"{type(e).__name__}: {e}")
        raise


def dispatch_batch(urls: list[str], skip_if_processed: bool = True,
                   retry_errors: bool = False) -> list[tuple[str, Optional[Pivot], Optional[str]]]:
    """
    Process multiple URLs.

    Returns:
        List of (url, pivot_or_none, error_or_none)

    Raises:
        StatePathError: STATE_PATH is not configured - stops the whole batch
    """
    results = []
    for url in urls:
        try:
            pivot = dispatch(url, skip_if_processed, retry_errors)
            results.append((url, pivot, None))
        except state.StatePathError:
            raise
        except Exception as e:
            results.append((url, None, str(e)))
    return results
