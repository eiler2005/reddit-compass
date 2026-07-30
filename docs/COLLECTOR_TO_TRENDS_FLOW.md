# Collector → News → Stories → Trends flow

This document describes how raw source collection becomes trendwatching UI data.
It is the operational map for developers and LLM coding agents.

## One-screen architecture

```text
Network sources
  Reddit public JSON / RSS
  Hacker News / ProductHunt
  RSS / NYT / Reuters / BBC / Guardian / FT / others
        │
        ▼
Collector process
  source adapters only
  no story clustering
  no trend ranking
  no LLM analysis requirement
        │
        ▼
compass.db
  runs
  items
  observations
  source_health
  legacy derived tables during transition
        │
        ▼
DataRelease snapshot transaction
  immutable frozen copy
  checksum verified before every experiment
        │
        ▼
trend_engine.db
  FacetRelease → StoryRelease → TrendRelease → RadarPublication
        │
        ▼
GUI / API
  /news     raw evidence inbox
  /stories  concrete event clusters
  /trends   recurring patterns across stories
  /radar    cockpit over a publication or preview release
  /today    short briefing over the published analysis
```

## Separation rules

Collector and Trend Engine are intentionally separate.

| Layer | Reads | Writes | Must not do |
|---|---|---|---|
| Collector | profile config, network sources | `compass.db`, snapshots | clustering, ranking, LLM trend analysis |
| DB repair | `compass.db`, local snapshots | `compass.db` schema/backfill | network collection, story/trend mutation |
| DataRelease | finalized collection rows | immutable frozen rows in `trend_engine.db` | mutate `compass.db` |
| FacetRelease | frozen items | item facets | merge stories |
| StoryRelease | facets + frozen items + embeddings | story candidates/stories | publish Radar directly |
| TrendRelease | accepted stories | trend candidates/trends | treat duplicate articles as trends |
| RadarPublication | evaluated releases | one channel pointer | copy or rewrite stories/trends |

`compass.db` is the corpus store. `trend_engine.db` is the derived analysis store.
Development of stories/trends must happen on immutable releases, not by repeatedly rebuilding the
collector database.

## Source collection flow

```text
config/profiles/broad.json
  source IDs
  Reddit subreddit packs
  RSS/provider sections
  domain weights
  project weights
        │
        ▼
reddit-compass collect --profile broad --sources reddit,hn,rss,ph
        │
        ├─ reddit adapter
        │    public .json API
        │    read-only
        │    discussion_url = reddit comments URL
        │    target_url = outbound article URL when present
        │
        ├─ hn adapter
        │    HN/Algolia-derived items
        │
        ├─ rss/provider adapters
        │    canonical_url = article/feed URL
        │    content_scope = headline/abstract/excerpt/full
        │
        └─ producthunt adapter
             product pulse rows
        │
        ▼
compass.db transaction
  run row
  item rows
  observation rows
  source_health rows
```

Collection status is about source collection only.
LLM availability must not turn a collection run from complete to failed.

Source health is factual:

```text
source_id = provider[:section]
status    = ok | degraded | empty | failed | skipped | not_configured
count     = collected item count
message   = adapter error, expected-min warning, or freshness note
```

## Item model

Every source is normalized to the same item shape before analysis.

```text
ContentItem
  item_id
  provider
  source_cluster
  source_section
  external_id
  canonical_url
  discussion_url
  target_url
  title
  summary_ru
  excerpt
  author
  published_at
  observed_at
  snapshot_date
  language
  content_scope
  domain_ids
  raw_engagement
  metadata
```

Important URL semantics:

| Field | Meaning | Example |
|---|---|---|
| `canonical_url` | best stable URL for the item itself | RSS article URL |
| `discussion_url` | conversation page | Reddit comments URL |
| `target_url` | outbound source discussed by a community item | Reddit link target |

For Reddit, `discussion_url` and `target_url` are both needed.
Without both, the engine cannot connect “Reddit discusses a Reuters article” to the Reuters RSS item.

## Offline repair flow

Use repair when older `compass.db` rows do not have current columns or source health.
This does not collect from the network.

```bash
reddit-compass db repair \
  --source-db data/compass.db \
  --output-dir data/snapshots
```

Repair does:

```text
old compass.db + local snapshots
        │
        ├─ migrate SQLite schema via PRAGMA user_version
        ├─ backfill item URL fields and content scope
        ├─ backfill domain/source section where snapshots contain it
        ├─ rebuild source_health from observations × items
        └─ keep existing collection rows
```

Repair does not:

- call Reddit, HN, RSS, Qwen or any other network source;
- rebuild legacy derived stories;
- write to `trend_engine.db`;
- prove story/trend quality.

## Immutable DataRelease

A DataRelease freezes collector rows for repeatable experiments.

```text
compass.db finalized runs
        │ snapshot transaction
        ▼
trend_engine.db
  data_releases
  release_items
  release_observations
  release_source_health
  checksum
```

Example:

```bash
reddit-compass engine release create \
  --profile broad \
  --from 2026-07-22 \
  --to 2026-07-29
```

Release invariants:

- finalized rows are immutable;
- checksum is verified before every Facet/Story/Trend attempt;
- changing `compass.db` after release creation does not change Engine experiments;
- partial input can be inspected, but default production publish requires explicit override and gates.

## FacetRelease

Facets annotate items but do not merge them.

```text
release_items
        │ deterministic facets / optional LLM item facets
        ▼
item_facets
  domain_ids
  theme_ids
  pain_points
  entities
  event_frame
  project_scores
  summary_ru
```

Command:

```bash
reddit-compass engine facets --release RELEASE_ID --profile broad
```

Facet rules:

- every item must receive at least one `domain_id`;
- `summary_ru` is explanatory, not evidence for event matching;
- story matching uses original title, URL, excerpt, entities and timestamps first.

## StoryRelease

A Story is one concrete event/situation.

```text
News items
  "Reuters: Company X cuts 10,000 jobs"
  "Reddit: discussion of same Reuters URL"
  "HN: thread about the same layoff"
        │
        ▼
Story
  event: Company X cuts 10,000 jobs
  evidence: Reuters + Reddit + HN
```

Candidate generation:

```text
FrozenItem
  ├─ URL index
  │    canonical_url
  │    target_url
  │    discussion_url
  │
  ├─ title/token index
  │    normalized title n-grams
  │
  ├─ entity index
  │    people, orgs, places, dates, numbers
  │
  ├─ near-duplicate index
  │    MinHash/SimHash-style buckets
  │
  └─ embedding top-K
       lexical-hash-v1 or optional E5 vectors
        │
        ▼
PairCandidate
  score
  decision = auto_merge | review | reject
  reason
  features_json
```

Story construction:

```text
PairCandidate edges
        │
        ▼
constrained agglomeration
  no hard conflicts on event people/place/date/numbers
  dense similarity alone cannot auto-merge
  large same-provider groups require event URL/provenance
        │
        ▼
engine_stories + engine_story_items
```

Commands:

```bash
# inspect candidates without saving a release
reddit-compass engine stories candidates \
  --facet-release FACET_ID \
  --embedding-model lexical-hash-v1 \
  --limit 300 \
  --candidate-limit 100

# create a StoryRelease
reddit-compass engine stories propose \
  --facet-release FACET_ID \
  --embedding-model lexical-hash-v1
```

Lab limits must apply before heavy work.
`--limit 50/100/300` must not deserialize embeddings or fuzzy-match against the full frozen release.

## TrendRelease

A Trend is a recurring pattern across several different Stories.

```text
Story A: Company X cuts jobs after AI capex pressure
Story B: Company Y freezes hiring after automation program overruns
Story C: Company Z writes down AI infrastructure spend
        │
        ▼
Trend candidate:
  "AI investment turns into balance-sheet pressure"
```

Trend candidate requirements:

- at least three different Story IDs;
- more than one day when enough history exists;
- a repeated pattern, not just one company/country/person;
- source scope is explicit:
  - `cross_source`;
  - `community_only`;
  - `mainstream_only`.

Command:

```bash
reddit-compass engine trends propose \
  --story-release STORY_RELEASE_ID \
  --window 30d
```

Trend statuses:

```text
pending_review
  deterministic candidate exists, but Qwen/manual review has not confirmed usefulness

confirmed
  reviewed and suitable for production analysis

rejected
  too generic, duplicate-driven, incoherent or not useful
```

`pending_review` trends are useful for inspection.
They are not enough for production `broad` publish.

## Publication and GUI

Radar pages read a publication pointer or a preview fallback.

```text
StoryRelease + TrendRelease
        │
        ├─ shadow publish
        │    channel = broad-preview / shadow
        │    allowed for inspection
        │
        └─ production publish
             channel = broad / ai-native
             requires gates
        │
        ▼
RadarPublication pointer
        │
        ▼
GUI / API
```

Production publish:

```bash
reddit-compass engine publish \
  --story-release STORY_ID \
  --trend-release TREND_ID \
  --channel broad \
  --allow-partial
```

The command only moves a pointer.
It does not rebuild DBs, recollect sources or rewrite stories.

If `broad` has no publication, GUI may show latest evaluated release in preview mode:

```text
/radar    → latest evaluated Engine release with Preview warning
/stories  → latest evaluated StoryRelease with Preview warning
/trends   → latest evaluated TrendRelease with Preview warning
```

Preview is not production.
It is a safe inspection mode.

## Production gates

Production channels are intentionally stricter than preview/shadow channels.

Story gate requires, at minimum:

```text
manual pair labels >= 120
manual group labels >= 30
pair precision >= 95%
pair recall >= 75%
cross-source recall >= 75%
overmerge rate <= 3%
evidence coverage == 100%
Qwen pair ratio <= 15%
```

Trend gate requires:

```text
manual trend labels >= 10
manual usefulness >= 75%
all trends Qwen-reviewed
confirmed trends > 0
```

These gates prevent publishing a broad Radar that looks polished but is based on unverified
undermerged or overmerged clusters.

## Current interpretation of metrics

Typical weak clustering signature:

```text
items: 5522
stories: 5267
compression_ratio: 95%+
cross_source_stories: 65
confirmed_trends: 0
```

This means:

- the corpus and schema are usable;
- the GUI can inspect evidence;
- the Engine still under-merges many items into singleton stories;
- production trendwatching requires labels/Qwen review and better story recall.

It does not mean the collector failed.
It means the Story/Trend Engine is not yet production-qualified.

## Textual data lineage example: Reddit post to Trend

```text
1. Reddit adapter reads public post JSON
   subreddit: r/technology
   title: "Reuters: OpenAI supplier cuts hiring after AI capex review"
   permalink: https://reddit.com/r/technology/comments/...
   url: https://reuters.com/technology/...

2. Collector normalizes ContentItem
   provider = reddit
   source_cluster = voices
   source_section = technology
   discussion_url = reddit permalink
   target_url = Reuters URL
   canonical_url = target_url or discussion_url
   raw_engagement = score/comments

3. Collector writes Observation
   run_id = 2026-07-29:broad
   item_id = reddit:...
   observed_at = run timestamp
   engagement_percentile = within-source percentile

4. DataRelease freezes item and observation
   release_id = 2026-07-22_2026-07-29-broad-r1
   checksum includes item fields and source health

5. FacetRelease adds metadata
   domain_ids = ["ai_technology", "business_markets", "labor_career"]
   pain_points = ["financial pressure", "career uncertainty"]
   entities = ["OpenAI", "Reuters", ...]
   event_frame = actor/action/object/date/geography/numbers

6. StoryRelease links Reddit to source article
   PairCandidate sees reddit.target_url == reuters.canonical_url
   decision = auto_merge
   Story evidence = Reddit discussion + Reuters article

7. TrendRelease groups several distinct Stories
   pattern = "AI capex pressure causes hiring and balance-sheet corrections"
   source_scope = cross_source
   review_status = pending_review until Qwen/manual confirmation

8. RadarPublication exposes it
   /news shows raw Reddit + Reuters items
   /stories shows concrete event cluster
   /trends shows repeated pattern after confirmation/preview
   /radar shows cockpit and project lenses
```

## Developer checklist

Before changing the pipeline, identify the layer:

```text
Collector issue?
  Missing sources, bad counts, wrong URLs, source health.

Repair/release issue?
  Old DB columns, missing target_url/discussion_url, checksum mismatch.

Facet issue?
  Wrong domain/theme/pain/entity/project scores.

Story issue?
  Duplicates not merged, overmerge, generic title matches, missing cross-source links.

Trend issue?
  Generic patterns, duplicate-driven trends, no confirmed trends, insufficient history.

GUI issue?
  Wrong layer shown, no preview warning, broken links, missing evidence.
```

Do not fix a lower-layer problem by changing only the UI.
