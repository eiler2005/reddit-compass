# SQLite Schema Reference — reddit-compass

> Автогенерировано из production БД. Последнее обновление: 2026-07-28.
> Файл: `data/compass.db` · Engine: SQLite 3 · WAL mode

## Обзор

```
sources → items → observations → stories → story_metrics → briefings
                         ↓              ↓
                    story_items    item_signals
                         ↓
                   research_state
```

**14 таблиц**, ~50K rows на VPS (7 дат, 9K items, 14K stories).

---

## Таблицы

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

Миграции идемпотентны через `PRAGMA user_version`. Rebuild: `reddit-compass db rebuild`.
