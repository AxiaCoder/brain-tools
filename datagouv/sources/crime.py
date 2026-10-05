"""Recorded crime from the Ministère de l'Intérieur communal base (police and gendarmerie)."""

import csv
import gzip
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterator, Optional

from datagouv import net

NAME = "crime"
COMMUNE_LEVEL_ONLY = False

DATASET = "https://www.data.gouv.fr/datasets/621df2954fa5a3b5a023e23c"
CATALOG_API = "https://www.data.gouv.fr/api/1/datasets/621df2954fa5a3b5a023e23c/"
RESOURCE_TITLE_PREFIX = "COM - "
RESOURCE_FORMAT = "csv.gz"
CHANGE_YEARS = 5
DOWNLOAD_TIMEOUT_SECONDS = 120
INDEX_FILE = "crime.sqlite3"

# Indicator label in the file -> reported key.
INDICATORS = {
    "Cambriolages de logement": "burglary",
    "Vols sans violence contre des personnes": "theft_without_violence",
    "Vols violents sans arme": "violent_theft_unarmed",
    "Vols avec armes": "armed_robbery",
    "Violences physiques hors cadre familial": "assault_outside_family",
    "Destructions et dégradations volontaires": "vandalism",
}
# The ministry divides burglaries by dwellings and every other indicator by inhabitants.
RATE_BASE = {"burglary": "dwellings"}
DEFAULT_RATE_BASE = "inhabitants"


def fetch(code: str) -> dict:
    """Return, per retained indicator, the count and rate of the latest year for INSEE ``code``.

    ``rate_per_mille`` is per 1,000 of ``rate_base`` (``dwellings`` or ``inhabitants``).
    ``rate_change_points`` is the latest rate minus the rate ``CHANGE_YEARS`` earlier,
    in per-mille points, ``None`` when either is missing or masked. A ``masked`` value
    is withheld by the ministry (statistical secrecy): its count and rate are ``None``.
    The file is downloaded once into the cache directory and indexed; it is fetched
    again only when the catalog points to another file.
    """
    url = resolve_url()
    index = ensure_index(url, cache_dir())
    with closing(sqlite3.connect(index)) as db:
        year = int(db.execute("SELECT value FROM meta WHERE key = 'year'").fetchone()[0])
        rows = db.execute(
            "SELECT year, indicator, count, rate, masked FROM rows WHERE code = ?", (code,)
        ).fetchall()
    return {
        "source": DATASET,
        "file": url,
        "year": year,
        "base_year": year - CHANGE_YEARS,
        "indicators": summarize(rows, year),
    }


def resolve_url() -> str:
    """Return the URL of the current communal csv.gz file, read from the data.gouv.fr catalog.

    Raises ``LookupError`` when the catalog lists no such resource.
    """
    body = net.get_json(CATALOG_API) or {}
    for resource in body.get("resources") or []:
        if (
            resource.get("type") == "main"
            and resource.get("format") == RESOURCE_FORMAT
            and (resource.get("title") or "").startswith(RESOURCE_TITLE_PREFIX)
        ):
            return resource["url"]
    raise LookupError("no communal csv.gz resource in the crime dataset catalog")


def cache_dir() -> Path:
    """Return the cache directory: ``$DATAGOUV_CACHE``, else the platform user cache."""
    override = os.environ.get("DATAGOUV_CACHE")
    if override:
        return Path(override)
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "brain-tools" / "datagouv"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "brain-tools" / "datagouv"


def ensure_index(url: str, directory: Path) -> Path:
    """Return the index of ``url`` in ``directory``, downloading and building it if missing or stale."""
    index = directory / INDEX_FILE
    if index.exists() and _indexed_url(index) == url:
        return index
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "crime.csv.gz.part"
    staging = directory / (INDEX_FILE + ".part")
    try:
        net.download(url, str(archive), timeout=DOWNLOAD_TIMEOUT_SECONDS)
        if staging.exists():
            staging.unlink()
        build_index(archive, staging, url)
        os.replace(staging, index)
    finally:
        for leftover in (archive, staging):
            if leftover.exists():
                leftover.unlink()
    return index


def build_index(archive: Path, index: Path, url: str) -> None:
    """Index the retained indicators of the latest year and of ``CHANGE_YEARS`` before it."""
    with closing(sqlite3.connect(index)) as db:
        db.execute(
            "CREATE TABLE rows (code TEXT, year INTEGER, indicator TEXT, count INTEGER,"
            " rate REAL, masked INTEGER)"
        )
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        db.executemany(
            "INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?)",
            (
                (r["code"], r["year"], r["indicator"], r["count"], r["rate"], r["masked"])
                for r in read_rows(archive)
            ),
        )
        year = db.execute("SELECT MAX(year) FROM rows").fetchone()[0]
        if year is None:
            raise ValueError("crime file holds no retained indicator")
        db.execute("DELETE FROM rows WHERE year NOT IN (?, ?)", (year, year - CHANGE_YEARS))
        db.execute("CREATE INDEX rows_code ON rows (code)")
        db.executemany("INSERT INTO meta VALUES (?, ?)", [("url", url), ("year", str(year))])
        db.commit()
        db.execute("VACUUM")


def read_rows(archive: Path) -> Iterator[dict]:
    """Yield the parsed rows of the retained indicators from the gzipped CSV ``archive``."""
    with gzip.open(archive, "rb") as raw:
        reader = csv.DictReader(_decode(raw), delimiter=";")
        for row in reader:
            parsed = parse_row(row)
            if parsed:
                yield parsed


def parse_row(row: dict) -> Optional[dict]:
    """Map one CSV row to the indexed fields; ``None`` for an indicator not retained."""
    key = INDICATORS.get(row.get("indicateur") or "")
    if key is None:
        return None
    masked = row.get("est_diffuse") != "diff"
    count = None if masked else _number(row.get("nombre"))
    return {
        "code": row["CODGEO_2026"],
        "year": int(row["annee"]),
        "indicator": key,
        "count": None if count is None else int(count),
        "rate": None if masked else _number(row.get("taux_pour_mille")),
        "masked": masked,
    }


def summarize(rows: list[tuple], year: int) -> dict:
    """Shape the indexed ``(year, indicator, count, rate, masked)`` rows of one town per indicator."""
    by_key = {(indicator, row_year): (count, rate, masked) for row_year, indicator, count, rate, masked in rows}
    result = {}
    for key in INDICATORS.values():
        latest = by_key.get((key, year))
        if latest is None:
            continue
        count, rate, masked = latest
        base = by_key.get((key, year - CHANGE_YEARS))
        change = None
        if rate is not None and base is not None and base[1] is not None:
            change = round(rate - base[1], 2)
        result[key] = {
            "count": count,
            "rate_per_mille": None if rate is None else round(rate, 2),
            "rate_base": RATE_BASE.get(key, DEFAULT_RATE_BASE),
            "masked": bool(masked),
            "rate_change_points": change,
        }
    return result


def _decode(raw) -> Iterator[str]:
    """Decode each byte line as UTF-8, falling back to cp1252 for a line that is not."""
    for line in raw:
        try:
            yield line.decode("utf-8")
        except UnicodeDecodeError:
            yield line.decode("cp1252")


def _number(value) -> Optional[float]:
    """Parse a French decimal cell (comma separator); ``NA`` and empty are ``None``."""
    if value in (None, "", "NA"):
        return None
    return float(value.replace(",", "."))


def _indexed_url(index: Path) -> Optional[str]:
    """Return the source URL recorded in ``index``, ``None`` if it is unreadable."""
    try:
        with closing(sqlite3.connect(index)) as db:
            row = db.execute("SELECT value FROM meta WHERE key = 'url'").fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None
