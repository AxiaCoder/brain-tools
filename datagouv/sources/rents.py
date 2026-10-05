"""Advertised rents from the « Carte des loyers » (Ministère de la Transition écologique)."""

from typing import Optional

from datagouv import net

NAME = "rents"
COMMUNE_LEVEL_ONLY = False

VINTAGE = 2025
DATASET = "https://www.data.gouv.fr/datasets/693aa2feed1bf4da603faa49"
TABULAR_API = "https://tabular-api.data.gouv.fr/api/resources/{rid}/data/"
UNIT = "EUR/m2 per month, charges included, advertised rent"

# Segment key -> Tabular API resource id of the 2025 vintage.
RESOURCES = {
    "apartment": "55b34088-0964-415f-9df7-d87dd98a09be",
    "apartment_1_2_rooms": "14a1fe11-b2d1-49b3-9f6b-83d12df9482c",
    "apartment_3_plus_rooms": "5e3b28a4-cf56-43a3-ae79-43cceeb27f8c",
    "house": "129f764d-b613-44e4-952c-5ff50a8c9b73",
}


def fetch(code: str) -> dict:
    """Return the predicted rent per segment for INSEE ``code``.

    A segment the dataset does not cover is ``None``. ``estimated_on`` is
    ``commune`` when the estimate comes from the town itself and ``maille``
    when it comes from a grouping of neighbouring towns.
    """
    segments = {}
    for segment, rid in RESOURCES.items():
        body = net.get_json(
            TABULAR_API.format(rid=rid), {"INSEE_C__exact": code, "page_size": 1}
        )
        rows = (body or {}).get("data") or []
        segments[segment] = parse_row(rows[0]) if rows else None
    return {"vintage": VINTAGE, "unit": UNIT, "source": DATASET, "segments": segments}


def parse_row(row: dict) -> dict:
    """Map one « Carte des loyers » row to the reported fields."""
    return {
        "rent_m2": _number(row.get("loypredm2")),
        "low_m2": _number(row.get("lwr.IPm2")),
        "high_m2": _number(row.get("upr.IPm2")),
        "estimated_on": row.get("TYPPRED"),
        "observations": row.get("nbobs_com"),
    }


def _number(value) -> Optional[float]:
    """Round a numeric cell to cents; ``None`` stays ``None``."""
    return None if value is None else round(float(value), 2)
