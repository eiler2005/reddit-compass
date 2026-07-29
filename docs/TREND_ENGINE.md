# Story/Trend Engine: versioned development and operations

This document is the canonical contract for developing clustering and trend discovery.
Research background lives in
[`STORY_TREND_CLUSTERING_RESEARCH.md`](STORY_TREND_CLUSTERING_RESEARCH.md).

## 1. Runtime boundary

```text
Collector
  └── compass.db
        items / observations / source_health / runs
          ↓ one read-only snapshot transaction
Trend Engine
  └── trend_engine.db
        DataRelease → FacetRelease → StoryRelease → TrendRelease
                                                ↓ manual pointer switch
                                          RadarPublication
                                                 ↓
                                      News / Stories / Trends
                                      Radar / Today / Project Lens
```

The boundary is enforced in code:

- `collector.py` imports no clustering, ranking, briefing, signals or LLM modules.
- `collect` finishes when source collection finishes. LLM availability does not affect
  collection status.
- Trend Engine opens `compass.db` with SQLite `mode=ro` and `PRAGMA query_only`.
- A finalized `DataRelease` contains full copied rows, not live item references.
- SQLite triggers prohibit insert/update/delete of finalized release rows.
- Story and trend attempts only write `trend_engine.db`.
- Radar reads the current `published_channels[channel]` pointer.
- News, Stories, Trends and Project Lens are separate read models over the same publication.
- Rollback changes that pointer; it does not restore backup tables.

`compass.db` keeps legacy derived tables for one transition release, but new `collect` commands do
not write them. `db rebuild` remains a legacy recovery command and is not an Engine workflow.

See [`NEWS_STORIES_TRENDS.md`](NEWS_STORIES_TRENDS.md) for the product contract:
News is raw inbox, Stories are concrete events, Trends are recurring patterns, and Project Lens is
goal-specific ranking for book/RBC/business work.

## 2. Version graph

```text
DataRelease
  └── FacetRelease
        └── StoryRelease
              └── TrendRelease
                    └── RadarPublication
```

Every attempt has its own ID, parent, method, parameter hash, Git SHA, timestamps and metrics.
Running the same stage again creates a new attempt; previous rows are never deleted.

### DataRelease

A Data Release freezes:

- collection run IDs and dates;
- complete/partial input status;
- full item rows, observations and source health;
- canonical, target and discussion URLs;
- source provider, section, cluster and content scope;
- engagement and timestamps;
- config/Git metadata;
- per-row checksums and a deterministic whole-release checksum.

Before facets, stories, trends and publication, the checksum is verified again.

### FacetRelease

Facets contain domains, themes, candidate themes, pains, typed entities, event frames, project
scores and evidence scope. The default pass is deterministic. If `en_core_web_sm` is installed,
spaCy adds typed people, organizations, geography, dates, numbers and a basic action/object frame.
If the model is absent, the release records `deterministic_fallback`; it does not silently claim
spaCy coverage.

### StoryRelease

A Story is one concrete event. It is not a topic.

Candidate generation combines:

1. canonical/target URL equality;
2. normalized title tokens;
3. typed entity inverted indexes;
4. optional local multilingual embeddings;
5. temporal compatibility;
6. shared dates, numbers, people and geography.

The implementation creates only inverted-index and dense top-K pairs. It does not persist an
all-pairs matrix. Event-specific exact URLs merge deterministically. Long-lived landing URLs
(repository roots and model/project pages) still require title/time/event agreement, because one
URL can host multiple releases or reports. High-confidence pairs auto-merge. Hard event conflicts
reject. Only the grey zone is eligible for Qwen review.

Cluster construction is constrained agglomeration:

- each new member must match the cluster medoid;
- numbers, people and geography cannot have hard conflicts;
- a transitive bridge cannot merge two groups if members fail the medoid threshold;
- story IDs are reconciled with the previous accepted attempt by item overlap;
- merge/split redirects retain provenance.

### TrendRelease

Trend discovery receives Stories, never raw articles/posts. Duplicate coverage of one event
therefore does not increase trend strength.

The graph uses shared themes, pains, non-generic title patterns, actions, source diversity and
time. A domain or entity alone cannot create an edge. Top-K graph edges are reduced into
medoid-constrained communities.

A candidate needs:

- at least three distinct Stories;
- at least two dates;
- a repeated non-entity pattern;
- evidence Story IDs;
- source scope (`cross_source`, `community_only`, `mainstream_only`).

Lifecycle remains `insufficient_history` until seven finalized Data Releases exist for the
profile. Later attempts compare the stable trend ID and event velocity to produce `new`,
`growing`, `stable`, `fading` or `resurfacing`.

## 3. Local workflow

Install the standard environment:

```bash
uv sync --dev
```

Optional local embeddings and spaCy:

```bash
uv sync --dev --extra engine
uv run python -m spacy download en_core_web_sm
```

Collection is independent:

```bash
reddit-compass collect --profile broad --sources reddit,hn,rss,ladder,ph
```

Create an immutable input:

```bash
reddit-compass engine release create \
  --run 2026-07-29:broad \
  --source-db data/compass.db

reddit-compass engine release verify --release RELEASE_ID
```

Run deterministic facets:

```bash
reddit-compass engine facets --release RELEASE_ID --profile broad
```

Optionally cache local E5 vectors once:

```bash
reddit-compass engine embeddings \
  --release RELEASE_ID \
  --model intfloat/multilingual-e5-small
```

Run a small, reproducible Story attempt:

```bash
reddit-compass engine stories propose \
  --facet-release FACET_ID \
  --limit 50

reddit-compass engine stories inspect --story-release STORY_ID --limit 20
```

`--limit` uses stratified domain/source/date seeds plus likely neighbours. A small slice therefore
tests actual merge candidates rather than fifty unrelated rows.

Review only ambiguous pairs:

```bash
reddit-compass engine stories review \
  --story-release STORY_ID \
  --limit 100 \
  --model qwen-plus
```

The review is cached by model, prompt version and normalized input hash. Invalid JSON, unknown
evidence IDs, missing evidence or out-of-range confidence are stored as invalid and never affect
clustering. Create another immutable attempt to consume valid cached reviews:

```bash
reddit-compass engine stories propose --facet-release FACET_ID --limit 50
```

Build and review trends:

```bash
reddit-compass engine trends propose --story-release STORY_ID --window 30d
reddit-compass engine trends inspect --trend-release TREND_ID
reddit-compass engine trends review --trend-release TREND_ID --model qwen-max
reddit-compass engine trends propose --story-release STORY_ID --window 30d
```

Qwen names/interprets a trend only after deterministic graph acceptance. It never clusters the
whole corpus directly.

## 4. Golden Set and gates

Export a version-scoped review file:

```bash
reddit-compass engine golden export \
  --story-release STORY_ID \
  --output data/review/story-golden.json \
  --pair-limit 120 \
  --group-limit 30
```

Fill `label` fields:

- pair: `same_story`, `different_story`, `low_signal`;
- group: `overmerge`, `undermerge`, `low_signal`.

Then import and evaluate:

```bash
reddit-compass engine golden import --input data/review/story-golden.json
reddit-compass engine stories eval --story-release STORY_ID
reddit-compass engine trends eval --trend-release TREND_ID
```

Production Story gate:

- at least 120 reviewed pairs and 30 groups;
- pair precision ≥ 95%;
- pair recall ≥ 75%;
- overmerge rate ≤ 3%;
- cross-source recall ≥ 75%;
- evidence coverage = 100%;
- Qwen touches ≤ 15% of candidate pairs.

Production Trend gate additionally needs at least ten manual trend labels, ≥ 75% useful trends and
100% valid Qwen confirmation coverage for the accepted trends. Deterministic graph output remains
`pending` and is visible only as an experiment until that review exists.

`broad` and `ai-native` channels reject publication until both gates pass. Experiments can be
published to a non-production channel such as `shadow`.

## 5. Publish and rollback

```bash
reddit-compass engine publish \
  --story-release STORY_ID \
  --trend-release TREND_ID \
  --channel shadow

reddit-compass engine publications --channel shadow

reddit-compass engine rollback \
  --channel shadow \
  --to PUBLICATION_ID
```

For the production Broad channel, use `--channel broad` only after gates and the seven-release
shadow period. A partial Data Release cannot be published without `--allow-partial`; the override
is stored in publication history.

The UI/API contracts are:

- `/runs` — collection status and source health;
- `/engine` — immutable versions, metrics and publication history;
- `/api/v2/engine/*` — release inspection and comparison;
- `/radar` — current published pointer;
- `/today` — short briefing derived from a publication.

If a new date has no publication, Radar serves the previous verified publication and says so. It
does not replace a useful Radar with an empty running state.

## 6. Legacy Cluster Lab

The old `lab` CLI and `cluster_lab.db` remain available for one compatibility release. They are
deprecated and must not be used for new experiments.

Safe migration:

```bash
reddit-compass engine legacy \
  --lab-db data/cluster_lab.db \
  --source-db data/compass.db
```

Legacy releases used live references. They are imported only when the recorded corpus checksum
still matches exactly. Otherwise migration records `checksum_mismatch` and refuses to fabricate a
frozen release. Legacy experiments are registered as `requires_rerun`; old proposals are not
silently reinterpreted under new Story/Trend semantics.

## 7. Mandatory development order

1. Synthetic fixtures.
2. 50 real items.
3. 100 items in one domain.
4. 300 mixed-domain items.
5. Full immutable local release.
6. Shadow Engine job on VPS.
7. Seven daily comparisons.
8. Manual production publication.

Before increasing the slice:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Never use a full `db rebuild` to tune clustering. Never test a new Story/Trend attempt by writing
legacy production story tables. Never publish merely because compression became lower: precision,
cross-source recall and useful trends are the gates.

## 8. Local checkpoint (2026-07-29)

The first offline experiment reused one frozen 2,116-item release; it performed no network
collection and changed no production DB.

```text
FacetRelease: 2,116 / 2,116 items, broad-domain coverage 100%
StoryRelease (300 mixed items):
  258 stories
  39 multi-item stories
  2 cross-source stories
  7 grey-zone review pairs
  compression ratio 0.86
TrendRelease:
  3 deterministic candidates
  0 confirmed / 3 pending Qwen review
Golden export:
  120 pairs / 30 groups
Published layers:
  News / Stories / Trends / Project Lens contracts implemented locally
```

The first inspect found and fixed two concrete false-positive sources:

- one long-lived GitHub repository URL had merged two different project reports;
- substring matching classified words such as `support` as `sport`.

The layer smoke test on local `data/compass.db` created
`/tmp/reddit-compass-news-story-trend-smoke.db` from run `2026-07-27:ai-native`.
It found good Story examples where Guardian and Reddit references were joined into one concrete
event:

- `Why does everything feel so joyless?` grouped Guardian duplicates plus Reddit discussion;
- `Misleading AI-generated doctors pose huge danger to public safety` grouped Guardian duplicates
  plus Reddit discussion.

It also showed why Trend publication still needs review gates:

- `open source` is useful as a theme but too broad to publish as an unreviewed trend;
- `marketing tip` is a community-only repeated series, not a business trend without Qwen/manual
  confirmation.

The frozen legacy corpus has no cached E5 vectors, no spaCy model and sparse Reddit target URLs.
Therefore cross-source recall from this checkpoint is diagnostic, not a quality claim. The next
step is manual Golden Set labeling, then the 100-item single-domain gate with optional local
embeddings.

## 9. Current profiling rule

Chunked NumPy cosine top-K is sufficient for current corpora. Add an ANN index only after profiling
a frozen release above 20,000 items. The retrieval layer is replaceable because embeddings are
cached by model hash plus normalized input hash and Story Releases record their parameters.
