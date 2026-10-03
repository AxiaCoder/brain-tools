"""Route curation results to destinations."""

import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from .pivot import Pivot
from .curate import CurationResult, validate_curation


# Category to domain/resource mapping
CATEGORY_PATHS = {
    # Domains
    "health": "domains/health/captures",
    "finance": "domains/finance/captures",
    "career": "domains/career/captures",
    "learning": "domains/learning/captures",
    "ecriture": "domains/ecriture/captures",
    "dating": "domains/dating/captures",
    "social": "domains/social/captures",
    # Resources
    "dev": "resources/dev/captures",
    "gaming": "resources/gaming/captures",
    "smarthome": "resources/smarthome/captures",
    "culture": "resources/culture/captures",
    "other": "inbox",
}


def get_brain_path() -> Path:
    """Get the brain path from environment or default."""
    brain_path = os.environ.get("BRAIN_PATH")
    if brain_path:
        return Path(brain_path)

    # Default paths to try
    defaults = [
        Path.home() / "Documents" / "the-brain",
        Path.home() / "Documents" / "brain",
    ]
    for default in defaults:
        if default.exists():
            return default

    raise RuntimeError(
        "BRAIN_PATH not set and no default brain found. "
        "Set BRAIN_PATH or create ~/Documents/the-brain"
    )


def is_auto_route_enabled() -> bool:
    """Check if captures go straight to their domain instead of inbox/<category>/."""
    return os.environ.get("AUTO_ROUTE", "false").lower() == "true"


def generate_markdown(pivot: Pivot, curation: CurationResult) -> str:
    """
    Generate markdown file content for brain.

    Uses the format defined in the ingest skill.
    """
    # Frontmatter
    # A consolidated capture is built from several videos. Declaring only the
    # carrier's url would leave the other sources findable in the state directory
    # and nowhere else - the note would claim one origin and have several.
    extra = list((pivot.meta or {}).get("extra_urls") or [])

    lines = [
        "---",
        f"source: {pivot.source_type}",
        f"url: {pivot.url}",
        f"proposed_domain: {curation.category}",
        f"tags: {curation.tags}",
        f"status: pending",
        f"ingested_at: {datetime.now().strftime('%Y-%m-%d')}",
    ]
    if extra:
        lines.append(f"sources: {len(extra) + 1}")
        lines.append("source_urls:")
        for one in [pivot.url, *extra]:
            lines.append(f"  - {one}")
    lines += [
        "---",
        "",
        f"# {pivot.title}",
        "",
        f"> {curation.pitch}",
        "",
    ]

    # Summary content
    if curation.summary_md:
        lines.append(curation.summary_md)
        lines.append("")

    # Source info
    lines.extend([
        "## Source",
        "",
        f"- **Auteur** : {pivot.author}",
    ])

    if pivot.duration_s:
        lines.append(f"- **Duree** : {pivot.duration_s}s")

    lines.append(f"- **Lien** : {pivot.url}")

    return "\n".join(lines)


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug. Diacritics are dropped (société -> societe) and œ/æ become oe/ae; other non-ASCII letters (ß, ø, ł) become hyphens (Straße -> stra-e)."""
    text = text.lower().replace("œ", "oe").replace("æ", "ae")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Replace spaces and special chars with hyphens
    text = re.sub(r'[^a-z0-9]+', '-', text)
    # Remove leading/trailing hyphens
    text = text.strip('-')
    # Limit length
    return text[:50]


def _update_index(dest_dir: Path, category: str, pivot: Pivot, filename: str, date_str: str) -> None:
    """Create or append to the folder's INDEX.md (auto-maintained capture index)."""
    index_file = dest_dir / "INDEX.md"
    title = pivot.title.replace("|", "/")
    row = f"| {date_str} | {title} | {pivot.source_type} | [{filename}](./{filename}) |"

    if index_file.exists():
        content = index_file.read_text(encoding="utf-8")
        content = re.sub(r"^updated:.*$", f"updated: {date_str}", content, count=1, flags=re.MULTILINE)
        if not content.endswith("\n"):
            content += "\n"
        content += row + "\n"
    else:
        content = "\n".join([
            "---",
            f"scope: Index des captures ingérées ({category}) — auto-maintenu par brain-tools",
            f'load_when: "capture/vidéo/résumé sur {category}, /ingest"',
            f"updated: {date_str}",
            "---",
            "",
            f"# Captures — {category}",
            "",
            "| Date | Titre | Source | Fichier |",
            "|------|-------|--------|---------|",
            row,
            "",
        ])
    index_file.write_text(content, encoding="utf-8")


def _require_valid(curation: CurationResult) -> None:
    """Raise ValueError listing every rule the curation breaks; return if it breaks none."""
    errors = validate_curation(curation)
    if errors:
        raise ValueError("Invalid curation: " + "; ".join(errors))


def route_to_brain(pivot: Pivot, curation: CurationResult) -> Optional[Path]:
    """
    Route content to the brain (markdown file).

    Returns:
        Path to created file, or None if extract_knowledge=False

    Raises:
        ValueError: the curation fails validate_curation - nothing is written
    """
    _require_valid(curation)
    if not curation.extract_knowledge:
        return None

    brain_path = get_brain_path()

    # Determine destination. Captures land in inbox/<category>/ and leave only
    # by a human filing pass; the subfolders are the structure, not a scaffold.
    # AUTO_ROUTE stays false on purpose - an inbox is what makes the unfiled
    # backlog visible, and filing means merging captures into a domain, not
    # moving them.
    if is_auto_route_enabled():
        rel_path = CATEGORY_PATHS[curation.category]
    else:
        rel_path = f"inbox/{curation.category}"

    dest_dir = brain_path / rel_path
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = slugify(pivot.title)
    filename = f"{date_str}-{slug}.md"

    # Write file
    dest_file = dest_dir / filename
    content = generate_markdown(pivot, curation)
    dest_file.write_text(content, encoding="utf-8")

    # Maintain a per-folder capture index (only for final destinations,
    # not the inbox).
    if is_auto_route_enabled():
        _update_index(dest_dir, curation.category, pivot, filename, date_str)

    return dest_file


def route_to_bookmarks(pivot: Pivot, curation: CurationResult) -> bool:
    """
    Route content to bookmarks (MCP smart-home).

    Returns:
        True if successful, False otherwise

    Note: MCP integration is a stub for now - will be called by Claude Code
    """
    if not curation.keep_link:
        return False

    # TODO: This will be called via MCP by Claude Code
    # For now, just return True to indicate it should be bookmarked
    # The actual MCP call will be: mcp__smart-home__bookmarks_create_link

    print(f"[BOOKMARK] Would create bookmark for: {pivot.url}")
    print(f"  Title: {pivot.title}")
    print(f"  Pitch: {curation.pitch}")
    print(f"  Tags: {curation.tags}")

    return True


def route_to_app(pivot: Pivot, curation: CurationResult) -> Optional[dict]:
    """
    Route content to an app (MCP smart-home) instead of a markdown note.

    Returns:
        {"target": str, "payload": dict} for Claude Code to call, or None.

    Note: same contract as route_to_bookmarks - the tool never reaches the MCP
    itself, it hands back what should be created. Claude Code makes the call.

    Raises:
        ValueError: the curation fails validate_curation
    """
    _require_valid(curation)
    if not curation.app_target:
        return None

    payload = dict(curation.app_payload or {})
    # The source link is what makes the entry traceable back to the video.
    payload.setdefault("sourceUrl", pivot.url)

    print(f"[APP:{curation.app_target}] Would create entry from: {pivot.url}")
    print(f"  Payload keys: {sorted(payload)}")

    return {"target": curation.app_target, "payload": payload}


def route(pivot: Pivot, curation: CurationResult) -> dict:
    """
    Route content to all destinations based on curation.

    The bookmark is independent of the other two; the note and the app entry
    exclude each other. A recipe can go to the kitchen app and be bookmarked,
    never also become a note.

    Returns:
        Dict with results:
        {"brain_path": Path|None, "bookmarked": bool, "app": dict|None}

    Raises:
        ValueError: the curation fails validate_curation - no destination is touched
    """
    _require_valid(curation)
    results = {
        "brain_path": None,
        "bookmarked": False,
        "app": None,
    }

    # Route to brain
    if curation.extract_knowledge:
        results["brain_path"] = route_to_brain(pivot, curation)

    # Route to bookmarks
    if curation.keep_link:
        results["bookmarked"] = route_to_bookmarks(pivot, curation)

    # Route to an app (kitchen, ...)
    if curation.app_target:
        results["app"] = route_to_app(pivot, curation)

    return results
