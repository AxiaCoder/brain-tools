"""Batch extraction: many links, one process, one model load.

Measured on 2026-08-22: loading faster-whisper costs 4.9 s against 1.4 s of
inference. /ingest spawns a Python process per URL, so the whole backlog of
467 favourites would repay that load 467 times - roughly 38 minutes of GPU
thrown away. stt.py already caches the model at module level; what was missing
was a caller that stays alive across links.

What this does *not* do is curate. It produces material and stops:

    python -m ingest.batch --export <user_data_tiktok.json> [--cluster 2026-08]
                           [--order recent|old] [--limit N] [--retry-errors]

Each pivot is written to <STATE_PATH>/pivots/, the record is marked ``extracted``
with its pivot_path, and curation happens later - possibly in another session,
with no network - through `state ready`, `state show` and /ingest.
"""

import argparse
import sys
import time
from pathlib import Path

from . import state
from .dispatch import detect_source_type
from .handlers import youtube, tiktok

_HANDLERS = {"youtube": youtube, "tiktok": tiktok}


def extract_one(url: str) -> dict:
    """Extract a single link and record it. Never raises: a batch must finish.

    Returns a row describing what happened, for the caller's summary.
    """
    source_type = detect_source_type(url)
    if source_type == "unknown":
        return {"url": url, "status": "unknown", "detail": "URL non reconnue"}

    try:
        source_id = _HANDLERS[source_type].extract_video_id(url)
    except ValueError as e:
        return {"url": url, "status": "unknown", "detail": str(e)}

    started = time.time()
    try:
        pivot = _HANDLERS[source_type].extract(url)
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        state.mark_error(source_type, source_id, url, detail)
        return {"url": url, "source_type": source_type, "source_id": source_id,
                "status": "error", "detail": detail,
                "elapsed_s": round(time.time() - started, 1)}

    pivot_path = state.save_pivot(pivot)
    state.mark_extracted(source_type, source_id, url,
                         title=pivot.title, author=pivot.author,
                         pivot_path=str(pivot_path))
    return {
        "url": url, "source_type": source_type, "source_id": source_id,
        "status": "extracted",
        "title": pivot.title,
        "voice": len(pivot.raw_text or ""),
        "description": len(pivot.description or ""),
        "screen": len(pivot.screen_text or ""),
        "elapsed_s": round(time.time() - started, 1),
    }


def select(export: Path, cluster: str = None, order: str = "recent",
           limit: int = None, retry_errors: bool = False) -> list[dict]:
    """The links this run should extract, already filtered by state."""
    entries = state.read_export(export)
    if cluster:
        entries = [e for e in entries if (e["date"] or "").startswith(cluster)]
    if order == "old":
        entries = list(reversed(entries))
    todo = state.pending(entries, retry_errors=retry_errors)
    # A pivot already on disk is work that does not need doing again.
    todo = [e for e in todo if e.get("reason") != "extracted"
            or not state.load_pivot(e["source_type"], e["source_id"])]
    return todo[:limit] if limit else todo


def run(links: list[dict]) -> list[dict]:
    total = len(links)
    print(f"lot : {total} lien(s)", flush=True)
    rows = []
    started = time.time()
    for n, entry in enumerate(links, 1):
        row = extract_one(entry["url"])
        rows.append(row)
        mark = {"extracted": "ok", "error": "ERREUR", "unknown": "IGNORE"}[row["status"]]
        detail = row.get("detail", "")
        sizes = ("voix={voice} desc={description} ecran={screen}".format(**row)
                 if row["status"] == "extracted" else detail[:80])
        print(f"[{n}/{total}] {mark:6} {row.get('elapsed_s', 0):5.1f}s  {sizes}", flush=True)

    elapsed = time.time() - started
    ok = [r for r in rows if r["status"] == "extracted"]
    errors = [r for r in rows if r["status"] == "error"]
    skipped = [r for r in rows if r["status"] == "unknown"]
    print("", flush=True)
    print(f"{len(ok)} extrait(s), {len(errors)} erreur(s), {len(skipped)} ignore(s) "
          f"en {elapsed / 60:.1f} min", flush=True)
    if ok:
        print(f"moyenne {elapsed / len(rows):.1f} s/lien", flush=True)
    for row in errors:
        print(f"  ERREUR {row['url']} -> {row.get('detail', '')[:100]}", flush=True)
    if ok:
        print("", flush=True)
        print(f"{len(ok)} pivot(s) en attente de curation : "
              f"python -m ingest.state ready", flush=True)
    return rows


def main(argv=None) -> int:
    # The Windows console is cp1252: titles come out as mojibake and a stray
    # emoji raises UnicodeEncodeError mid-listing. Force UTF-8 on the way out.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(prog="python -m ingest.batch",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--cluster", help="prefixe de date, ex. 2026-08")
    parser.add_argument("--order", choices=["recent", "old"], default="recent")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="lister ce qui serait extrait, sans rien telecharger")
    args = parser.parse_args(argv)

    links = select(args.export, cluster=args.cluster, order=args.order,
                   limit=args.limit, retry_errors=args.retry_errors)
    if not links:
        print("Rien a extraire.")
        return 0
    if args.dry_run:
        for entry in links:
            print(f"{entry['url']}  {entry['date']}  {entry['reason']}")
        print(f"# {len(links)} lien(s)", file=sys.stderr)
        return 0

    run(links)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
