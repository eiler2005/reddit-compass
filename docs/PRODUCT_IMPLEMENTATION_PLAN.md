# reddit-compass: подробный план продуктовой реализации

> Статус: handoff-спецификация для реализации другой LLM.
>
> Документ описывает целевое поведение, контракты, порядок изменений, тесты и критерии
> приёмки. Реализатор не должен принимать скрытые продуктовые решения самостоятельно:
> если фактическое состояние репозитория противоречит этому документу, работу следует
> остановить и зафиксировать противоречие.
>
> Детальный аудит текущих `/dashboard`, `/runs/{date}` и `/runs/{date}/radar`, обязательное
> сохранение source coverage, мега-трендов, LLM-облаков, а также готовые versioned prompts
> находятся в `docs/CURRENT_UI_RADAR_REVISION_PLAN.md`. Для UI/Radar эта спецификация имеет
> приоритет.

## 0. Как работать с этим документом

Цель реализации — превратить reddit-compass из набора сборщиков и отчётов в персональный
редакционный радар с двумя основными сценариями:

1. **Утренний бриф:** за 5–10 минут понять, какие сюжеты появились, растут или возвращаются,
   почему они важны и какими источниками подтверждаются.
2. **Research workspace:** найти материалы по теме, сравнить источники, сохранить сюжет,
   добавить заметку и перевести его в рабочий статус.

Реализацию выполнять последовательно, по фазам из этого документа. Нельзя начинать новый
этап, пока не выполнены acceptance criteria предыдущего.

### Обязательный процесс

1. Перед изменениями прочитать `AGENTS.md`, `README.md`, `ARCHITECTURE.md`, `ROADMAP.md`.
2. Проверить `git status --short` и сохранить все пользовательские изменения.
3. Не читать и не печатать `.env`, `.env.secrets`, токены или действующие credentials.
4. Не изменять `deploy/**`, если конкретный этап этого явно не требует.
5. Не коммитить, не пушить и не деплоить без отдельного разрешения пользователя.
6. Для каждого этапа добавлять поведенческие тесты до перехода к следующему.
7. Перед сдачей выполнить:

   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy src
   uv run pytest
   ```

### Запрещённые упрощения

- Не заменять story clustering простым выводом top Reddit posts.
- Не сравнивать абсолютные score разных платформ.
- Не использовать Markdown как источник данных для HTML.
- Не добавлять фиктивные NYT/WSJ данные или mock-источники в production.
- Не считать title-only материал прочитанной статьёй.
- Не создавать новый theme автоматически только потому, что его предложила LLM.
- Не удалять API v1 и legacy JSONL в рамках первой реализации.
- Не ослаблять тесты или coverage gate для прохождения CI.

---

## 1. Проверенный baseline

На момент составления плана:

- Ruff, format check и mypy проходят.
- Pytest: 86 тестов; текущая измеряемая coverage около 79.6%.
- Из coverage целиком исключены CLI, LLM, источники и веб-интерфейс.
- `all/nightly` обрабатывает преимущественно Reddit; HN, RSS, Ladder и ProductHunt запускаются
  отдельными командами.
- `/dashboard` читает SQLite, но production pipeline не вызывает `save_snapshot`.
- Исторические страницы читают JSONL напрямую.
- `PostCard` не содержит явного provider/source cluster/content scope.
- `SignalCard` не содержит URL и надёжного source type.
- `/api/v1/signals` возвращает virality-сигналы, а UI словом «signals» называет LLM-анализ.
- Ограничение `signals --top N` сортирует все платформы по raw score.
- RSS и Ladder обычно имеют score `0`, поэтому вытесняются Reddit/HN.
- Trend history сопоставляет точные LLM-строки; близкие формулировки не объединяются.
- RSS/Ladder в части trend logic могут ошибочно классифицироваться как Reddit.
- Radar HTML читает structured signals, но run-level synthesis извлекает из Markdown.
- Dashboard содержит hardcoded source counts и списки источников.
- Ladder adapter содержит меньше источников, чем заявлено в документации, и не поддерживает
  WSJ.
- Внешние строки рендерятся в HTML без системного escaping.
- В tracked-документации присутствует credential-like Basic Auth значение. Его нельзя
  копировать в новые файлы; значение нужно удалить и ротировать отдельным безопасным действием.

Этот baseline должен быть превращён в regression tests до удаления legacy-путей.

---

## 2. Целевая архитектура

```text
source adapters
      │
      ▼
ContentItem + Observation
      │
      ├── JSONL artifacts (exchange/source of recovery)
      │
      ▼
SQLite projection
      │
      ▼
dedupe → story clustering → metrics/ranking
      │
      ▼
item signals → structured briefing.json
      │
      ├── briefing.md
      ├── Telegram notification
      ├── API v2
      └── Jinja web UI
```

### Архитектурные инварианты

- JSONL остаётся переносимым форматом обмена и восстановления.
- SQLite является query projection, а не единственной копией данных.
- Любую SQLite-базу можно удалить и восстановить из snapshots.
- UI, Markdown и notification используют один `briefing.json`.
- Source adapters ничего не знают о Jinja, API или editorial ranking.
- Story ranking ничего не знает о способе сетевого доступа к источнику.
- Все выводы LLM ссылаются на существующие `item_id`.
- Source health отделён от editorial briefing.

---

## 3. Фазы реализации

| Фаза | Результат | Не начинать следующую фазу, пока |
|---|---|---|
| A | Source-agnostic domain models и compatibility layer | legacy JSONL корректно преобразуется |
| B | SQLite v2 и rebuild | БД идемпотентно восстанавливается |
| C | Story clustering, metrics и structured briefing | mixed-source fixture даёт ожидаемый radar |
| D | Unified run и source registry | отдельные команды объединяются в один run |
| E | LLM validation и evidence contracts | невалидные/негрунтованные ответы отклоняются |
| F | Раздельные UI: `/today`, полный Radar, story, explore, runs | mobile/desktop и XSS tests проходят |
| G | API v2 и research state | v1 остаётся совместимым |
| H | Official-first sources | NYT работает по ключу, WSJ честно not configured |
| I | Документация, shadow rollout и финальная проверка | все acceptance criteria выполнены |

---

## 4. Фаза A — source-agnostic domain model

### 4.1 Новые типы

Создать модуль `src/reddit_compass/intelligence/models.py`.

Использовать `dataclass` для внутренних immutable value objects и Pydantic для API/LLM
validation. Не заменять существующий `PostCard` сразу: он остаётся legacy contract.

#### Enums

```python
SourceCluster = Literal[
    "voices",
    "developers",
    "mainstream",
    "business",
    "tech_culture",
    "product_pulse",
    "search_interest",
]

ContentScope = Literal["headline", "abstract", "excerpt", "full"]

StoryDirection = Literal["new", "growing", "stable", "fading", "resurfacing"]

ConfidenceLevel = Literal["low", "medium", "high"]

ResearchStatus = Literal["unread", "read", "in_progress", "archived", "dismissed"]
```

#### `ContentItem`

Обязательные поля:

| Поле | Тип | Правило |
|---|---|---|
| `item_id` | `str` | стабильный ID `provider:external_id` |
| `provider` | `str` | `reddit`, `hackernews`, `nytimes`, `wired` и т. п. |
| `source_cluster` | `SourceCluster` | берётся только из registry |
| `external_id` | `str` | native ID или hash canonical URL |
| `canonical_url` | `str` | только `http/https`, очищен от tracking query |
| `title` | `str` | оригинальный title |
| `summary_ru` | `str` | может быть пустым до анализа |
| `excerpt` | `str` | разрешённый abstract/excerpt, не полный paywalled текст |
| `author` | `str` | пустая строка допустима |
| `published_at` | `str | None` | UTC ISO-8601 |
| `observed_at` | `str` | UTC ISO-8601 |
| `snapshot_date` | `str` | `YYYY-MM-DD` |
| `language` | `str` | BCP-47-like short code, default `en` |
| `content_scope` | `ContentScope` | честно отражает доступный контент |
| `source_section` | `str` | subreddit, section или feed name |
| `raw_engagement` | `dict[str, float]` | platform-native values |
| `metadata` | `dict[str, Any]` | безопасные дополнительные поля |

Правила:

- В `raw_engagement` Reddit хранит score/comments/upvote ratio, HN — points/comments,
  ProductHunt — votes/comments, media может оставить `{}`.
- `metadata` не содержит secrets, cookies, Authorization headers или полного платного текста.
- `canonical_url` не содержит `utm_*`, `fbclid`, `gclid`, fragments.
- Reddit permalink преобразуется в полный canonical URL.
- Если native ID отсутствует, `external_id = sha256(canonical_url)[:24]`.

#### `Observation`

```python
run_id: str
item_id: str
observed_at: str
source_rank: int | None
engagement_percentile: float
score_delta: float | None
comments_delta: float | None
```

`engagement_percentile` всегда `0..100` и считается только внутри одного provider в одном run.

#### `ItemSignal`

```python
item_id: str
theme_ids: list[str]
candidate_themes: list[str]
pain_points: list[str]
buying_intent: bool
goal_relevance: dict[str, int]
summary_ru: str
evidence_scope: ContentScope
model: str
analyzed_at: str
```

`goal_relevance` использует ключи активного профиля, например `book`, `rbc`, `business`.
Каждое значение валидируется в диапазоне `0..100`.

#### `EvidenceRef`

```python
item_id: str
provider: str
source_cluster: SourceCluster
url: str
title: str
excerpt: str
content_scope: ContentScope
```

#### `Story`

```python
story_id: str
canonical_key: str
title: str
summary_ru: str
theme_ids: list[str]
first_seen: str
last_seen: str
item_ids: list[str]
```

`story_id` не зависит от даты run и сохраняется при продолжении сюжета.

#### `StoryMetric`

```python
run_id: str
story_id: str
goal_relevance: float
cross_source_coverage: float
momentum: float
novelty: float
evidence_quality: float
trend_score: float
confidence: ConfidenceLevel
direction: StoryDirection
item_count: int
source_count: int
```

#### `BriefingStory`

```python
story: Story
metric: StoryMetric
why_it_matters: str
evidence: list[EvidenceRef]
score_breakdown: dict[str, float]
```

#### `Briefing`

```python
schema_version: int
run_id: str
date: str
profile: str
status: Literal["complete", "partial"]
generated_at: str
source_health: list[SourceHealth]
top_changes: list[BriefingStory]
watchlist: list[BriefingStory]
pain_points: list[GroundedText]
column_ideas: list[GroundedText]
narrative_shifts: list[GroundedText]
```

`GroundedText`:

```python
text: str
evidence_ids: list[str]
```

### 4.2 Compatibility adapter

Создать `src/reddit_compass/intelligence/compat.py`.

Он обязан:

- Преобразовывать `PostCard` в `ContentItem`.
- Определять provider по имени legacy-файла, а не по subreddit.
- Использовать следующую таблицу:

  | Legacy file | Provider group | Cluster |
  |---|---|---|
  | `posts.jsonl` | `reddit` | `voices` |
  | `keyword-search.jsonl` | `reddit` | `voices` |
  | `hackernews.jsonl` | `hackernews` | `developers` |
  | `rss.jsonl` | provider из `subreddit` | registry |
  | `ladder.jsonl` | provider из `subreddit` | registry |
  | `producthunt.jsonl` | `producthunt` | `product_pulse` |

- Не определять RSS/Ladder как Reddit по принципу «subreddit непустой».
- Для legacy RSS/Ladder ставить `content_scope="headline"`, если excerpt отсутствует.
- Для Reddit selftext использовать максимум как `excerpt`, но не включать comments в него.
- Пропускать битую JSONL-строку с диагностикой и счётчиком, а не падать всем rebuild.

### 4.3 Acceptance criteria фазы A

- Все пять legacy source families преобразуются в `ContentItem`.
- Один и тот же URL с tracking parameters получает одинаковый canonical URL.
- RSS/Ladder никогда не маркируются Reddit.
- В output отсутствуют secrets и полные paywalled bodies.
- Добавлены unit tests для всех mapping rules.

---

## 5. Фаза B — SQLite v2 и rebuild

### 5.1 Миграционная стратегия

Существующие таблицы `snapshots`, `posts`, `comments`, `virality_signals`,
`tracked_threads` не переименовывать и не удалять.

Добавить миграции через `PRAGMA user_version`:

- `0/1 → 2`: создать новые intelligence tables.
- Каждая миграция выполняется в транзакции.
- Повторное открытие БД ничего не меняет.
- При ошибке version не увеличивается.

### 5.2 Новые таблицы

Минимальный набор:

```text
runs
items
observations
stories
story_items
story_metrics
item_signals
briefings
research_state
source_health
```

#### `runs`

```sql
run_id TEXT PRIMARY KEY
snapshot_date TEXT NOT NULL
profile TEXT NOT NULL
status TEXT NOT NULL
started_at TEXT NOT NULL
finished_at TEXT
schema_version INTEGER NOT NULL
UNIQUE(snapshot_date, profile)
```

#### `items`

`item_id` — primary key. Остальные поля соответствуют `ContentItem`.
Сложные значения сохраняются JSON text с `ensure_ascii=False`.

Индексы:

- `(provider, published_at)`
- `(source_cluster, published_at)`
- `(snapshot_date)`
- `(canonical_url)`

#### `observations`

Primary key `(run_id, item_id)`.
Индексы по `item_id` и `run_id`.

#### `stories`

`story_id` — primary key; `canonical_key` indexed.

#### `story_items`

Primary key `(run_id, story_id, item_id)`.

#### `story_metrics`

Primary key `(run_id, story_id)`.
Индекс `(run_id, trend_score DESC)`.

#### `item_signals`

Primary key `(run_id, item_id)`.

#### `briefings`

Одна JSON-запись на `(run_id, schema_version)`.

#### `research_state`

Одна single-user запись на `story_id`:

```sql
story_id TEXT PRIMARY KEY
saved INTEGER NOT NULL DEFAULT 0
status TEXT NOT NULL DEFAULT 'unread'
note TEXT NOT NULL DEFAULT ''
updated_at TEXT NOT NULL
```

### 5.3 Projection functions

Добавить отдельный repository layer, не раздувать существующий `db.py`:

```text
src/reddit_compass/intelligence/repository.py
src/reddit_compass/intelligence/migrations.py
```

Обязательные функции:

```python
migrate(conn) -> None
upsert_run(conn, run) -> None
upsert_items(conn, items) -> None
upsert_observations(conn, observations) -> None
replace_run_stories(conn, run_id, stories, metrics) -> None
replace_run_signals(conn, run_id, signals) -> None
save_briefing(conn, briefing) -> None
get_briefing(conn, date, profile) -> Briefing | None
query_stories(conn, filters, pagination) -> Page[StoryView]
get_story(conn, story_id) -> StoryDetail | None
update_research_state(conn, story_id, patch) -> ResearchState
```

### 5.4 Rebuild

CLI:

```bash
reddit-compass db rebuild --from-snapshots
reddit-compass db rebuild --from-snapshots --date 2026-07-27
```

Алгоритм:

1. Найти только директории `snapshots/YYYY-MM-DD`.
2. Сортировать по дате по возрастанию.
3. Для даты загрузить все известные legacy и v2 artifacts.
4. Создать/обновить run.
5. Нормализовать items.
6. Пересчитать observations, stories и metrics.
7. Если существует `briefing.json`, импортировать его после validation.
8. Если briefing отсутствует, создать deterministic non-LLM briefing.
9. Commit выполняется отдельно для каждой даты.
10. Повторный rebuild не создаёт дублей и не стирает `research_state`.

### 5.5 Acceptance criteria фазы B

- Пустая БД создаётся с user_version `2`.
- Legacy API v1 продолжает читать старые таблицы.
- Rebuild дважды даёт одинаковые counts и IDs.
- Повреждённый snapshot помечается partial, остальные даты импортируются.
- `research_state` переживает rebuild.

---

## 6. Фаза C — story clustering и ranking

### 6.1 Нормализация заголовков

Создать `intelligence/clustering.py`.

Нормализация:

1. Unicode NFKC.
2. Lowercase.
3. Удалить punctuation и повторные spaces.
4. Удалить URL и publisher suffix после `|`, если suffix совпадает с provider.
5. Токены короче 3 символов исключить, кроме `ai`.
6. Удалить небольшой RU/EN stopword set, хранимый в коде.
7. Числа, суммы и имена компаний сохранить: они важны для событий.

Добавить dependency `rapidfuzz>=3,<4`.

### 6.2 Порядок clustering

Для каждого нового item:

1. **Exact canonical URL:** совпадение URL → существующий story.
2. **Cross-post canonical target:** Reddit/HN ссылки на один внешний URL → один story.
3. **Title match:** сравнить только со stories, наблюдавшимися в последние 14 дней.
4. Рассчитать:

   ```text
   token_jaccard = intersection(tokens) / union(tokens)
   fuzzy_ratio = rapidfuzz.token_set_ratio / 100
   similarity = 0.6 * token_jaccard + 0.4 * fuzzy_ratio
   ```

5. Match, если:

   - `similarity >= 0.72`; или
   - `similarity >= 0.62` и совпадает хотя бы один entity-like token:
     company/person name, число, валюта или acronym.

6. Если совпало несколько stories, выбрать максимальный similarity.
7. При разнице между двумя лучшими кандидатами `< 0.03` не объединять автоматически:
   создать новый story и записать ambiguity counter.

`story_id`:

- Для нового story: `story_` + первые 20 символов SHA-256 от canonical key.
- Canonical key строится из стабильных пяти наиболее информативных tokens первого item.
- После создания story ID никогда не пересчитывается.

### 6.3 Stable themes

Расширить `config/profiles/ai-native.json`:

```json
{
  "schema_version": 2,
  "goals": {
    "book": {"label": "Книга", "weight": 1.0},
    "rbc": {"label": "Колонки РБК", "weight": 1.0},
    "business": {"label": "Бизнес-сигнал", "weight": 0.7}
  },
  "themes": [
    {"id": "ai_agents", "label": "AI-агенты", "keywords": ["agent", "агент"]},
    {"id": "labor", "label": "Рынок труда", "keywords": ["layoff", "jobs", "увольнен"]},
    {"id": "regulation", "label": "Регулирование", "keywords": ["regulation", "law", "закон"]},
    {"id": "surveillance", "label": "Слежка и приватность", "keywords": ["privacy", "camera", "surveillance"]}
  ]
}
```

Сохранить поддержку profile schema v1:

- При отсутствии `schema_version` считать v1.
- Существующие subreddits/settings загружаются без изменения.
- Goals/themes получают documented defaults.

### 6.4 Percentiles

Для каждого provider внутри run:

```text
engagement_value =
  reddit: log1p(score) + 0.5 * log1p(comments)
  hackernews: log1p(points) + 0.5 * log1p(comments)
  producthunt: log1p(votes) + 0.5 * log1p(comments)
  media: 0
```

`engagement_percentile` — percentile rank внутри provider.

Для media отсутствие engagement не означает нулевую ценность; momentum story строится также
по числу новых публикаций и росту source coverage.

### 6.5 Компоненты ranking

Все компоненты `0..100`.

#### Goal relevance

- Если есть item signals: weighted average активных goals по всем items story.
- Без LLM: keyword/profile relevance, ограниченная максимум `60`, чтобы deterministic fallback
  не притворялся полной редакционной оценкой.

#### Cross-source coverage

Считать число независимых clusters:

```text
1 cluster  -> 25
2 clusters -> 55
3 clusters -> 75
4 clusters -> 90
5+         -> 100
```

Несколько публикаций одного provider не увеличивают cluster count.

#### Momentum

```text
momentum =
  0.50 * median_top_engagement_percentile +
  0.30 * normalized_new_items_delta +
  0.20 * normalized_source_delta
```

Если предыдущего run нет, delta-компоненты равны `50`, а direction определяется как `new`.

#### Novelty

```text
first_seen today      -> 100
first_seen 1-2d ago   -> 80
first_seen 3-6d ago   -> 55
first_seen 7-13d ago  -> 30
older                 -> 10
resurfacing >14d gap  -> 75
```

#### Evidence quality

По каждому item:

```text
headline = 25
abstract = 50
excerpt  = 75
full     = 100
```

Story value — среднее двух лучших независимых providers. Один provider ограничивает значение
максимум `60`.

#### Итог

```text
trend_score =
  0.30 * goal_relevance +
  0.25 * cross_source_coverage +
  0.20 * momentum +
  0.15 * novelty +
  0.10 * evidence_quality
```

#### Confidence

- `high`: минимум два независимых providers и evidence quality `>= 60`.
- `medium`: два providers либо один provider с evidence quality `>= 75`.
- `low`: всё остальное.

Confidence не смешивается с trend score.

#### Direction

- `new`: first seen в текущем run.
- `resurfacing`: не было observations минимум 14 дней, затем появилось снова.
- `growing`: item count или source count выросли минимум на 30%.
- `fading`: оба показателя снизились минимум на 30%.
- `stable`: остальные случаи.

### 6.6 Deterministic briefing

Даже без Qwen система обязана создать корректный `briefing.json`:

- `top_changes`: первые пять stories по trend score, где direction `new/growing/resurfacing`.
- `watchlist`: следующие десять `stable/growing`.
- `why_it_matters`: безопасная шаблонная строка без фактических утверждений сверх evidence.
- `pain_points`, `column_ideas`, `narrative_shifts`: пустые массивы без LLM.
- `status=partial`, если отсутствует хотя бы один expected source.

### 6.7 Acceptance criteria фазы C

- Одна новость из Reddit, HN, NYT и FT становится одним story.
- Два разных события одной компании не склеиваются только из-за названия компании.
- Media item со score `0` может войти в top story.
- Raw Reddit score не используется напрямую в cross-source ranking.
- Story ID сохраняется между соседними датами.
- Direction и novelty покрыты table-driven tests.

---

## 7. Фаза D — unified run и source registry

### 7.1 Source registry

Создать `src/reddit_compass/sources/registry.py`.

`SourceDefinition`:

```python
source_id: str
provider: str
label: str
cluster: SourceCluster
access: Literal["reddit", "api", "rss", "ladder", "manual"]
country: str
language: str
default_scope: ContentScope
expected_freshness_hours: int
requires_env: tuple[str, ...]
enabled_by_default: bool
```

Registry должен включить фактически поддерживаемые источники. UI и документация получают
source labels только из registry.

Не заявлять source enabled, если его adapter отсутствует или required env не установлен.

### 7.2 Profile selection

Профиль определяет:

```json
{
  "sources": {
    "enabled": ["reddit", "hackernews", "bbc", "guardian", "nytimes", "producthunt"],
    "optional": ["ft", "wired", "wsj"],
    "expected_for_complete_run": ["reddit", "hackernews", "rss", "producthunt"]
  }
}
```

Legacy profile без `sources` использует текущие defaults.

### 7.3 Новая CLI-команда

```bash
reddit-compass run
reddit-compass run --sources reddit,hn,rss,ladder,ph
reddit-compass run --profile ai-native
reddit-compass run --sources rss,hn --allow-partial
reddit-compass run --analyze
```

Поведение:

1. Создать или открыть run для `(UTC date, profile)`.
2. Запустить только запрошенные adapters.
3. Каждый adapter пишет собственный legacy artifact и обновляет manifest.
4. После каждого adapter обновить normalized `items.jsonl` и SQLite projection.
5. После последнего adapter пересчитать stories/metrics.
6. `--analyze` запускает Qwen и пересоздаёт structured briefing.
7. Без `--analyze` создаётся deterministic briefing.
8. `--allow-partial` разрешает successful exit при недоступных expected sources, но status остаётся
   `partial`.

Старые команды остаются алиасами:

- `fetch` → `run --sources reddit`
- `hn` → `run --sources hn`
- `rss` → `run --sources rss`
- `ladder` → `run --sources ladder`
- `ph` → `run --sources ph`

Они не должны перезаписывать результаты других adapters той же даты.

### 7.4 Manifest v2

`run-manifest.json`:

```json
{
  "schema_version": 2,
  "run_id": "2026-07-27:ai-native",
  "date": "2026-07-27",
  "profile": "ai-native",
  "status": "partial",
  "started_at": "...",
  "finished_at": "...",
  "sources": {
    "reddit": {
      "status": "ok",
      "count": 1600,
      "duration_sec": 680,
      "content_scope": "excerpt",
      "last_success_at": "...",
      "error_code": null,
      "message": ""
    }
  }
}
```

Правила:

- `sources` — map по stable source ID, не append-only list.
- Обновление выполняется atomic write через временный файл + replace.
- При существующем manifest обновляется только текущий source.
- Error message sanitised и не содержит URL credentials.
- Complete определяется по `expected_for_complete_run`, а не по hardcoded числу.

### 7.5 Acceptance criteria фазы D

- `fetch`, затем `rss`, затем `hn` создают один run и сохраняют результаты всех трёх.
- Повтор одного adapter обновляет только его artifact/status.
- Manifest корректен при parallel-like последовательных обновлениях.
- `/runs` впоследствии может показать expected/actual без hardcoded source list.

---

## 8. Фаза E — LLM selection, validation и evidence

### 8.1 Стратифицированный отбор

Заменить global raw-score sort.

Для лимита `N`:

1. Выделить `70% N` равномерно между активными source clusters.
2. Внутри cluster ранжировать по:

   ```text
   0.50 * engagement_percentile +
   0.35 * deterministic_goal_relevance +
   0.15 * freshness
   ```

3. `20% N` распределить глобально по этому же normalized score.
4. `10% N` — deterministic exploration:
   - items ниже top quota;
   - равномерно по clusters;
   - seed строится из snapshot date, чтобы тесты воспроизводились.
5. Один provider не может занимать более `40% N`, если активны минимум три providers.

### 8.2 Pydantic LLM contracts

Создать `intelligence/llm_schemas.py`.

Запрещено собирать `SignalCard` напрямую из произвольного dict.

Validation:

- `item_id` обязан быть во входном batch.
- Relevance `0..100`.
- Максимум 5 pain points и 5 theme IDs на item.
- `theme_ids` должны существовать в active profile.
- Неизвестные темы переходят только в `candidate_themes`.
- Summary максимум 500 символов.
- Evidence IDs run-level synthesis обязаны существовать в предоставленном corpus.

### 8.3 Retry policy

- Первая попытка: основная модель.
- При invalid JSON/schema: одна repair-попытка с validation errors.
- При HTTP 429/5xx: до двух retry с bounded backoff.
- После исчерпания попыток batch помечается failed; run продолжает работу как partial.
- Нельзя молча возвращать пустой successful analysis.

### 8.4 Scope-aware prompting

Каждый item передаётся с:

```json
{
  "item_id": "...",
  "provider": "nytimes",
  "content_scope": "abstract",
  "title": "...",
  "excerpt": "..."
}
```

System instruction:

- `headline`: можно пересказать только headline.
- `abstract`: нельзя утверждать детали, которых нет в abstract.
- `excerpt/full`: можно использовать только предоставленный текст.
- Нельзя ссылаться на внешние знания модели.
- Каждый run-level тезис обязан содержать evidence IDs.

### 8.5 Structured briefing

Сохранять `briefing.json` напрямую из validated Pydantic model.

`briefing.md`, web UI и Telegram не должны разбирать свободный Markdown.

`signals-report.md` сохранить как legacy generated output на один переходный релиз.

### 8.6 Narrative shifts

LLM не решает самостоятельно, что изменилось «за неделю».

Pipeline передаёт только вычисленные facts:

- story direction;
- item/source count delta;
- first/last seen;
- previous/current score;
- evidence IDs.

LLM превращает эти facts в редакционный текст. Если historical facts отсутствуют,
`narrative_shifts=[]`.

### 8.7 Acceptance criteria фазы E

- Unknown item/evidence ID отклоняется.
- Title-only input не порождает unsupported details в accepted output.
- Невалидный JSON проходит repair либо становится explicit failed batch.
- При partial LLM analysis deterministic briefing остаётся доступен.

---

## 9. Фаза F — новый web UI

### 9.1 Stack

Добавить:

```toml
jinja2 = ">=3.1,<4"
rapidfuzz = ">=3,<4"
```

Не добавлять React, Node build chain, Tailwind runtime или Streamlit.

Структура:

```text
src/reddit_compass/api/
├── app.py
├── v2.py
├── ui.py
├── view_models.py
├── templates/
│   ├── base.html
│   ├── today.html
│   ├── radar.html
│   ├── story.html
│   ├── explore.html
│   ├── runs.html
│   ├── empty.html
│   └── components/
│       ├── story_card.html
│       ├── evidence_chip.html
│       ├── source_health.html
│       └── pagination.html
└── static/
    ├── app.css
    └── app.js
```

Jinja autoescape должен быть включён. Не использовать `|safe` для content из sources/LLM.

### 9.2 `/today`

Query parameters:

```text
date=YYYY-MM-DD
profile=ai-native
```

Если date отсутствует — последний доступный briefing.

Порядок страницы:

1. Header/nav.
2. Date switcher: previous/current/next available date.
3. Freshness/status line.
4. `Что изменилось` — максимум пять story cards.
5. `Что прочитать сейчас` — максимум пять evidence links.
6. `В работе` — сохранённые stories со status `in_progress`.
7. Compact source status.
8. Явная ссылка `Открыть полный Trend Radar`.

Story card:

- title;
- Russian summary;
- direction label;
- trend score;
- confidence;
- why it matters;
- source cluster count;
- 2–3 evidence links;
- `<details>` score breakdown;
- research actions.

Не показывать raw score как универсальное место в рейтинге.

Today не дублирует theme clouds, trend-strength tables, полный Mega ranking, все pain points,
relevance tables, column ideas или raw feeds. Эти аналитические функции живут в Radar.

### 9.3 `/runs/{date}/radar`

Это отдельный полный analytics workspace, а не alias Today.

Сохранить и улучшить все текущие блоки:

1. Items, LLM-signals, themes/stories и pain-points KPI.
2. LLM top themes/editorial stories.
3. Column ideas.
4. Narrative shifts.
5. Pain points.
6. Top relevance для книги.
7. Отдельную relevance view для РБК и других goals.
8. LLM theme cloud.
9. Stable и emerging theme clouds.
10. Trend strength, novelty, source coverage и direction.
11. Story-level Mega trends через все источники.
12. Raw popularity как secondary collapsed block.
13. Source coverage/freshness.

`/radar` ведёт на Radar последнего доступного run. Исторический
`/runs/{date}/radar` остаётся canonical route и никогда не редиректится в `/today`.

Полная спецификация и prompts находятся в
`docs/CURRENT_UI_RADAR_REVISION_PLAN.md`.

### 9.4 `/stories/{story_id}`

Показать:

- title, summary, theme labels;
- why now;
- 1/7/30-day timeline из story metrics;
- score breakdown;
- evidence, сгруппированные по source cluster;
- original titles, excerpts, content scope и прямые URL;
- coverage gaps/counterpoints только при наличии grounded evidence;
- research state form.

Timeline в первой версии выполнить доступной HTML-таблицей + CSS bars. Не добавлять chart library.

### 9.5 `/explore`

Поддержать:

```text
q
date_from
date_to
theme
provider
source_cluster
direction
confidence
status
saved
sort
page
page_size
```

Defaults:

- `sort=trend_score`
- `page=1`
- `page_size=50`, максимум `100`

Поиск выполняется по title, summary, excerpt, pain points и research note.

Все фильтры отражаются в URL.

### 9.6 `/runs`

Показывать:

- complete/partial/running;
- profile/date;
- expected и successful sources;
- count/duration/freshness;
- sanitized error;
- copyable CLI-команду для ручного повтора.

Никаких start/retry buttons, изменяющих collector, в первой версии.

### 9.7 Legacy routes и redirects

- Не включать redirects до выполнения feature parity matrix из
  `docs/CURRENT_UI_RADAR_REVISION_PLAN.md` и семи shadow runs.
- После parity: `/dashboard` → `/today`.
- После parity новый renderer занимает `/runs/{date}/radar`.
- `/radar` → последний `/runs/{date}/radar`.
- Radar не редиректится в Today.
- Старые renderers оставить под `/legacy/dashboard` и
  `/legacy/runs/{date}/radar` минимум один переходный релиз.

### 9.8 Research actions

Single-user form endpoint:

```text
POST /ui/stories/{story_id}/research-state
```

Поля:

```text
saved: bool
status: ResearchStatus
note: str, max 5000
return_to: same-origin relative URL
```

Security:

- Request разрешён только через existing web auth boundary.
- Добавить CSRF token, подписанный `RC_API_SECRET`.
- `return_to` принимает только relative path, начинающийся с `/`.
- После update использовать POST/Redirect/GET.

### 9.9 Headers и URL safety

Добавить:

```text
Content-Security-Policy:
  default-src 'self';
  img-src 'self' data:;
  style-src 'self';
  script-src 'self';
  frame-ancestors 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer-when-downgrade
```

External URL:

- только `http/https`;
- `target="_blank"` всегда с `rel="noopener noreferrer"`;
- invalid URL не становится ссылкой.

### 9.10 Responsive и accessibility

- Breakpoint около `720px`.
- Mobile — одна колонка, без горизонтальных таблиц.
- Status всегда имеет текст, не только цвет/emoji.
- `:focus-visible` обязателен.
- Interactive elements минимум 40px по высоте на mobile.
- Heading hierarchy не пропускает уровни.

### 9.11 Acceptance criteria фазы F

- `/today` работает при complete, partial, empty и no-LLM run.
- `/runs/{date}/radar` сохраняет полный feature parity текущего аналитического Radar.
- Radar и Today используют разные read models и не редиректятся друг в друга.
- Malicious title `<script>` отображается как текст.
- Mobile viewport не имеет горизонтального scroll.
- Все evidence links валидны и безопасны.
- Save/note/status переживают rebuild.

---

## 10. Фаза G — API v2

### 10.1 Endpoints

Все v2 endpoints используют существующий Bearer auth:

```text
GET   /api/v2/briefings/{date}
GET   /api/v2/radar/{date}
GET   /api/v2/stories
GET   /api/v2/stories/{story_id}
GET   /api/v2/items
GET   /api/v2/runs
GET   /api/v2/source-health
GET   /api/v2/virality-events
PATCH /api/v2/stories/{story_id}/research-state
```

### 10.2 Stories query

Параметры совпадают с `/explore`. Response:

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 50
}
```

### 10.3 Naming

- `virality_events` — crosspost/score surge.
- `item_signals` — LLM-анализ items.
- `stories` — объединённые сюжеты.
- `briefings` — run-level editorial synthesis.

Не использовать ambiguous `signals` в v2.

### 10.4 Compatibility

- Не менять response schema `/api/v1/*`.
- Исправления security и input bounds в v1 допустимы, если shape остаётся прежним.
- Документировать v1 как deprecated только после успешного shadow rollout.

### 10.5 Acceptance criteria фазы G

- OpenAPI содержит все v2 schemas.
- Filters и pagination совпадают между UI и API.
- PATCH валидирует status/note и возвращает обновлённое состояние.
- Все v1 tests остаются зелёными.

---

## 11. Фаза H — official-first sources

### 11.1 NYT

Создать `sources/nytimes.py`.

Приоритет:

1. NYT Top Stories API для текущих разделов.
2. NYT Article Search API для профильных keywords.

Config:

```text
NYT_API_KEY
```

Если ключ отсутствует:

- adapter status `not_configured`;
- run не падает, если source optional;
- UI показывает setup hint без значения ключа.

Сохранять только:

- URL;
- title;
- abstract;
- byline;
- section;
- published date;
- multimedia URL, только если условия API это разрешают.

`content_scope="abstract"`.

### 11.2 WSJ/Dow Jones

Не пытаться обходить серверный paywall через Ladder.

Создать registry entry `wsj`:

- access `api`;
- cluster `business`;
- required env определяется реальным Dow Jones auth contract.

До наличия credentials:

- adapter status `not_configured`;
- никаких fake items;
- документация честно говорит, что content не собирается.

Реальную Dow Jones authentication реализовывать только после предоставления пользователем
доступа и сверки официальной документации. Это допустимый внешний blocker, а не повод
имитировать поддержку.

### 11.3 Ladder

- Использовать allowlist поддерживаемых доменов.
- Не называть Ladder «WSJ source».
- По умолчанию хранить title, canonical URL и разрешённый description.
- Не сохранять полный платный HTML.
- `content_scope` определяется фактически.
- Ladder failure не должен удалять ранее собранные RSS/API данные.

### 11.4 Generic RSS/Atom

Расширить профиль custom feeds:

```json
{
  "custom_feeds": [
    {
      "id": "example-newsletter",
      "url": "https://example.com/feed.xml",
      "label": "Example",
      "cluster": "business",
      "language": "en"
    }
  ]
}
```

Validation:

- unique ID;
- `https` предпочтителен;
- cluster только из enum;
- timeout и size limit;
- XML entities/network expansion отключены.

### 11.5 Acceptance criteria фазы H

- NYT fixture корректно преобразуется в `ContentItem`.
- Отсутствующий NYT/WSJ credential виден как `not_configured`, а не `error` и не `ok`.
- Ladder не заявляет WSJ.
- Custom RSS работает без изменения Python registry.

---

## 12. Артефакты snapshot

Целевая структура:

```text
data/snapshots/YYYY-MM-DD/
├── run-manifest.json
├── items.jsonl
├── observations.jsonl
├── item-signals.jsonl
├── stories.jsonl
├── briefing.json
├── briefing.md
├── posts.jsonl
├── hackernews.jsonl
├── rss.jsonl
├── ladder.jsonl
├── producthunt.jsonl
├── signals.jsonl
├── signals-report.md
└── trend-radar.md
```

Legacy-файлы остаются в корне snapshot один переходный релиз. Не перемещать их сразу в
`legacy/`, потому что внешние потребители могут читать существующие пути.

Каждый v2 artifact содержит `schema_version`.

Запись JSON/JSONL выполняется атомарно, где файл может читаться работающим API.

---

## 13. Тестовая стратегия

### 13.1 Unit tests

Добавить:

```text
tests/test_intelligence_models.py
tests/test_compat.py
tests/test_migrations_v2.py
tests/test_rebuild.py
tests/test_clustering.py
tests/test_ranking.py
tests/test_briefing.py
tests/test_llm_validation.py
tests/test_api_v2.py
tests/test_ui.py
tests/test_source_registry.py
tests/test_nytimes.py
```

### 13.2 Обязательный mixed-source fixture

Synthetic-safe fixture:

- Reddit пост с external article URL.
- HN story с тем же URL.
- NYT abstract с близким title.
- FT headline с близким title.
- Несвязанный Reddit пост той же компании.
- ProductHunt item.

Ожидания:

- первые четыре items образуют один story;
- несвязанный пост не приклеивается;
- source cluster count корректен;
- media items не вытеснены из-за score `0`;
- evidence links указывают на существующие item IDs.

### 13.3 Ranking cases

Table-driven tests:

- new single-source viral Reddit;
- moderate story в трёх clusters;
- old persistent story;
- resurfacing после 14 дней;
- media-only story;
- partial run;
- no previous run.

Expected component values и direction фиксируются явно.

### 13.4 LLM cases

Mock только сетевой ответ Qwen, не production data:

- valid response;
- fenced JSON;
- invalid JSON → repair;
- invalid relevance;
- unknown theme;
- unknown item ID;
- unsupported evidence ID;
- timeout/429;
- partial batches.

### 13.5 UI/security cases

- `<script>`, event handler и HTML entity в title/excerpt/note.
- `javascript:` URL.
- external URL rel attributes.
- invalid date/story ID.
- empty DB.
- partial briefing.
- saved state POST с invalid CSRF.
- unsafe `return_to`.

### 13.6 Browser checks

Playwright screenshots:

- `/today` desktop 1440×1000.
- `/today` mobile 390×844.
- `/runs/{date}/radar` desktop 1440×1000.
- `/runs/{date}/radar` mobile 390×844.
- story desktop/mobile.
- explore с открытыми filters.
- partial source health.

Screenshots не обязаны храниться в git, если проект не использует snapshot testing; достаточно
автоматических assertions на overflow, visibility и keyboard focus.

### 13.7 Coverage

Убрать полное исключение следующих чистых модулей:

- intelligence models/compat/clustering/ranking/repository;
- API v2 schemas/query logic;
- Jinja view-model builders.

Сетевые transport branches можно по-прежнему изолировать fixtures, но нельзя исключать весь
source adapter.

---

## 14. Документация

После реализации обновить:

- `README.md`: раздельные user journeys `/today` и `/runs/{date}/radar`, CLI `run`,
  source capabilities.
- `ARCHITECTURE.md`: source-agnostic pipeline, artifacts и rebuild invariant.
- `ROADMAP.md`: отметить реализованные фазы, не оставлять SQLite/UI как «будущее».
- `CHANGELOG.md`: добавить изменения, не перезаписывая существующие пользовательские правки.
- `docs/MULTI_SOURCE_PLAN.md`: официальный/optional/fallback access по источникам.
- `.env.example`: только пустые placeholders новых ключей.

Обязательно удалить credential-like значение из tracked README/документов. Само действующее
значение не помещать в issue, commit message, changelog или лог. Ротация выполняется отдельно
с пользователем.

---

## 15. Shadow rollout

### Этап 1 — локально

1. `db rebuild` на копии/временной БД.
2. Сравнить counts legacy files и v2 items.
3. Проверить минимум три historical dates.
4. Открыть `/today`, `/runs/{date}/radar`, story и explore без сети.

### Этап 2 — параллельная генерация

Семь последовательных nightly runs создают:

- старые reports;
- `briefing.json`;
- `briefing.md`;
- SQLite v2 projection.

Старые URLs остаются доступны.

### Сравниваемые показатели

- source counts;
- процент items, попавших в stories;
- количество ambiguous clusters;
- top-5 stories;
- evidence count;
- complete/partial status;
- direction stability между днями;
- LLM failed/repair batches.

### Этап 3 — переключение

Только после семи успешных runs:

- `/dashboard` redirect на `/today`;
- новый renderer Radar занимает canonical `/runs/{date}/radar`;
- `/radar` ведёт на последний date-specific Radar;
- Radar не редиректится в Today;
- Telegram ведёт на `/today`;
- API v2 объявляется основной read API;
- v1 и legacy artifacts остаются ещё один релиз.

Деплой не входит в автоматическое завершение этой задачи. Реализатор должен отдать готовый
локальный diff и дождаться разрешения.

---

## 16. Definition of Done

### Data trust

- [ ] Все items имеют provider, cluster, canonical URL и content scope.
- [ ] SQLite полностью восстанавливается из snapshots.
- [ ] Rebuild идемпотентен и сохраняет research state.
- [ ] Raw engagement разных платформ не сравнивается напрямую.
- [ ] RSS/Ladder не определяются как Reddit.
- [ ] Source counts вычисляются фактически.

### Stories и briefing

- [ ] Один сюжет объединяет независимые источники.
- [ ] Разные события одной компании не склеиваются автоматически.
- [ ] Story ID сохраняется между датами.
- [ ] Top stories имеют evidence.
- [ ] Unsupported evidence IDs отклоняются.
- [ ] Narrative shift основан на historical facts.
- [ ] `briefing.json` — источник для Markdown и UI.

### UX

- [ ] `/today` читается за 5–10 минут.
- [ ] `/today` показывает краткое «что сегодня» и не дублирует полный Radar.
- [ ] `/runs/{date}/radar` содержит LLM analysis, ideas, shifts, pain points, relevance,
  theme clouds, trend strength, Mega stories и source coverage.
- [ ] Radar сохраняет canonical date-specific route и не редиректится в Today.
- [ ] Complete/partial невозможно перепутать.
- [ ] `/stories/{id}` показывает timeline и evidence.
- [ ] `/explore` имеет server-side search/filter/pagination.
- [ ] Save/note/status работают.
- [ ] Mobile layout не имеет horizontal overflow.
- [ ] Внешний HTML escaped, URL безопасны.

### Sources

- [ ] Registry соответствует реальным adapters.
- [ ] NYT official adapter работает при наличии ключа.
- [ ] WSJ без credentials честно `not_configured`.
- [ ] Ladder не заявляет неподдерживаемые sources.
- [ ] Custom RSS добавляется через профиль.

### Compatibility и качество

- [ ] API v1 tests проходят без изменения response shape.
- [ ] Legacy artifacts продолжают создаваться.
- [ ] API v2 покрыт contract tests.
- [ ] Ruff, format, mypy и pytest зелёные.
- [ ] Документация соответствует реальному CLI/UI.
- [ ] Secrets не попали в diff.

---

## 17. Что передать на последующее ревью

После реализации другая LLM должна предоставить:

1. Краткое резюме реализованных фаз.
2. Список намеренно отложенных пунктов и причину.
3. `git status --short`.
4. `git diff --stat`.
5. Список миграций и способ rollback/rebuild.
6. Результаты четырёх обязательных quality-команд.
7. Результат offline end-to-end fixture.
8. Ссылки или локальные пути к desktop/mobile screenshots.
9. Пример `briefing.json` на synthetic fixture.
10. Подтверждение, что commit/push/deploy не выполнялись.

Последующее ревью должно отдельно проверить:

- миграционную безопасность;
- ranking math;
- clustering false positives/false negatives;
- evidence grounding;
- backward compatibility;
- XSS/CSRF/unsafe URLs;
- честность source status;
- mobile reading experience;
- отсутствие конфликтов с пользовательскими изменениями в рабочем дереве.
