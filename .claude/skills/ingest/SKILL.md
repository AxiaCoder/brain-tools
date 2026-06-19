# Skill: Ingest Curation

Auto-triggered during `/ingest` command to apply curation rules.

## Categories

- **tech**: Programming, devops, software engineering
- **philosophy**: Existentialism, ethics, critical thinking
- **health**: Fitness, nutrition, medical
- **finance**: Investing, budgeting, economics
- **career**: Job skills, leadership, productivity
- **creative**: Art, music, writing, design
- **politics**: Current affairs, governance, activism
- **entertainment**: Movies, games, pop culture
- **misc**: Everything else

## Rules

### keep_link
Save to bookmarks (`resources/bookmarks.md`) when:
- Tutorial or how-to guide (future reference)
- Entertainment content (review later)
- Short-form content (<5min) with no deep insights
- Time-sensitive content (news, events)

### extract_knowledge
Extract and integrate into brain when:
- Evergreen knowledge (timeless concepts)
- Deep dive or analysis (>10min)
- Aligns with active learning goals (check `domains/learning/`)
- Valuable mental model or framework

### discard
Skip entirely when:
- Spam or low-quality content
- Completely off-topic for user interests
- Duplicate of already-processed content

## Output Format

Return JSON:
```json
{
  "action": "keep_link" | "extract_knowledge" | "discard",
  "category": "tech",
  "notes": "Brief explanation of decision"
}
```

## Context Sources

Before curating, check:
- `memory/NOW.md` — current focus areas
- `domains/learning/LEARNING.md` — active learning goals
- User interests from `memory/SELF.md` and `memory/INTELLECTUAL.md`
