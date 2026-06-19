"""Main dispatcher for ingestion pipeline."""

from typing import List
from .pivot import Pivot


def dispatch(urls: List[str]) -> List[Pivot]:
    """
    Main entry point for ingestion.

    Args:
        urls: List of URLs to ingest

    Returns:
        List of Pivot objects
    """
    raise NotImplementedError("TODO: Implement dispatch logic")
