"""Resolve a user-typed town (name or INSEE code) to an official commune or arrondissement."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import requests

from datagouv import net

GEO_API = "https://geo.api.gouv.fr/communes"
TOWN_TYPES = "commune-actuelle,arrondissement-municipal"
SEARCH_LIMIT = 20

INSEE_CODE = re.compile(r"^(\d{5}|2[AB]\d{3})$", re.IGNORECASE)

# Municipal arrondissements: (first code, last code) -> (parent commune code, parent name).
ARRONDISSEMENT_PARENTS = {
    ("75101", "75120"): ("75056", "Paris"),
    ("69381", "69389"): ("69123", "Lyon"),
    ("13201", "13216"): ("13055", "Marseille"),
}


class TownResolutionError(ValueError):
    """Raised when a query matches no town, or several without an exact winner."""


@dataclass(frozen=True)
class Town:
    """A resolved town: its INSEE code and official name."""

    code: str
    name: str

    @property
    def parent(self) -> Optional[tuple[str, str]]:
        """``(code, name)`` of the parent commune for an arrondissement, else ``None``."""
        return parent_commune(self.code)


def parent_commune(code: str) -> Optional[tuple[str, str]]:
    """Return ``(code, name)`` of the commune containing arrondissement ``code``, else ``None``."""
    for (first, last), parent in ARRONDISSEMENT_PARENTS.items():
        if code.isdigit() and first <= code <= last:
            return parent
    return None


def normalize(name: str) -> str:
    """Fold a town name for comparison: no accents, no case, no punctuation.

    The trailing word ``Arrondissement`` is dropped, so ``Lyon 3e`` equals
    ``Lyon 3e Arrondissement``.
    """
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    folded = re.sub(r"[^a-z0-9]+", " ", folded).strip()
    return re.sub(r"\s+arrondissement$", "", folded)


def resolve(query: str) -> Town:
    """Resolve ``query`` - a name or a 5-character INSEE code - to a ``Town``.

    Raises ``TownResolutionError`` when nothing matches, or when the name search
    returns no exact match or several: the candidates are listed, none is picked.
    """
    query = query.strip()
    if INSEE_CODE.match(query):
        return _resolve_code(query.upper())
    return _resolve_name(query)


def _resolve_code(code: str) -> Town:
    """Look up an INSEE code among current communes, then municipal arrondissements."""
    for params in ({"fields": "nom"}, {"fields": "nom", "type": "arrondissement-municipal"}):
        try:
            found = net.get_json(f"{GEO_API}/{code}", params)
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                continue
            raise
        if found and found.get("nom"):
            return Town(code=found.get("code", code), name=found["nom"])
    raise TownResolutionError(f"no commune or arrondissement with INSEE code {code}")


def _resolve_name(name: str) -> Town:
    """Search a name and keep the single candidate whose normalized name equals it."""
    candidates = net.get_json(
        GEO_API,
        {"nom": name, "type": TOWN_TYPES, "fields": "code,nom", "limit": SEARCH_LIMIT},
    ) or []
    wanted = normalize(name)
    exact = [c for c in candidates if normalize(c["nom"]) == wanted]
    if len(exact) == 1:
        return Town(code=exact[0]["code"], name=exact[0]["nom"])
    listed = exact or candidates
    if not listed:
        raise TownResolutionError(f"no town found for {name!r}")
    choices = ", ".join(f"{c['nom']} ({c['code']})" for c in listed)
    reason = "several towns are named" if exact else "no exact match for"
    raise TownResolutionError(f"{reason} {name!r} - use an INSEE code: {choices}")
