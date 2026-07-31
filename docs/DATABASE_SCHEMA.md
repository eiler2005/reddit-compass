# SQLite schema reference — reddit-compass

> Актуальный контракт хранения. Это не дамп одной production-БД: количество строк и IDs меняются
> с каждым run, а ownership таблиц остаётся стабильным. Последняя схема кода —
> `intelligence/migrations.py` для raw corpus и `intelligence/engine.py` для Engine.

## Два независимых хранилища

```text
public sources → JSONL snapshots → compass.db (Collector owns writes)
                                      │ read-only, one snapshot transaction
                                      ▼
                         trend_engine.db (Engine owns writes)
                         DataRelease → FacetRelease → StoryRelease
                                      → TrendRelease → RadarPublication
                                      → published_channels[channel]
                                      ▼
                         News / Stories / Trends / Radar / Today
```

| Store | Owner | Purpose | Mutation rule |
|---|---|---|---|
| `data/compass.db` | Collector | Normalized raw materials, observations, source health and one factual collection run | Engine opens it `mode=ro` + `query_only`; current Collector keeps legacy projections only for transition compatibility. |
| `data/trend_engine.db` | Trend Engine | Frozen corpus releases, facets, story/trend attempts, labels, reviews, quality outcomes and publication pointers | A finalized Data Release is immutable; experiments create new release attempts, never rewrite raw data. |
| `data/snapshots/YYYY-MM-DD/*.jsonl` | Collector / handoff | Exchange, debug and recovery artifacts per source | Finalizer reads them into the raw corpus; they are not the UI source of truth. |

`db rebuild` applies only to legacy raw-corpus recovery. It is **not** a way to develop stories or
trends, and it does not re-create an Engine publication. For the completion contract, see
[`COLLECTION_LIFECYCLE.md`](COLLECTION_LIFECYCLE.md); for algorithmic rules, see
[`TREND_ENGINE.md`](TREND_ENGINE.md).

---

## `compass.db`: raw corpus tables

### `runs` — запуски

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | TEXT PK | `{date}:{profile}`, e.g. `2026-07-28:broad` |
| `snapshot_date` | TEXT | `YYYY-MM-DD` |
| `profile` | TEXT | `ai-native`, `broad` |
| `status` | TEXT | `complete`, `partial`, `running`, `failed` |
| `started_at` | TEXT | ISO-8601 UTC |
| `finished_at` | TEXT | ISO-8601 UTC |
| `schema_version` | INTEGER | `2` или `3` |

**Unique**: `(snapshot_date, profile)`

---

### `items` — материалы (нормализованные)

| Column | Type | Description |
|--------|------|-------------|
| `item_id` | TEXT PK | `{provider}:{external_id}` или `sha256(url)[:24]` |
| `provider` | TEXT | `reddit`, `hackernews`, `bbc`, `techcrunch`, `nytimes`... |
| `source_cluster` | TEXT | `voices`, `developers`, `mainstream`, `business`, `tech_culture`, `product_pulse` |
| `external_id` | TEXT | Native ID или hash |
| `canonical_url` | TEXT | URL без tracking params |
| `title` | TEXT | Оригинал |
| `summary_ru` | TEXT | Русский summary (LLM или empty) |
| `excerpt` | TEXT | Abstract/excerpt (не полный текст) |
| `author` | TEXT | Автор или empty |
| `published_at` | TEXT | ISO-8601 или NULL |
| `observed_at` | TEXT | Когда собран |
| `snapshot_date` | TEXT | `YYYY-MM-DD` |
| `language` | TEXT | `en`, `ru` |
| `content_scope` | TEXT | `headline`, `abstract`, `excerpt`, `full` |
| `source_section` | TEXT | Subreddit, feed name, section |
| `raw_engagement` | TEXT JSON | `{"score": 100, "comments": 42}` |
| `metadata` | TEXT JSON | Дополнительные поля |

**Indexes**: `(provider, published_at)`, `(source_cluster, published_at)`, `(snapshot_date)`, `(canonical_url)`

---

### `observations` — наблюдения per run

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | TEXT PK | FK → runs |
| `item_id` | TEXT PK | FK → items |
| `observed_at` | TEXT | ISO-8601 |
| `source_rank` | INTEGER | Позиция в source feed |
| `engagement_percentile` | REAL | 0–100, внутри provider |
| `score_delta` | REAL | Изменение score vs prev |
| `comments_delta` | REAL | Изменение comments vs prev |

**PK**: `(run_id, item_id)` · **Indexes**: `(item_id)`, `(run_id)`

---

### `stories` — сюжеты (кластеры материалов)

| Column | Type | Description |
|--------|------|-------------|
| `story_id` | TEXT PK | `story_` + sha256(canonical_key)[:20] |
| `canonical_key` | TEXT | 5 информативных токенов |
| `title` | TEXT | Заголовок первого item |
| `summary_ru` | TEXT | Русский summary |
| `theme_ids` | TEXT JSON | `["ai_agents", "labor"]` |
| `first_seen` | TEXT | Дата первого появления |
| `last_seen` | TEXT | Дата последнего появления |
| `item_ids` | TEXT JSON | `["reddit:abc", "hackernews:123"]` |

**Index**: `(canonical_key)`

---

### `story_items` — связь story ↔ item

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | TEXT PK | FK → runs |
| `story_id` | TEXT PK | FK → stories |
| `item_id` | TEXT PK | FK → items |

**PK**: `(run_id, story_id, item_id)`

---

### `story_metrics` — метрики сюжета per run

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | TEXT PK | FK → runs |
| `story_id` | TEXT PK | FK → stories |
| `goal_relevance` | REAL | 0–100 |
| `cross_source_coverage` | REAL | 0–100 |
| `momentum` | REAL | 0–100 |
| `novelty` | REAL | 0–100 |
| `evidence_quality` | REAL | 0–100 |
| `trend_score` | REAL | Weighted sum (0–100) |
| `confidence` | TEXT | `low`, `medium`, `high` |
| `direction` | TEXT | `new`, `growing`, `stable`, `fading`, `resurfacing` |
| `item_count` | INTEGER | Materials in story |
| `source_count` | INTEGER | Distinct providers |

**PK**: `(run_id, story_id)` · **Index**: `(run_id, trend_score DESC)`

**trend_score formula**:
```
0.30 × goal_relevance + 0.25 × cross_source_coverage +
0.20 × momentum + 0.15 × novelty + 0.10 × evidence_quality
```

---

### `item_signals` — LLM-разметка

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | TEXT PK | FK → runs |
| `item_id` | TEXT PK | FK → items |
| `theme_ids` | TEXT JSON | Stable theme IDs |
| `candidate_themes` | TEXT JSON | Новые кандидаты |
| `pain_points` | TEXT JSON | Боли |
| `buying_intent` | INTEGER | 0/1 |
| `goal_relevance` | TEXT JSON | `{"book": 80, "rbc": 60}` |
| `summary_ru` | TEXT | LLM summary |
| `evidence_scope` | TEXT | Content scope при анализе |
| `model` | TEXT | `qwen3.7-plus` etc |
| `analyzed_at` | TEXT | ISO-8601 |

**PK**: `(run_id, item_id)`

---

### `briefings` — JSON briefing per run

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | TEXT PK | FK → runs |
| `schema_version` | INTEGER | 1 |
| `briefing_json` | TEXT | Полный JSON briefing |
| `created_at` | TEXT | ISO-8601 |

**PK**: `(run_id, schema_version)`

---

### `source_health` — статус источников per run

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | TEXT PK | FK → runs |
| `source_id` | TEXT PK | `reddit`, `hackernews`, `bbc`... |
| `provider` | TEXT | Provider name |
| `cluster` | TEXT | Source cluster |
| `status` | TEXT | `ok`, `empty`, `error`, `skipped`, `not_configured` |
| `count` | INTEGER | Items collected |
| `duration_sec` | REAL | Seconds |
| `error_code` | TEXT | Exception class or NULL |
| `message` | TEXT | Error message |

**PK**: `(run_id, source_id)`

---

### `research_state` — пользовательские заметки

| Column | Type | Description |
|--------|------|-------------|
| `story_id` | TEXT PK | FK → stories |
| `saved` | INTEGER | 0/1 |
| `status` | TEXT | `unread`, `read`, `in_progress`, `archived`, `dismissed` |
| `note` | TEXT | Free text, max 5000 chars |
| `updated_at` | TEXT | ISO-8601 |

---

## `trend_engine.db`: immutable analysis and publication

The Engine database deliberately duplicates the rows needed for analysis. A `DataRelease` is a
full frozen copy of its selected raw rows; a later collector run cannot change its checksum or a
previous result. Every Facet/Story/Trend run has a parent ID, method/parameter hash, Git SHA,
metrics and status (`building`, `evaluated`, `rejected`, `published`).

| Table group | Main tables | What it records |
|---|---|---|
| Frozen input | `data_releases`, `release_items`, `release_observations`, `release_source_health` | Release ID, input/checksums, complete/partial status, copied URLs (canonical/target/discussion), source coverage and source health. SQLite triggers block mutation of finalized release rows. |
| Facets | `facet_releases`, `item_facets` | Stable domains/themes, pains, typed entities, event frames, project relevance, Russian summaries and evidence scope. |
| Stories | `story_releases`, `story_candidate_pairs`, `engine_stories`, `engine_story_items`, `story_redirects` | Candidate features/decision provenance, constrained membership, cross-source counts and merge/split redirects. |
| Trends | `trend_releases`, `engine_trends`, `engine_trend_stories` | A repeated pattern across distinct stories, lifecycle/history status, evidence story IDs, counterpoints, confidence and source scope. |
| Human/LLM review | `engine_labels`, `llm_reviews` | Version-scoped manual labels and validated bounded-Qwen responses. Invalid JSON/evidence never becomes a merge. |
| Quality/embeddings | `engine_quality_reports`, `embedding_vectors`, `item_embedding_refs` | Quality-gate audit outcome plus cached local vector provenance. |
| Publication | `radar_publications`, `published_channels`, `publication_history` | Immutable Story+Trend combination and the single channel pointer used by UI. Rollback changes only this pointer. |
| Reddit-native signals | `signal_releases`, `community_signals` | Separate community/Pulse layer: within-subreddit percentile, velocity, discussion depth and perspective gap. |
| Compatibility | `legacy_lab_imports` | Provenance when an older Cluster Lab record was safely imported; it is never treated as a current publication. |

### Version graph and status semantics

```text
DataRelease (finalized + checksum)
  └─ FacetRelease (evaluated)
       └─ StoryRelease (evaluated/reviewed)
            └─ TrendRelease (evaluated/reviewed)
                 └─ RadarPublication (manual)
                      └─ published_channels["broad" | "ai-native" | "shadow"]
```

- `input_status=complete` means the selected raw collection reached all expected source inputs;
  `partial` is available only for inspect/preview/shadow, never silently promoted to Broad.
- A quality report is keyed by **Data + Story + Trend** release, so `/runs` can show the stored
  outcome without recomputing an expensive historic taxonomy during a web request.
- The UI reads a publication pointer, not the most recently created experiment. If a new attempt
  fails quality or Qwen review, the previous good publication stays available.

---

### Legacy таблицы (v1, не используются в v2/v3)

| Table | Description |
|-------|-------------|
| `snapshots` | v1 snapshot metadata |
| `posts` | v1 posts (заменено `items`) |
| `comments` | v1 comments |
| `virality_signals` | v1 virality detection |
| `tracked_threads` | v1 thread tracking |

---

## Полезные запросы

### Топ сюжеты за дату
```sql
SELECT s.title, sm.trend_score, sm.direction, sm.source_count, sm.item_count
FROM story_metrics sm
JOIN stories s ON sm.story_id = s.story_id
WHERE sm.run_id = '2026-07-28:broad'
ORDER BY sm.trend_score DESC LIMIT 10;
```

### Cross-source сюжеты (3+ providers)
```sql
SELECT s.title, GROUP_CONCAT(DISTINCT i.provider) as providers, COUNT(DISTINCT i.provider) as pc
FROM stories s
JOIN story_items si ON s.story_id = si.story_id
JOIN items i ON si.item_id = i.item_id
WHERE si.run_id = '2026-07-28:broad'
GROUP BY s.story_id HAVING pc >= 3
ORDER BY pc DESC;
```

### Растущие сюжеты с историей
```sql
SELECT s.title, sm.direction, sm.trend_score, sm.item_count, s.first_seen
FROM story_metrics sm
JOIN stories s ON sm.story_id = s.story_id
WHERE sm.direction IN ('growing', 'resurfacing')
  AND sm.run_id = '2026-07-28:broad'
ORDER BY sm.trend_score DESC;
```

### Coverage по provider за дату
```sql
SELECT provider, COUNT(*) as items,
       COUNT(DISTINCT source_cluster) as clusters
FROM items WHERE snapshot_date = '2026-07-28'
GROUP BY provider ORDER BY items DESC;
```

### Сюжеты с LLM pain points
```sql
SELECT s.title, iss.pain_points, iss.goal_relevance
FROM item_signals iss
JOIN story_items si ON iss.item_id = si.item_id AND iss.run_id = si.run_id
JOIN stories s ON si.story_id = s.story_id
WHERE iss.pain_points != '[]'
  AND iss.run_id = '2026-07-28:broad'
LIMIT 20;
```

### Сохранённые сюжеты с заметками
```sql
SELECT rs.status, rs.note, s.title, sm.trend_score
FROM research_state rs
JOIN stories s ON rs.story_id = s.story_id
LEFT JOIN story_metrics sm ON s.story_id = sm.story_id
  AND sm.run_id = (SELECT run_id FROM runs ORDER BY snapshot_date DESC LIMIT 1)
WHERE rs.saved = 1;
```

### Timeline сюжета (история по датам)
```sql
SELECT r.snapshot_date, sm.trend_score, sm.direction,
       sm.item_count, sm.source_count, sm.confidence
FROM story_metrics sm
JOIN runs r ON sm.run_id = r.run_id
WHERE sm.story_id = 'story_abc123'
ORDER BY r.snapshot_date;
```

---

## Миграции

| Version | Changes |
|---------|---------|
| 0→1 | Legacy tables: snapshots, posts, comments, virality_signals, tracked_threads |
| 1→2 | Intelligence tables: runs, items, observations, stories, story_items, story_metrics, item_signals, briefings, research_state, source_health |
| 2→3 | Added: `domain_ids`, `trend_id`, `lifecycle`, `project_scores` columns |

Raw-corpus migrations are idempotent through `PRAGMA user_version`. Engine schema initialization
is also idempotent, but finalization triggers preserve immutable releases. Use
`reddit-compass db rebuild` only for documented legacy recovery; use `engine release create`,
then a new Facet/Story/Trend attempt for analytical iterations.
