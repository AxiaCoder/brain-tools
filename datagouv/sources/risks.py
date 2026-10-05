"""Known risks of a commune from Géorisques GASPAR."""

from typing import Optional

from datagouv import net

NAME = "risks"
COMMUNE_LEVEL_ONLY = True

GASPAR_API = "https://georisques.gouv.fr/api/v1/gaspar/risques"


def fetch(code: str) -> dict:
    """Return the risks listed for commune ``code``, sub-types nested under their risk."""
    body = net.get_json(GASPAR_API, {"code_insee": code}) or {}
    details = []
    for entry in body.get("data") or []:
        details.extend(entry.get("risques_detail") or [])
    return {"source": "Géorisques GASPAR", "risks": group(details)}


def group(details: list[dict]) -> list[dict]:
    """Nest each sub-type under the risk whose ``num_risque`` prefixes its own.

    GASPAR lists risks and their sub-types flat: ``11`` (Inondation) is the
    parent of ``112`` (crue à débordement lent). A sub-type is attached to its
    shortest listed prefix; one whose parent is not listed stays top-level.
    Source order is kept.
    """
    numbers = {_number(d) for d in details}
    top: dict[str, dict] = {}
    for detail in details:
        if _root(_number(detail), numbers) is None:
            top[_number(detail) or _label(detail)] = {"risk": _label(detail), "subtypes": []}
    for detail in details:
        root = _root(_number(detail), numbers)
        if root is not None:
            top[root]["subtypes"].append(_label(detail))
    return list(top.values())


def _number(detail: dict) -> str:
    """The ``num_risque`` of a GASPAR entry as a string, empty when missing."""
    return str(detail.get("num_risque") or "")


def _label(detail: dict) -> str:
    """The long label of a GASPAR entry, falling back to its number."""
    return detail.get("libelle_risque_long") or _number(detail)


def _root(number: str, numbers: set[str]) -> Optional[str]:
    """The shortest strict prefix of ``number`` present in ``numbers``, else ``None``."""
    for cut in range(1, len(number)):
        if number[:cut] in numbers:
            return number[:cut]
    return None
