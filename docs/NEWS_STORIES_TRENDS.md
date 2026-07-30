# News, Stories, Trends and Project Lens

This is the product contract for the published analysis UI.
For the operational data lineage from source collection to Radar publication, see
[`COLLECTOR_TO_TRENDS_FLOW.md`](COLLECTOR_TO_TRENDS_FLOW.md).

## Layer definitions

```text
News Item
  ↓ grouped by event identity
Story
  ↓ grouped by recurring pattern across distinct events
Trend
  ↓ filtered and scored for a concrete writing/research goal
Project Lens
```

### News

News is the raw published corpus from an immutable `DataRelease`.

It answers:

- what was collected;
- from which provider, section and source cluster;
- when it was published or observed;
- which Story, if any, the item belongs to.

News must not be described as a trend. It is an inbox and an evidence store.

Public surfaces:

- UI: `/news`
- UI shadow/preview: `/news?channel=shadow`
- API: `GET /api/v2/news`

### Stories

A Story is one concrete event or situation.

Examples:

- a company announces layoffs;
- a regulator opens a case;
- a product launches;
- a specific sports result happens.

Stories are built from News Items using URL, target/discussion URL, title, entity, time and optional
embedding evidence. One story can contain Reddit discussion, RSS article, HN thread and mainstream
coverage of the same event.

Public surfaces:

- UI: `/stories`
- UI detail: `/stories/{story_id}`
- UI shadow/preview: `/stories?channel=shadow`
- API: `GET /api/v2/engine/stories`
- API detail: `GET /api/v2/engine/stories/{story_id}`

### Trends

A Trend is a recurring pattern across several distinct Stories.

Examples:

- AI capex becomes a balance-sheet concern across different companies;
- surveillance backlash moves from complaints to physical sabotage;
- layoffs and hiring freezes repeat across several knowledge-work sectors.

A Trend must not be a pile of duplicate copies of one article. The Engine requires several Story
IDs, several days and a non-generic repeated pattern. Qwen confirmation is mandatory before a Trend
can pass the production publication gate.

Public surfaces:

- UI: `/trends`
- UI detail: `/trends/{trend_id}`
- UI shadow/preview: `/trends?channel=shadow`
- API: `GET /api/v2/engine/trends`
- API detail: `GET /api/v2/engine/trends/{trend_id}`

### Project Lens

Project Lens is a goal-specific view over published Stories and Trends.

Default project IDs:

- `book`
- `rbc`
- `business`

It answers:

- what matters for the book;
- what can become an RBC column;
- what new evidence strengthens or weakens a recurring thesis.

Project Lens does not create new facts. It ranks and groups published Stories and Trends using
`project_scores`.

Public surfaces:

- UI: `/projects/{project_id}`
- UI shadow/preview: `/projects/{project_id}?channel=shadow`
- API: `GET /api/v2/projects/{project_id}/lens`

## GUI contract

Each published analysis page includes the same layer navigation:

- `News`;
- `Stories`;
- `Trends`;
- `Project Lens`;
- `Radar`.

`/radar` is the cockpit. It links to the four working layers and keeps source/version context.
`/news`, `/stories`, `/trends` and `/projects/{project_id}` are the work pages.
`/stories/{story_id}` and `/trends/{trend_id}` are drill-down pages with evidence links.

## Publication channels

Radar and all analysis layers read from a `RadarPublication`.
Default user-facing channel is `broad`.
Experimental runs should be published to `shadow` first:

```bash
reddit-compass engine publish \
  --story-release STORY_RELEASE_ID \
  --trend-release TREND_RELEASE_ID \
  --channel shadow
```

Shadow UI URLs:

- `/radar?channel=shadow`
- `/news?channel=shadow`
- `/stories?channel=shadow`
- `/trends?channel=shadow`
- `/projects/rbc?channel=shadow`

All layer links preserve `channel`.
If `publication_id` is provided explicitly, filters and drill-down links also preserve it so a review
session stays pinned to the same immutable publication.

Production `broad` must not be overwritten just to inspect a new clustering attempt.
Promote to `broad` only after Golden Set / Qwen gates are satisfied, or use an explicit release
manager override with audit trail.

## Development rule

When changing clustering or ranking, tests must target the exact layer being changed:

- collector tests for collection facts only;
- News tests for frozen item projection;
- Story tests for event clustering and evidence membership;
- Trend tests for repeated patterns across Story IDs;
- Project Lens tests for project-specific scoring and sorting.

Do not fix weak Trends by adding more UI. Fix Story quality, then Trend quality, then publish.
