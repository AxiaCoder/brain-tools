"""Data sources. Each module exposes the same contract:

* ``NAME`` - the key under which its result is reported;
* ``COMMUNE_LEVEL_ONLY`` - ``True`` when the source has no data for municipal
  arrondissements, so the caller queries the parent commune instead;
* ``fetch(code: str) -> dict`` - INSEE code in, JSON-serializable dict out; raises on failure.
"""

from datagouv.sources import crime, fibre, rents, risks, water

SOURCES = [rents, risks, water, crime, fibre]
