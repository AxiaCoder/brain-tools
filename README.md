# Brain Tools

Boîte à outils pour The Brain.

## Outils

- **ingest** : Routeur d'ingestion de liens (vidéos → bookmarks + brain)

## Installation

```bash
pip install -e .
```

### Dependencies

- Python 3.11+
- `yt-dlp` (pour YouTube)
- `groq` (pour LLM processing)

## Tests

```bash
pip install -e ".[dev]"
python -m pytest
```

## Spec

Voir `projects/brain-tools/SPEC.md` dans The Brain.
