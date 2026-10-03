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

## Configuration

Copier `.env.example` en `.env` à la racine du dépôt, puis renseigner :

- `BRAIN_PATH` : chemin du dépôt The Brain, où les captures sont écrites.
- `STATE_PATH` : dossier d'état de l'ingestion (`processed/`, `pivots/`, `covers/`), hors du dépôt. **Requis** : absent, vide ou inexistant, l'ingestion s'arrête avec une erreur. Le dossier doit exister ; ses sous-dossiers sont créés au besoin.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest
```

## Spec

Voir `projects/brain-tools/SPEC.md` dans The Brain.
