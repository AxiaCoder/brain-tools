"""Tap water quality from Hub'Eau (contrôle sanitaire de l'eau distribuée)."""

from datagouv import net

NAME = "water"
COMMUNE_LEVEL_ONLY = True

HUBEAU_API = "https://hubeau.eaufrance.fr/api/v1/qualite_eau_potable/resultats_dis"
FIELDS = (
    "code_prelevement,date_prelevement,conclusion_conformite_prelevement,"
    "conformite_limites_bact_prelevement,conformite_limites_pc_prelevement"
)
# One sample spans one row per measured parameter, so many rows make few samples.
ROWS_FETCHED = 1000
TIMEOUT_SECONDS = 60
SAMPLES_KEPT = 10

COMPLIANCE = {"C": "compliant", "N": "non_compliant", "D": "derogation", "S": "not_applicable"}


def fetch(code: str) -> dict:
    """Return the last sample date and the compliance tally of the latest samples of ``code``.

    ``bacteriological`` and ``physico_chemical`` count the latest ``SAMPLES_KEPT``
    distinct samples by Hub'Eau verdict (``C`` compliant, ``N`` non-compliant,
    ``D`` derogation, ``S`` not applicable).
    """
    body = net.get_json(
        HUBEAU_API,
        {"code_commune": code, "size": ROWS_FETCHED, "sort": "desc", "fields": FIELDS},
        timeout=TIMEOUT_SECONDS,
    ) or {}
    samples = latest_samples(body.get("data") or [], SAMPLES_KEPT)
    return {
        "source": "Hub'Eau qualite_eau_potable",
        "samples": len(samples),
        "last_sample_date": samples[0]["date_prelevement"][:10] if samples else None,
        "last_conclusion": samples[0].get("conclusion_conformite_prelevement") if samples else None,
        "bacteriological": tally(samples, "conformite_limites_bact_prelevement"),
        "physico_chemical": tally(samples, "conformite_limites_pc_prelevement"),
    }


def latest_samples(rows: list[dict], limit: int) -> list[dict]:
    """Collapse parameter rows into distinct samples, most recent first, at most ``limit``."""
    samples: dict[str, dict] = {}
    for row in rows:
        samples.setdefault(row.get("code_prelevement") or row.get("date_prelevement"), row)
    ordered = sorted(samples.values(), key=lambda r: r.get("date_prelevement") or "", reverse=True)
    return ordered[:limit]


def tally(samples: list[dict], field: str) -> dict[str, int]:
    """Count ``samples`` by their compliance verdict in ``field``."""
    counts: dict[str, int] = {}
    for sample in samples:
        verdict = COMPLIANCE.get(sample.get(field) or "", "unknown")
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts
