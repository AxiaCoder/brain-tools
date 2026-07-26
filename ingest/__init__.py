"""Ingest module for brain-tools."""

from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root so config (BRAIN_PATH, GROQ_API_KEY, AUTO_ROUTE)
# is available regardless of the current working directory.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
