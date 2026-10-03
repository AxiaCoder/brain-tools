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
tests/              pytest, no network, no GPU
docs/curation.md    the curation contract
```

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
