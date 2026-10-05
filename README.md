# brain-tools

Turns the videos I save on TikTok and YouTube into notes for my second brain.

This repository is published to be read, not reused: it is wired to one personal knowledge base and
makes no attempt to be generic. What it shows is a working pipeline, its tests, and one way of
splitting work with an AI model — deterministic steps in tested code, judgement left to the model
under a written contract.

## The Brain

*The Brain* is my second brain: a private Git repository of Markdown files, organised by life domain
(health, finance, career, writing, learning…) and by project. AI agents read and write it — Claude
Code on my machines, and a self-hosted agent on a home server. New material never lands directly in
a domain: it waits in an `inbox/`, sorted by category, until a human filing pass merges it into the
right note. The inbox is what keeps the unfiled backlog visible.

brain-tools is what feeds that inbox from video bookmarks.

## What it does

One link in, one decision out:

```
link ─▶ extraction (this repo, tested) ─▶ curation (the model) ─▶ routing (this repo, tested)
                                                                     ├─ a note in the brain's inbox
                                                                     ├─ a bookmark
                                                                     └─ an entry in an app (recipes)
```

**Extraction** reads three channels in a single pass, then throws the media away:

| Channel | Source |
|---|---|
| Voice | YouTube subtitles, or local speech-to-text (faster-whisper, GPU with a CPU fallback; Groq as an option) |
| Description | what the author wrote under the post |
| Screen | text burnt into the frames or carousel slides, read by OCR (RapidOCR on ONNX Runtime) |

Any one of them can carry the whole content, and any one can be empty — a recipe's quantities sit in
the description, a carousel's text is only on screen. TikTok carousels, which yt-dlp does not
download, go through gallery-dl.

**Curation** decides, per link: a category from a closed list, whether to keep the link, whether to
extract the knowledge, whether the content is structured data for an app. That is the model's job,
run from Claude Code, and the rules it follows are written down in [docs/curation.md](docs/curation.md).
The code does not trust the answer: `validate_curation` rejects an unknown category, a note without
a summary, or a recipe that would end up both in the app and in a note.

**Routing** writes the note itself. Bookmarks and app entries are created by the agent through the
home server's MCP tools; for an app entry the model writes the payload and the code adds the source
link to it. The state then records what was actually
written — not what was decided — so that a link is never processed twice, and a link interrupted
halfway is never silently lost.

## How the work is split

| Code | Model |
|---|---|
| Download, transcribe, OCR, parse URLs | Read three noisy channels and tell what the post is about |
| Validate the curation against a schema | Decide where it belongs, and whether it is worth keeping |
| Record state, refuse to run without it | Write the note — with its claims checked first |

The model is never asked to do what a function can do, and a function is never asked to judge.

## Layout

```
ingest/
  dispatch.py       link → handler, idempotence
  pivot.py          what extraction hands to curation
  handlers/         youtube.py, tiktok.py (videos and photo carousels)
  stt.py            speech-to-text, local or Groq
  screen.py         OCR on frames and slides
  curate.py         the curation result and its validation
  route.py          notes, bookmarks, app payloads
  state.py          one record per link, and its CLI
  batch.py          extract many links in one process (the model loads once)
  media.py          the one file kept per post: its cover image
  triage.py         a reading list of extracted links waiting for curation
datagouv/           compare towns on public open data (independent from ingest)
  towns.py          name or INSEE code → commune or arrondissement
  sources/          rents.py, risks.py, water.py — one module per source
tests/              pytest, no network, no GPU
docs/curation.md    the curation contract
```

## datagouv — compare towns

A side tool, independent from `ingest`: it compares French towns side by side on public open data.
No API key, no configuration.

```bash
python -m datagouv.compare_towns "Lyon 3e" "Lyon 7e" Clermont-Ferrand 63113
python -m datagouv.compare_towns "Lyon 3e" Clermont-Ferrand --json
```

A town is a commune or municipal arrondissement name, or its INSEE code. A name must match exactly
(accents and case aside, `Lyon 3e` for `Lyon 3e Arrondissement`); otherwise the candidates are
listed and nothing is picked.

| Data | Source |
|---|---|
| Town lookup | [geo.api.gouv.fr](https://geo.api.gouv.fr) |
| Advertised rents, €/m² charges included, 2025 | « Carte des loyers », Ministère de la Transition écologique, via the data.gouv.fr Tabular API |
| Natural and industrial risks | [Géorisques](https://georisques.gouv.fr) GASPAR |
| Tap water compliance | [Hub'Eau](https://hubeau.eaufrance.fr) `qualite_eau_potable` |
| Recorded crime: burglaries, thefts, violent thefts, armed robberies, assaults outside the family, vandalism | Communal base of crime recorded by the police and gendarmerie, Ministère de l'Intérieur, on data.gouv.fr |
| Fibre (FTTH) coverage | « Indicateur France Très Haut Débit », ANCT, via the data.gouv.fr Tabular API |
| Public transport: metro, tram, bus and regional train lines and stops | [OpenStreetMap](https://www.openstreetmap.org/copyright) via the [Overpass API](https://overpass-api.de) — © OpenStreetMap contributors, ODbL |

Risks, water and fibre are only published per commune: for a Paris, Lyon or Marseille arrondissement
they are read for the whole city, and the output says so. Crime and transport are read per arrondissement.
A source that fails shows its error; the others still render.

Crime shows, for the latest year, the count, the rate per 1,000 inhabitants — per 1,000 dwellings for
burglaries — and the change of that rate in per-mille points over five years; a value the ministry
withholds for statistical secrecy shows as `masked`. The crime file (about 40 MB) is downloaded on
first use, indexed, and kept in `$DATAGOUV_CACHE`, by default `%LOCALAPPDATA%\brain-tools\datagouv`
on Windows and `~/.cache/brain-tools/datagouv` elsewhere. It is downloaded again only when the
ministry publishes a new file.

Transport counts, per mode, the lines with at least one stop in the town — distinct by line number, so
both directions of a line count once; light rail counts as tram, trolleybus as bus, and trains are
regional and commuter lines only — and the stops, distinct by name. Metro and tram stations and train
stations are named when there are eight or fewer. The Overpass servers are often busy: three public
endpoints are tried in turn, and the cells show an error when all of them fail.

## Configuration

Copy `.env.example` to `.env` at the repository root:

- `BRAIN_PATH` — the brain repository, where notes are written.
- `STATE_PATH` — the ingestion state directory, **outside this repository**. Required: if it is unset,
  empty or missing, ingestion stops with an error rather than starting from an empty state and
  reprocessing every link.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest
```

## License

[MIT](LICENSE)
