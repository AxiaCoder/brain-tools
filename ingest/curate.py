"""Curation step: decide what to keep and how to route."""

from typing import Dict, Any
from .pivot import Pivot


def curate(pivot: Pivot) -> Dict[str, Any]:
    """
    Analyze content and decide routing.

    Args:
        pivot: Pivot object with raw content

    Returns:
        Dict with keys:
            - action: "keep_link" | "extract_knowledge" | "discard"
            - category: Optional[str] (e.g., "tech", "philosophy")
            - notes: Optional[str] (curator notes)
    """
    raise NotImplementedError("TODO: Implement curation logic")
