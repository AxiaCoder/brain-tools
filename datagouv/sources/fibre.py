"""Fibre (FTTH) coverage from the ANCT « Indicateur France Très Haut Débit »."""

from typing import Optional

from datagouv import net

NAME = "fibre"
COMMUNE_LEVEL_ONLY = True

DATASET = "ANCT Indicateur France Très Haut Débit"
TABULAR_API = "https://tabular-api.data.gouv.fr/api/resources/35dc0362-960f-4353-8a9b-0cdcf5a1a270/data/"


def fetch(code: str) -> dict:
    """Return the FTTH-ready premises of INSEE ``code`` against all its premises.

    ``ftth_share`` is a percentage rounded to 0.1, ``None`` when the town is not
    listed or has no premises; ``quarter`` is the dataset quarter, e.g. ``2026 T2``.
    """
    body = net.get_json(TABULAR_API, {"insee_com__exact": code, "page_size": 1})
    rows = (body or {}).get("data") or []
    row = rows[0] if rows else {}
    premises = _count(row.get("locaux_arcep"))
    ftth = _count(row.get("locaux_ftth"))
    share = round(100 * ftth / premises, 1) if premises and ftth is not None else None
    return {
        "source": DATASET,
        "quarter": row.get("trimestre"),
        "premises": premises,
        "ftth_premises": ftth,
        "ftth_share": share,
    }


def _count(value) -> Optional[int]:
    """Round a premises count to an integer; ``None`` stays ``None``."""
    return None if value is None else round(float(value))
