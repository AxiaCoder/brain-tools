"""Routing logic: save to bookmarks and/or brain."""

from typing import Dict, Any
from .pivot import Pivot


def route(pivot: Pivot, curation_result: Dict[str, Any]) -> None:
    """
    Route content based on curation decision.

    Args:
        pivot: Pivot object with content
        curation_result: Output from curate()
    """
    raise NotImplementedError("TODO: Implement routing logic")
