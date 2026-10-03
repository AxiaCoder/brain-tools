"""Write the triage list: one line per extracted-but-not-yet-curated link.

The queue lives on disk as pivots, but `state ready` only prints ids and a
truncated TikTok title - which is the description cut mid-sentence, not a
title. Sorting a hundred links needs something a human can read, annotate,
and come back to between sessions.

So this renders the pending queue as a markdown table into the brain, with an
empty decision column. It is a working file: regenerate it after each pass and
delete it once the cluster is curated.

    python -m ingest.triage --export <user_data_tiktok.json> [--out PATH]

The theme column is guessed from keywords, never from reading. It is there to
scan by, and the file says so in its own header - a guessed label presented as
a verdict would be worse than no label.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from . import state

THEMES = [
    ("recette", r"recette|ingr[ée]dient|cuisson|po[êe]le|pr[ée]paration|prot[ée]in|di[èe]te|macros|buns|tacos|p[âa]tes|sauce|four"),
    ("claude / IA", r"claude|prompt|\bIA\b|\bLLM\b|\bmcp\b|skill|chatgpt|opus|gemini|midjourney"),
    ("auto-entreprise", r"auto.?entrepr|micro.?entrepr|urssaf|factur|siret|\bcgv\b|freelance|comptab|mentions? l[ée]gales|lancement"),
    ("dev / web", r"\bseo\b|site (web|pro)|wordpress|\bcss\b|javascript|python|\bapi\b|github|d[ée]velopp|no.?code"),
    ("écriture", r"roman|personnage|lecteur|worldbuilding|intrigue|m[ée]chant|narrat|elfique|fantasy|chapitre|sc[ée]nario|univers"),
    ("géopolitique", r"g[ée]opolitique|guerre|russie|chine|ouighour|conflit|otan|g[ée]ostrat"),
    ("culture", r"marvel|avengers|\bfilm|cin[ée]ma|s[ée]rie tv|livres? "),
    ("carrière", r"\bcv\b|entretien|recrut|salaire|linkedin|candidat"),
    ("sport / santé", r"muscu|s[ée]ance|entra[îi]nement|prise de masse|\bs[èe]che\b|sommeil|cardio"),
    ("voyage", r"voyage|expat|van\b|road ?trip"),
]


def guess_theme(text: str) -> str:
    hits = [name for name, pattern in THEMES if re.search(pattern, text, re.I)]
    return " + ".join(hits[:2]) if hits else "—"


def collect(export: Path) -> list[dict]:
    """Every pending pivot, newest favourite first."""
    dates = {}
    if export and export.exists():
        for entry in state.read_export(export):
            match = re.search(r"/(\d{15,})", entry["url"])
            if match:
                dates[match.group(1)] = entry["date"]

    rows = []
    for path in state.pivots_dir().glob("*.json"):
        pivot = json.loads(path.read_text(encoding="utf-8"))
        desc = re.sub(r"#\w+", "", re.sub(r"\s+", " ", pivot.get("description") or "")).strip()
        screen = pivot.get("screen_text") or ""
        if not desc:
            desc = re.sub(r"\s+", " ", screen)[:160].strip()
        rows.append({
            "id": pivot["source_id"],
            "date": dates.get(pivot["source_id"], "?"),
            "author": pivot.get("author") or "?",
            "duration": pivot.get("duration_s"),
            "kind": "carrousel" if (pivot.get("meta") or {}).get("post_type") == "photo" else "vidéo",
            "voice": len(pivot.get("raw_text") or ""),
            "desc": desc[:200],
            "theme": guess_theme(desc + " " + screen[:500]),
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def render(rows: list[dict], title: str) -> str:
    lines = [
        "---",
        "scope: Liste de tri des favoris TikTok extraits, en attente de curation. Fichier de travail, à supprimer une fois la grappe curée.",
        'load_when: "tri des favoris TikTok, /ingest, reprise du rattrapage"',
        "updated: 2026-08-22",
        "---",
        "",
        f"# {title}",
        "",
        f"> {len(rows)} liens extraits, en attente de curation. Les pivots sont sur disque : "
        "**aucun re-téléchargement n'est nécessaire**, la curation se fait hors ligne.",
        "> **Le thème est deviné par mots-clés, pas par lecture.** Il sert à scanner, pas à décider.",
        "> Colonne « décision » à remplir : `capture` · `bookmark` · `kitchen` · `jeter` · `?`",
        "",
        "| # | Date | Thème (auto) | Auteur | Format | De quoi ça parle | Décision |",
        "|---|------|--------------|--------|--------|------------------|----------|",
    ]
    for n, row in enumerate(rows, 1):
        fmt = f"{row['kind']} {row['duration']}s" + (" · **muet**" if row["voice"] == 0 else "")
        lines.append(f"| {n} | {row['date'][:10]} | {row['theme']} | `{row['author']}` | "
                     f"{fmt} | {row['desc'].replace('|', '/')} | |")
    lines += ["", "## Identifiants", "",
              "<details><summary>id par numéro, pour la curation</summary>", ""]
    lines += [f"{n}. `{row['id']}`" for n, row in enumerate(rows, 1)]
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(prog="python -m ingest.triage",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("--export", type=Path, help="user_data_tiktok.json, pour dater les lignes")
    parser.add_argument("--out", type=Path,
                        default=Path(state.__file__).parent.parent / "TRIAGE.md")
    parser.add_argument("--title", default="Tri — favoris TikTok en attente")
    args = parser.parse_args(argv)

    rows = collect(args.export)
    if not rows:
        print("Aucun pivot en attente : rien à trier.")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(rows, args.title), encoding="utf-8")
    print(f"{len(rows)} liens -> {args.out}")
    for theme, count in Counter(r["theme"] for r in rows).most_common():
        print(f"  {count:3}  {theme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
