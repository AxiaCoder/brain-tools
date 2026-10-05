"""Compare French towns side by side on public open data.

    python -m datagouv.compare_towns "Lyon 3e" Clermont-Ferrand 75056 [--json]

A town is a commune or municipal arrondissement name, or its INSEE code. A
source that fails reports its error in its own cells; the others still render.
"""

import argparse
import json
import sys
import textwrap

from datagouv.sources import SOURCES, rents
from datagouv.towns import Town, TownResolutionError, resolve

CELL_WIDTH = 34
RENT_SEGMENTS = {
    "apartment": "Rent, apartment",
    "apartment_1_2_rooms": "Rent, apt 1-2 rooms",
    "apartment_3_plus_rooms": "Rent, apt 3+ rooms",
    "house": "Rent, house",
}
VERDICT_LABELS = {"non_compliant": "NON-compliant", "not_applicable": "n/a"}


def collect(town: Town) -> dict:
    """Run every source for ``town``; a failing source yields ``{"error": ...}``.

    A commune-level-only source queried for an arrondissement uses the parent
    commune, and its result carries ``scope`` naming that commune.
    """
    results = {}
    for source in SOURCES:
        code, scope = town.code, None
        if source.COMMUNE_LEVEL_ONLY and town.parent:
            code, scope = town.parent[0], {"code": town.parent[0], "name": town.parent[1]}
        try:
            result = source.fetch(code)
        except Exception as error:
            result = {"error": f"{type(error).__name__}: {error}", "error_type": type(error).__name__}
        if scope:
            result["scope"] = scope
        results[source.NAME] = result
    return results


def compare(queries: list[str]) -> list[dict]:
    """Resolve every query, then collect every source for each town.

    Raises ``TownResolutionError`` if any query does not resolve to exactly one town.
    """
    towns = [(query, resolve(query)) for query in queries]
    return [
        {"query": query, "code": town.code, "name": town.name, "sources": collect(town)}
        for query, town in towns
    ]


def render_text(report: list[dict]) -> str:
    """Render the comparison as a fixed-width table, one column per town."""
    rows = [("Town", [[t["name"]] for t in report]), ("INSEE code", [[t["code"]] for t in report])]
    for segment, label in RENT_SEGMENTS.items():
        rows.append((label, [_rent_cell(t["sources"]["rents"], segment) for t in report]))
    rows.append(("Risks", [_risks_cell(t["sources"]["risks"]) for t in report]))
    rows.append(("Water, last sample", [_water_cell(t["sources"]["water"], None) for t in report]))
    rows.append(("Water, bacteriological", [_water_cell(t["sources"]["water"], "bacteriological") for t in report]))
    rows.append(("Water, physico-chemical", [_water_cell(t["sources"]["water"], "physico_chemical") for t in report]))

    label_width = max(len(label) for label, _ in rows)
    lines = [
        f"Rents: Carte des loyers {rents.VINTAGE}, {rents.UNIT}.",
        "  value (low-high prediction interval); n = observations in the town;",
        "  'maille' = estimated on a group of towns, not the town alone.",
        "Water: compliance of the latest distinct samples (Hub'Eau).",
        "",
    ]
    separator = "-" * (label_width + (CELL_WIDTH + 3) * len(report))
    for index, (label, cells) in enumerate(rows):
        wrapped = [_wrap(cell) for cell in cells]
        height = max(len(cell) for cell in wrapped)
        for line in range(height):
            parts = [cell[line] if line < len(cell) else "" for cell in wrapped]
            head = label if line == 0 else ""
            lines.append(head.ljust(label_width) + "".join(" | " + p.ljust(CELL_WIDTH) for p in parts).rstrip())
        if index == 1:
            lines.append(separator)
    return "\n".join(lines)


def _wrap(cell: list[str]) -> list[str]:
    """Wrap each line of a cell to ``CELL_WIDTH``, with a hanging indent."""
    out = []
    for line in cell:
        out.extend(textwrap.wrap(line, CELL_WIDTH, subsequent_indent="  ") or [""])
    return out


def _scope_lines(result: dict) -> list[str]:
    """A leading note when the data is for the parent commune, else nothing."""
    scope = result.get("scope")
    return [f"[data for {scope['name']} as a whole]"] if scope else []


def _rent_cell(result: dict, segment: str) -> list[str]:
    """Rent cell: value, interval, observations and estimation level."""
    if "error" in result:
        return [f"error ({result['error_type']}), see --json"]
    row = result["segments"].get(segment)
    if row is None or row["rent_m2"] is None:
        return ["no data"]
    detail = f"n={row['observations']}"
    if row["estimated_on"] != "commune":
        detail += f", {row['estimated_on']}"
    return [f"{row['rent_m2']:.2f} ({row['low_m2']:.2f}-{row['high_m2']:.2f})", detail]


def _risks_cell(result: dict) -> list[str]:
    """Risks cell: one line per risk, sub-type count in parentheses."""
    if "error" in result:
        return _scope_lines(result) + [f"error ({result['error_type']}), see --json"]
    risks = result["risks"]
    if not risks:
        return _scope_lines(result) + ["none listed"]
    lines = []
    for risk in risks:
        extra = f" (+{len(risk['subtypes'])})" if risk["subtypes"] else ""
        lines.append(risk["risk"] + extra)
    return _scope_lines(result) + lines


def _water_cell(result: dict, field) -> list[str]:
    """Water cell: last sample date when ``field`` is ``None``, else a verdict tally."""
    if "error" in result:
        return (_scope_lines(result) if field is None else []) + [f"error ({result['error_type']}), see --json"]
    if field is None:
        return _scope_lines(result) + [result["last_sample_date"] or "no sample"]
    if not result["samples"]:
        return ["-"]
    counts = result[field]
    others = [
        f"{count} {VERDICT_LABELS.get(verdict, verdict)}"
        for verdict, count in sorted(counts.items())
        if verdict != "compliant"
    ]
    return [", ".join([f"{counts.get('compliant', 0)}/{result['samples']} compliant", *others])]


def main(argv=None) -> int:
    """CLI entry point; returns the process exit code (2 when a town does not resolve)."""
    parser = argparse.ArgumentParser(
        prog="python -m datagouv.compare_towns",
        description="Compare French towns on rents, risks and tap water (public open data).",
    )
    parser.add_argument("towns", nargs="+", help="commune or arrondissement name, or INSEE code")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = compare(args.towns)
    except TownResolutionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
