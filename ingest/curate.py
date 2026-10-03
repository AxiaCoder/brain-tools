"""Curation result dataclass."""

from dataclasses import dataclass
from typing import Optional


# Apps that can receive structured content instead of a markdown note.
# The tool never calls them: it carries the payload and Claude Code makes the
# MCP call, exactly like bookmarks.
VALID_APP_TARGETS = {"kitchen"}


@dataclass
class CurationResult:
    """Result of content curation."""

    source_id: str
    category: str  # health, finance, career, learning, dating, social, dev, gaming, smarthome, culture, other
    keep_link: bool
    extract_knowledge: bool
    pitch: str  # One-line description
    tags: list[str]
    summary_md: Optional[str]  # Markdown summary, only if extract_knowledge=True
    # Third destination, exclusive with extract_knowledge: content that is
    # structured data for an existing app goes to the app, not to a note.
    app_target: Optional[str] = None  # e.g. "kitchen"
    app_payload: Optional[dict] = None  # arguments for that app's MCP tool

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "source_id": self.source_id,
            "category": self.category,
            "keep_link": self.keep_link,
            "extract_knowledge": self.extract_knowledge,
            "pitch": self.pitch,
            "tags": self.tags,
            "summary_md": self.summary_md,
            "app_target": self.app_target,
            "app_payload": self.app_payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CurationResult":
        """Create from dictionary."""
        return cls(
            source_id=data["source_id"],
            category=data["category"],
            keep_link=data["keep_link"],
            extract_knowledge=data["extract_knowledge"],
            pitch=data["pitch"],
            tags=data.get("tags", []),
            summary_md=data.get("summary_md"),
            app_target=data.get("app_target"),
            app_payload=data.get("app_payload"),
        )


# Valid categories (for validation)
VALID_CATEGORIES = {
    # Domains
    "health", "finance", "career", "learning", "dating", "social", "ecriture",
    # Resources
    "dev", "gaming", "smarthome", "culture", "other"
}


def validate_curation(result: CurationResult) -> list[str]:
    """
    Validate a curation result.

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    if result.category not in VALID_CATEGORIES:
        errors.append(f"Invalid category: {result.category}")

    if result.extract_knowledge and not result.summary_md:
        errors.append("summary_md required when extract_knowledge=True")

    if not result.pitch:
        errors.append("pitch is required")

    if result.app_target is not None:
        if result.app_target not in VALID_APP_TARGETS:
            errors.append(f"Invalid app_target: {result.app_target}")

        if not result.app_payload:
            errors.append("app_payload required when app_target is set")

        # The whole point of routing to an app is that the app owns the
        # content. Writing a note as well would fork it into two copies that
        # drift apart.
        if result.extract_knowledge:
            errors.append(
                "extract_knowledge must be False when app_target is set: "
                "the app owns the content, a note would duplicate it"
            )

    elif result.app_payload:
        errors.append("app_payload set without app_target")

    return errors
