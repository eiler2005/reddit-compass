# Radar prompt contracts

Все prompts должны возвращать JSON, валидируемый Pydantic-схемами. Любой тезис без
`evidence_ids` не попадает в UI.

## item_facets

```text
Classify each item for trendwatching.

Return JSON:
{
  "items": [
    {
      "item_id": "...",
      "domain_ids": ["ai_technology"],
      "theme_ids": ["ai_agents"],
      "candidate_themes": [],
      "pain_points": [],
      "actors": [],
      "geography": [],
      "content_scope": "headline|abstract|excerpt|full",
      "confidence": "low|medium|high",
      "goal_relevance": {"book": 0, "rbc": 0, "business": 0},
      "summary_ru": "...",
      "evidence_ids": ["item_id"]
    }
  ]
}
```

## story_merge_review

```text
Decide whether 2-5 items describe the same concrete story.
Use canonical URL, target URL, title, actors, event, geography and date.
Do not merge broad themes.

Return JSON:
{"same_story": true, "reason": "...", "canonical_title": "...", "evidence_ids": ["..."]}
```

## trend_naming

```text
Name a trend from several stories without losing specificity.
A trend is a repeated pattern across stories, not one article headline.

Return JSON:
{"trend_name": "...", "domain_ids": [], "theme_ids": [], "evidence_ids": []}
```

## trend_interpretation

```text
Explain why this trend is growing/resurfacing/fading and what confirms it.
Use only provided evidence.

Return JSON:
{
  "trend_id": "...",
  "why_now_ru": "...",
  "why_it_matters_ru": "...",
  "counterpoints": [],
  "confidence": "low|medium|high",
  "evidence_ids": []
}
```

## rbc_lens

```text
Find column angles for RBC: business impact, conflict of interests, market signal,
regulatory angle, management consequence. Avoid generic AI takes.

Return JSON:
{"ideas": [{"title_ru": "...", "angle_ru": "...", "evidence_ids": []}]}
```

## book_lens

```text
Find long-horizon thesis material for the book: AI, labor, institutions, society,
trust, agency, expertise, infrastructure. Prefer recurring evidence over one-day noise.

Return JSON:
{"theses": [{"thesis_ru": "...", "changed_since_last_run_ru": "...", "evidence_ids": []}]}
```

## meta_trends

```text
Compare 7d/30d history and identify narrative shifts.
Only explain shifts already visible in computed metrics.

Return JSON:
{"shifts": [{"from_ru": "...", "to_ru": "...", "metric_basis": "...", "evidence_ids": []}]}
```
