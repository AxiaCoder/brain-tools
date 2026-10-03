"""Ingestion state: what has been handled, how far, and where it went.

One file per link, ``<STATE_PATH>/processed/<source_type>_<source_id>.json``. One file
per link rather than a single ledger is what keeps the state mergeable: two
machines ingesting different links touch different files, so git never has to
reconcile competing writes to the same last line.

The record carries three things the earlier two-field version did not:

* **what the link was** - url, title, author. Without them "the last video
  handled" reads as ``tiktok_7675791138401340704.json`` and says nothing.
* **how far it got** - ``extracted`` is set when the handler returns, ``done``
  only once the outputs are written. A run interrupted in between leaves the
  link visibly unfinished instead of silently burnt.
* **where it went** - the destinations actually written, not the ones decided.

Reading:

    python -m ingest.state last [N]
    python -m ingest.state pending --export <user_data_tiktok.json> [--order recent|old] [--limit N]
    python -m ingest.state report

Writing (called by the /ingest command once routing has succeeded):

    python -m ingest.state done --type tiktok --id 123 [--brain PATH] [--bookmark] [--app kitchen:slug]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

STATE_PATH_VAR = "STATE_PATH"


class StatePathError(RuntimeError):
    """Raised when ``STATE_PATH`` is unset, empty, or not an existing directory."""


def state_root() -> Path:
    """The state directory named by ``STATE_PATH``, read at each call.

    Raises StatePathError when the variable is unset, empty, or does not name
    an existing directory. The directory itself is never created.
    """
    raw = os.environ.get(STATE_PATH_VAR, "").strip()
    if not raw:
        raise StatePathError(
            f"{STATE_PATH_VAR} is not set. Set it in the .env file at the repo root "
            "to the ingestion state directory (outside the repository)."
        )
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise StatePathError(
            f"{STATE_PATH_VAR}={raw} is not an existing directory. Create it, or fix "
            "the value in the .env file at the repo root."
        )
    return root


def processed_dir() -> Path:
    """One record per link handled: ``<STATE_PATH>/processed``."""
    return state_root() / "processed"


def pivots_dir() -> Path:
    """Extracted pivots waiting to be curated: ``<STATE_PATH>/pivots``.

    A queue, not an archive: mark_done deletes the file.
    """
    return state_root() / "pivots"


def covers_dir() -> Path:
    """Cover images kept for a second look: ``<STATE_PATH>/covers``."""
    return state_root() / "covers"

# The handler returned, nothing has been written to a destination yet.
STATUS_EXTRACTED = "extracted"
# Curation and routing are done. This is the only status that means "finished".
STATUS_DONE = "done"
# Extraction failed. Skipped by default so a dead video does not loop, but
# listed by `report` and replayable with --retry-errors.
STATUS_ERROR = "error"

TAB = chr(9)


def state_file(source_type: str, source_id: str) -> Path:
    return processed_dir() / f"{source_type}_{source_id}.json"


def _normalize(raw: dict, path: Path) -> dict:
    """Bring any record - including the pre-2026-08-22 two-field form - up to date.

    The old form was ``{"processed_at": ..., "status": "ok"|"error"}`` with the
    type and id only present in the filename. Those records are read as
    finished; they are never rewritten, because re-running them would cost a
    download to recover a title nobody is waiting for.
    """
    if "source_id" in raw and "status" in raw:
        return raw

    stem = path.stem
    source_type, _, source_id = stem.partition("_")
    processed_at = raw.get("processed_at")
    legacy_status = raw.get("status", "ok")
    status = STATUS_ERROR if legacy_status == "error" else STATUS_DONE

    return {
        "source_type": source_type,
        "source_id": source_id,
        "url": raw.get("url"),
        "title": None,
        "author": None,
        "status": status,
        "extracted_at": processed_at,
        "completed_at": processed_at if status == STATUS_DONE else None,
        "outputs": {"brain": None, "bookmark": False, "app": None},
        "error": raw.get("error"),
        "legacy": True,
    }


def read_record(source_type: str, source_id: str) -> Optional[dict]:
    path = state_file(source_type, source_id)
    if not path.exists():
        return None
    try:
        return _normalize(json.loads(path.read_text(encoding="utf-8")), path)
    except (json.JSONDecodeError, OSError) as e:
        # A corrupt record must not read as "already done" - that would drop
        # the link for good. Surface it and treat it as unseen.
        print(f"[state] unreadable record {path.name}: {e}", file=sys.stderr)
        return None


def _write(record: dict) -> Path:
    processed_dir().mkdir(parents=True, exist_ok=True)
    path = state_file(record["source_type"], record["source_id"])
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def pivot_file(source_type: str, source_id: str) -> Path:
    return pivots_dir() / f"{source_type}_{source_id}.json"


def save_pivot(pivot) -> Path:
    """Persist an extracted pivot so curation can run without the network."""
    pivots_dir().mkdir(parents=True, exist_ok=True)
    path = pivot_file(pivot.source_type, pivot.source_id)
    payload = dict(vars(pivot))
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")
    return path


def load_pivot(source_type: str, source_id: str) -> Optional[dict]:
    path = pivot_file(source_type, source_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[state] unreadable pivot {path.name}: {e}", file=sys.stderr)
        return None


def mark_extracted(source_type: str, source_id: str, url: str,
                   title: str = None, author: str = None,
                   pivot_path: str = None) -> Path:
    """Handler returned. Nothing is routed yet, and that is the point.

    ``pivot_path`` is set by the batch extractor: with the pivot on disk the
    link is resumable without the network, which the single-link path - where
    the media is thrown away and nothing is kept - is not.
    """
    return _write({
        "source_type": source_type,
        "source_id": source_id,
        "url": url,
        "title": title,
        "author": author,
        "status": STATUS_EXTRACTED,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "completed_at": None,
        "outputs": {"brain": None, "bookmark": False, "app": None},
        "pivot_path": pivot_path,
        "error": None,
    })


def mark_done(source_type: str, source_id: str, brain: str = None,
              bookmark: bool = False, app: str = None,
              discarded: bool = False, reason: str = None) -> Path:
    """Outputs are written. Only now is the link finished.

    Keeps whatever the extraction step recorded (url, title, author) so a link
    completed through the command line does not lose its identity.
    """
    record = read_record(source_type, source_id) or {
        "source_type": source_type,
        "source_id": source_id,
        "url": None,
        "title": None,
        "author": None,
        "extracted_at": None,
    }
    record.update({
        "status": STATUS_DONE,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "outputs": {"brain": brain, "bookmark": bool(bookmark), "app": app},
        # A curated-then-dropped link and a link that produced nothing by
        # accident are both "done with empty outputs". Over hundreds of links
        # that difference is worth a field.
        "discarded": bool(discarded),
        "discard_reason": reason if discarded else None,
        "error": None,
    })
    record.pop("legacy", None)
    # The queue entry has served its purpose. Keeping it would turn a work
    # queue into an archive nobody prunes.
    record.pop("pivot_path", None)
    pivot_file(source_type, source_id).unlink(missing_ok=True)
    return _write(record)


def mark_error(source_type: str, source_id: str, url: str, error: str) -> Path:
    """Extraction failed. Keeps the title and author of an earlier extraction, if any."""
    previous = read_record(source_type, source_id) or {}
    return _write({
        "source_type": source_type,
        "source_id": source_id,
        "url": url,
        "title": previous.get("title"),
        "author": previous.get("author"),
        "status": STATUS_ERROR,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "completed_at": None,
        "outputs": {"brain": None, "bookmark": False, "app": None},
        "error": error,
    })


def should_skip(source_type: str, source_id: str, retry_errors: bool = False) -> bool:
    """Whether dispatch should leave this link alone.

    ``extracted`` deliberately does not skip: the media was thrown away after
    the single pass, so resuming an unfinished link means doing it again.
    """
    record = read_record(source_type, source_id)
    if record is None:
        return False
    if record["status"] == STATUS_DONE:
        return True
    if record["status"] == STATUS_ERROR:
        return not retry_errors
    return False


def all_records() -> list[dict]:
    """Every record, most recently touched first."""
    records_dir = processed_dir()
    if not records_dir.exists():
        return []
    records = []
    for path in records_dir.glob("*.json"):
        try:
            records.append(_normalize(json.loads(path.read_text(encoding="utf-8")), path))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[state] unreadable record {path.name}: {e}", file=sys.stderr)
    records.sort(key=lambda r: r.get("completed_at") or r.get("extracted_at") or "", reverse=True)
    return records


# --- The TikTok data export -------------------------------------------------
# Not a handler and not a source type: the export is a *list of links*. It is
# read here because the only question it answers is "which of these are left".

def read_export(path: Path) -> list[dict]:
    """Favourites from ``user_data_tiktok.json``, newest first (export order).

    Only ``Favorite Videos`` is read. ``Like List`` is a different pile - it
    overlaps the favourites on 9 links out of 524 - and is out of scope.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        items = data["Likes and Favorites"]["Favorite Videos"]["FavoriteVideoList"]
    except (KeyError, TypeError):
        raise ValueError(
            f"{path}: no 'Likes and Favorites > Favorite Videos > FavoriteVideoList'. "
            "Is this a TikTok data export?"
        )
    return [{"date": it.get("Date"), "url": it.get("Link")} for it in items if it.get("Link")]


def _identify(url: str):
    """(source_type, source_id) or None when the URL is not one we handle."""
    from .dispatch import detect_source_type, extract_source_id  # local: avoids a cycle
    source_type = detect_source_type(url)
    if source_type == "unknown":
        return None
    try:
        return source_type, extract_source_id(url, source_type)
    except ValueError:
        return None


def pending(entries: list[dict], retry_errors: bool = False) -> list[dict]:
    """The entries that still have work to do, in the order given."""
    out = []
    for entry in entries:
        ident = _identify(entry["url"])
        if ident is None:
            out.append({**entry, "reason": "unrecognised"})
            continue
        source_type, source_id = ident
        if should_skip(source_type, source_id, retry_errors=retry_errors):
            continue
        record = read_record(source_type, source_id)
        out.append({
            **entry,
            "source_type": source_type,
            "source_id": source_id,
            "reason": record["status"] if record else "new",
        })
    return out


# --- CLI --------------------------------------------------------------------

def _fmt(record: dict) -> str:
    when = record.get("completed_at") or record.get("extracted_at") or "?"
    title = record.get("title") or "(titre inconnu)"
    author = record.get("author")
    outputs = record.get("outputs") or {}
    dests = []
    if outputs.get("brain"):
        dests.append(f"brain:{outputs['brain']}")
    if outputs.get("bookmark"):
        dests.append("bookmark")
    if outputs.get("app"):
        dests.append(f"app:{outputs['app']}")
    if record.get("discarded"):
        dests.append("ecarte" + (f" ({record['discard_reason']})" if record.get("discard_reason") else ""))
    if record.get("error"):
        dests.append(f"erreur:{record['error'][:60]}")
    tail = " | ".join(dests) or "-"
    who = f" @{author}" if author else ""
    return f"{when[:16]}  [{record['status']:9}] {title}{who}\n{'':20}{tail}"


def main(argv=None) -> int:
    # The Windows console is cp1252: titles come out as mojibake and a stray
    # emoji raises UnicodeEncodeError mid-listing. Force UTF-8 on the way out.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(prog="python -m ingest.state", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_last = sub.add_parser("last", help="derniers liens traites")
    p_last.add_argument("n", nargs="?", type=int, default=10)

    p_pending = sub.add_parser("pending", help="ce qui reste a faire dans une liste")
    p_pending.add_argument("--export", type=Path, required=True)
    p_pending.add_argument("--order", choices=["recent", "old"], default="recent")
    p_pending.add_argument("--limit", type=int)
    p_pending.add_argument("--retry-errors", action="store_true")
    p_pending.add_argument("--verbose", action="store_true")

    sub.add_parser("report", help="compteurs et liens restes en plan")

    p_ready = sub.add_parser("ready", help="pivots extraits en attente de curation")
    p_ready.add_argument("n", nargs="?", type=int, default=20)

    p_show = sub.add_parser("show", help="le pivot d un lien, pour la curation")
    p_show.add_argument("--type", required=True)
    p_show.add_argument("--id", required=True)

    p_done = sub.add_parser("done", help="marquer un lien comme route")
    p_done.add_argument("--type", required=True)
    p_done.add_argument("--id", required=True)
    p_done.add_argument("--brain")
    p_done.add_argument("--bookmark", action="store_true")
    p_done.add_argument("--app")
    p_done.add_argument("--discard", action="store_true",
                        help="cure puis ecarte : aucune destination, volontairement")
    p_done.add_argument("--reason", help="pourquoi ecarte")

    args = parser.parse_args(argv)

    if args.cmd == "last":
        records = all_records()[: args.n]
        if not records:
            print("Aucun lien traite.")
            return 0
        for record in records:
            print(_fmt(record))
        return 0

    if args.cmd == "pending":
        entries = read_export(args.export)
        if args.order == "old":
            entries = list(reversed(entries))
        todo = pending(entries, retry_errors=args.retry_errors)
        # The total is reported before --limit is applied: a capped listing
        # that announces its own length as the backlog reads as almost done.
        total = len(todo)
        shown = todo[: args.limit] if args.limit else todo
        for entry in shown:
            print(f"{entry['url']}\t{entry['date']}\t{entry['reason']}" if args.verbose else entry["url"])
        tail = f", {len(shown)} affiches (--limit)" if len(shown) != total else ""
        print(f"# {total} a faire sur {len(entries)} favoris{tail}", file=sys.stderr)
        return 0

    if args.cmd == "report":
        records = all_records()
        counts = {}
        for record in records:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "aucun"
        print(f"{len(records)} liens en etat : {detail}")
        stuck = [r for r in records if r["status"] == STATUS_EXTRACTED]
        errors = [r for r in records if r["status"] == STATUS_ERROR]
        ready = [r for r in stuck if r.get("pivot_path")]
        redo = [r for r in stuck if not r.get("pivot_path")]
        if ready:
            print()
            print(f"{len(ready)} pivot(s) en attente de curation - state show puis /ingest :")
            for record in ready:
                print(f"  {record['source_type']}_{record['source_id']}  {record.get('title') or ''}"[:110])
        if redo:
            print()
            print(f"{len(redo)} extrait(s) sans pivot - a re-extraire :")
            for record in redo:
                print(f"  {record.get('url') or record['source_id']}")
        if errors:
            print(f"\n{len(errors)} en erreur - rejouables avec --retry-errors :")
            for record in errors:
                print(f"  {record.get('url') or record['source_id']} : {(record.get('error') or '')[:80]}")
        return 0

    if args.cmd == "ready":
        waiting = [r for r in all_records()
                   if r["status"] == STATUS_EXTRACTED and r.get("pivot_path")]
        # Oldest first: the pivot that has waited longest is the one most
        # likely to be forgotten.
        waiting.sort(key=lambda r: r.get("extracted_at") or "")
        if not waiting:
            print("Aucun pivot en attente.")
            return 0
        for record in waiting[: args.n]:
            print(TAB.join([record["source_type"], record["source_id"],
                            (record.get("title") or "")[:70]]))
        print(f"# {len(waiting)} en attente", file=sys.stderr)
        return 0

    if args.cmd == "show":
        pivot = load_pivot(args.type, args.id)
        if pivot is None:
            print(f"Aucun pivot pour {args.type}_{args.id}", file=sys.stderr)
            return 1
        print(json.dumps(pivot, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "done":
        path = mark_done(args.type, args.id, brain=args.brain, bookmark=args.bookmark,
                         app=args.app, discarded=args.discard, reason=args.reason)
        print(f"done -> {path}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
