"""Compare French towns side by side on public open data.

    python -m datagouv.compare_towns "Lyon 3e" Clermont-Ferrand 75056 [--json]

A town is a commune or municipal arrondissement name, or its INSEE code. A
source that fails reports its error in its own cells; the others still render.
"""

import argparse
import json
import sys
import textwrap

import requests

from datagouv.sources import SOURCES, rents, transport
from datagouv.towns import Town, TownResolutionError, resolve

CELL_WIDTH = 34
RENT_SEGMENTS = {
    "apartment": "Rent, apartment",
    "apartment_1_2_rooms": "Rent, apt 1-2 rooms",
    "apartment_3_plus_rooms": "Rent, apt 3+ rooms",
    "house": "Rent, house",
}
VERDICT_LABELS = {"non_compliant": "NON-compliant", "not_applicable": "n/a"}
CRIME_INDICATORS = {
    "burglary": "Crime, burglaries",
    "theft_without_violence": "Crime, theft no violence",
    "violent_theft_unarmed": "Crime, violent theft",
    "armed_robbery": "Crime, armed robbery",
    "assault_outside_family": "Crime, assault",
    "vandalism": "Crime, vandalism",
}
TRANSPORT_MODES = {
    "subway": ("Transport, metro", "station"),
    "tram": ("Transport, tram", "stop"),
    "bus": ("Transport, bus", "stop"),
    "train": ("Transport, train", "station"),
}
TRANSPORT_LISTED_NAMES = 8


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

    Each entry carries ``warnings``, the resolution warnings of its town.
    Raises ``TownResolutionError`` if any query does not resolve to exactly one town.
    """
    towns = [(query, resolve(query)) for query in queries]
    return [
        {
            "query": query,
            "code": town.code,
            "name": town.name,
            "warnings": list(town.warnings),
            "sources": collect(town),
        }
        for query, town in towns
    ]


def render_text(report: list[dict]) -> str:
    """Render the comparison as a fixed-width table, one column per town."""
    rows = [("Town", [[t["name"]] for t in report]), ("INSEE code", [[t["code"]] for t in report])]
    if any(t.get("warnings") for t in report):
        rows.append(("Warning", [t.get("warnings") or ["-"] for t in report]))
    header_rows = len(rows)
    for segment, label in RENT_SEGMENTS.items():
        rows.append((label, [_rent_cell(t["sources"]["rents"], segment) for t in report]))
    rows.append(("Risks", [_risks_cell(t["sources"]["risks"]) for t in report]))
    rows.append(("Water, last sample", [_water_cell(t["sources"]["water"], None) for t in report]))
    rows.append(("Water, bacteriological", [_water_cell(t["sources"]["water"], "bacteriological") for t in report]))
    rows.append(("Water, physico-chemical", [_water_cell(t["sources"]["water"], "physico_chemical") for t in report]))
    for key, label in CRIME_INDICATORS.items():
        rows.append((label, [_crime_cell(t["sources"]["crime"], key) for t in report]))
    rows.append(("Fibre (FTTH)", [_fibre_cell(t["sources"]["fibre"]) for t in report]))
    for mode, (label, _) in TRANSPORT_MODES.items():
        rows.append((label, [_transport_cell(t["sources"]["transport"], mode) for t in report]))

    label_width = max(len(label) for label, _ in rows)
    lines = [
        f"Rents: Carte des loyers {rents.VINTAGE}, {rents.UNIT}.",
        "  value (low-high prediction interval); n = observations in the town;",
        "  'maille' = estimated on a group of towns, not the town alone.",
        "Water: compliance of the latest distinct samples (Hub'Eau).",
        *_crime_header(report),
        "Fibre: share of premises that can be connected to FTTH (ANCT).",
        "Transport: lines with a stop in the town, distinct stops by name; trains: regional",
        f"  and commuter lines, stations and halts. Data {transport.ATTRIBUTION}.",
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
        if index == header_rows - 1:
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
    """Rent cell: value, interval, observations and estimation level, each when known."""
    if "error" in result:
        return [f"error ({result['error_type']}), see --json"]
    row = result["segments"].get(segment)
    if row is None or row["rent_m2"] is None:
        return ["no data"]
    value = f"{row['rent_m2']:.2f}"
    if row["low_m2"] is not None and row["high_m2"] is not None:
        value += f" ({row['low_m2']:.2f}-{row['high_m2']:.2f})"
    details = []
    if row["observations"] is not None:
        details.append(f"n={row['observations']}")
    if row["estimated_on"] not in (None, "commune"):
        details.append(row["estimated_on"])
    return [value, ", ".join(details)] if details else [value]


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


def _crime_header(report: list[dict]) -> list[str]:
    """Header lines for crime: year, rate bases, and the year the change is measured against."""
    years = next((t["sources"]["crime"] for t in report if "year" in t["sources"]["crime"]), None)
    if years is None:
        return ["Crime: recorded offences (Ministère de l'Intérieur)."]
    lines = [
        f"Crime: offences recorded in {years['year']} (Ministère de l'Intérieur): count, rate",
        "  per 1,000 inhabitants (burglaries: per 1,000 dwellings), change of the rate",
        f"  in per-mille points since {years['base_year']}; 'masked' = withheld by the ministry.",
    ]
    stale = next((t["sources"]["crime"] for t in report if t["sources"]["crime"].get("stale")), None)
    if stale:
        lines.append(f"  Using the cached file, refresh failed: {stale['stale_reason']}")
    return lines


def _crime_cell(result: dict, key: str) -> list[str]:
    """Crime cell: count, rate per mille and its change, or ``masked``; ``no rate`` when unpublished."""
    if "error" in result:
        return [f"error ({result['error_type']}), see --json"]
    row = result["indicators"].get(key)
    if row is None:
        return ["no data"]
    if row["masked"]:
        return ["masked"]
    count = "no count" if row["count"] is None else row["count"]
    if row["rate_per_mille"] is None:
        return [f"{count} · no rate"]
    change = row["rate_change_points"]
    trend = "" if change is None else f" ({change:+.2f} pts)"
    return [f"{count} · {row['rate_per_mille']:.2f}‰{trend}"]


def _fibre_cell(result: dict) -> list[str]:
    """Fibre cell: FTTH share, connectable premises out of all, quarter."""
    if "error" in result:
        return _scope_lines(result) + [f"error ({result['error_type']}), see --json"]
    if result["ftth_share"] is None:
        return _scope_lines(result) + ["no data"]
    return _scope_lines(result) + [
        f"{result['ftth_share']:.1f}% ({result['ftth_premises']}/{result['premises']})",
        result["quarter"] or "",
    ]


def _transport_cell(result: dict, mode: str) -> list[str]:
    """Transport cell: line count, stop count, and their names when few enough.

    Line refs are listed for every mode but bus, stop names for metro, tram and
    train; either only up to ``TRANSPORT_LISTED_NAMES`` names.
    """
    if "error" in result:
        return [f"error ({result['error_type']}), see --json"]
    row = result["modes"][mode]
    if not row["lines"] and not row["stops"]:
        return ["none"]
    _, stop_word = TRANSPORT_MODES[mode]
    return [
        _counted(row["lines"], "line", mode != "bus"),
        _counted(row["stops"], stop_word, mode != "bus"),
    ]


def _counted(names: list[str], word: str, listed: bool) -> str:
    """``3 lines: A, B, C``; the names are dropped when not ``listed`` or too many."""
    text = f"{len(names)} {word}{'' if len(names) == 1 else 's'}"
    if listed and 0 < len(names) <= TRANSPORT_LISTED_NAMES:
        text += ": " + ", ".join(names)
    return text


def main(argv=None) -> int:
    """CLI entry point; returns the process exit code.

    2 when a town does not resolve, 3 when the town lookup service cannot be reached.
    """
    parser = argparse.ArgumentParser(
        prog="python -m datagouv.compare_towns",
        description="Compare French towns on rents, risks, tap water, crime, fibre and public transport (open data).",
    )
    parser.add_argument("towns", nargs="+", help="commune or arrondissement name, or INSEE code, not postal code")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        report = compare(args.towns)
    except TownResolutionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except requests.RequestException as error:
        print(f"error: town lookup failed ({type(error).__name__})", file=sys.stderr)
        return 3
    for town in report:
        for warning in town["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
