# The curation contract

> A snapshot, taken on 2026-10-03, of the instructions the model follows when it curates a link.
> The live version is a Claude Code skill in the private brain repository and keeps evolving; this
> copy is here to show how the work is framed, not to be kept in sync.

Extraction hands the model a *pivot*: the link's metadata and three text channels. The model returns
one decision, which the code validates before anything is written:

```json
{
  "source_id": "the link's id, from the pivot",
  "category": "health | finance | career | learning | ecriture | dating | social | dev | gaming | smarthome | culture | other",
  "keep_link": true,
  "extract_knowledge": false,
  "pitch": "One sentence, always filled",
  "tags": ["free", "tags"],
  "summary_md": "Only when extract_knowledge is true",
  "app_target": null,
  "app_payload": null
}
```

`ecriture` is the brain's writing and worldbuilding domain; the brain is written in French. The only
`app_target` today is `kitchen`, the recipe app.

Most rules below exist because a specific mistake was made once. Where that is the case, the mistake
is kept next to the rule: a rule without its reason gets applied where it does not fit, or dropped
the first time it is in the way.

## Read the three channels before judging

| Channel | Field | What it carries |
|---|---|---|
| Voice | `raw_text` | the soundtrack, transcribed — or the subtitles |
| Description | `description` | what the author wrote under the post |
| Screen | `screen_text` | text burnt into the images, read by OCR |

Any of them can carry all the content, and any of them can be empty. Measured on two real posts: in a
recipe, the voice carried the steps and **the description carried every ingredient with its
quantity**; in a carousel, the voice was empty (instrumental music), the description was hashtags
only, and **the screen carried all 1,421 characters of content**.

**No redundancy.** The voice wins when it exists; the screen fills what the voice does not say; the
description checks what speech-to-text gets wrong — quantities, proper names, spelling, links. The
same information never appears three times.

### The voice that is not a voice

A video with no voice-over does not produce an empty transcript: it produces the **lyrics of its
background music**, fluent and confident. Nothing flags it. The signal is in the metadata — the
track name and artist. A transcript about eternal love under a title like "5 tips for X", with a
track set, is a song: ignore the voice entirely and curate on the screen and the description.

### Repair, don't copy

- A TikTok `title` is the description cut mid-sentence. Write a real title from the content: the
  agent puts it in the pivot it hands to routing, where it becomes the note's heading and file name.
- OCR loses accents, swallows short words and adds stray characters. Fix them while writing — the
  fragments are unambiguous. Never copy a misspelling into the brain.
- OCR fails on exactly the stylised, curved lettering of title slides. When the screen text starts
  mangled, look at the saved cover image. It costs about 1,500 tokens; OCR costs none, so only then.
- Promotional slides ("link in bio", customer testimonials) are noise. Drop them.

## Three destinations

| Decision | When |
|---|---|
| `keep_link` | the value is in the video itself: a visual tutorial, a reference to watch again |
| `extract_knowledge` | the value is the information, and it transfers to text |
| both | a detailed tutorial worth rewatching *and* worth summarising |
| `app_target` | the content is structured data for an existing app |

**Structured data goes to the app, not to a note.** A recipe filed as a note is in the wrong place:
in the recipe app its ingredients feed the shopping list and the meal plan; in a file they are dead
bullet points. So when `app_target` is set, `extract_knowledge` must be false — otherwise the same
recipe lives in two places and drifts. The validation refuses it.

**When in doubt, don't force it.** A link left in the inbox costs thirty seconds of manual filing; a
half-structured recipe in the app costs more.

**A bookmark without tags is a lost bookmark.** Tag at creation, from the existing vocabulary — one
broad tag plus one or two precise ones. One tag feeds a daily "watch this tonight" rotation: it goes
only on what will really be watched later, never on everything.

## Promotional content is kept on purpose

Much of the stock is lead-magnet content ending in "comment X and I'll send you the guide". That is
**never** a reason to reject it: the ideas before the call to action are what was saved. What gets
dropped is the call to action.

Three consequences:

1. Say in the note that the source is promotional and that nothing behind the form was followed or
   checked. Re-read in six months, the note must not pass for first-hand experience.
2. `keep_link` is almost always false: nobody rewatches a sales funnel.
3. A list of nine building blocks is worth nothing on its own. It is worth something compared with
   what already exists — so the note says which ones are already in place and which are missing.

## Ask why it was saved before asking what it is worth

A bookmark is a gesture, not a link met by chance. The content answers *what is this*; the bookmark
answers *why this one*. The second question comes first, because it decides the destination.

Where to look for the answer, in order: the brain itself (background, places, tastes, projects); the
rest of the corpus (a topic that keeps coming back is a real interest, even with no project behind
it); the date (a bookmark saved during a project belongs to that project); and finally, the person —
**ask, never discard on an assumption about their life.**

Three mistakes made on the same day, all of the same kind:

| Proposed | Answer |
|---|---|
| Discard a brunch spot "500 km away" | It is in my home town — and the brain already said so |
| Discard several videos about a lifestyle no active project covers | "If I saved them, it's because they're part of a project one day" |
| Discard a text with "nothing actionable" in it | "I kept it because it made me think of someone" |

In all three, the content was judged against what the model believed it knew about the person,
instead of asking what the bookmark said about their intent. A link can teach nothing and still be
worth keeping — for a place, a person, a memory, a wish. When the value lies in the gesture rather
than the information, the right output is a **bookmark without a note**: summarising a text kept for
what it evokes kills it.

## Two bookmarks saying the same thing are not two sources

The corpus comes from a recommendation feed, not a literature review. If a topic, a tool or a name
comes back across several bookmarks, **the algorithm is sending it back first** — not the field
converging. Click on a topic, get served the topic.

So never write "two independent sources cite it", "this is the third time X comes up, so it's
settled". Those sentences turn a selection bias into evidence. What can be said instead: the topic
recurs in the corpus — which is information about the person's interest, not about the tool's
value. If the underlying question matters, it is settled elsewhere: in code, in a measurement, in a
source with authority. The rule weighs double when two videos seem to confirm a decision already
taken: the temptation is stronger and the mistake costlier.

## A checkable claim is checked before it is written

A short video is not a source. As soon as a note is about to carry an article of law, a threshold, a
rate, an amount, an effective date or a count, **it gets checked first** — not written followed by
"to be checked". A note is re-read months later as established fact; "to be checked" does not
survive that re-reading.

What this turned up across seven videos about French sole-trader rules:

| Written from the videos | What the official sources said |
|---|---|
| The start-up exemption must be requested on the day of registration, no catching up | **60 days** after the business starts |
| A tolerance on the VAT mention "until September 2027" | **31 December 2027** |
| The local business tax "depends on your town and your turnover", 17% in Paris | **minimum base × local rate** — not a share of turnover; 17% is a rate applied to a base |
| "€76 in Paris against €250 minimum elsewhere" | €250 is the **legal floor of the base**, not a tax owed; the two amounts are not comparable |

**Two of those four mistakes came from the curation itself, not from the videos.** Copying a spoken
claim makes you its author.

In practice: check against the authoritative source (the government site, the vendor's site for a
tool, the repository for a project); mark what was checked and cite the source at the end of the
note, so a reader can tell checked from copied; when sources disagree, say so and give an order of
magnitude rather than a wrong figure; what could not be checked stays, named as such, and never at
the top. A search-engine snippet is not a page read.

## When to do nothing

Low quality, entirely off topic, already processed, or too shallow to be worth extracting.
