"""Curation result dataclass."""

from dataclasses import dataclass
from typing import Optional


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
        )


# Valid categories (for validation)
VALID_CATEGORIES = {
    # Domains
    "health", "finance", "career", "learning", "dating", "social",
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

    return errors
