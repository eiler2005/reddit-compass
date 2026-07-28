# LLM implementation task: сделать Radar сильным story/trend clustering engine

Дата постановки: 2026-07-28.

Статус: это расширенное задание для следующей LLM-итерации. Оно объединяет прошлую задачу
про честные метрики/compression и новый этап про canonical URLs, generic guards,
cross-source clustering и более сильное обнаружение сюжетов/кластеров.

## 0. Главная цель

`reddit-compass` должен стать trendwatching dashboard, который из большого корпуса материалов
выделяет настоящие сюжеты и тренды, а не просто показывает 1000 одиночных карточек.

Нужная продуктовая модель:

```text
raw materials
  -> candidate stories
  -> clustered stories
  -> cross-source confirmed stories
  -> radar-ready trends
  -> project lenses: книга / РБК / business signal
```

Пользователь должен за 5–10 минут понимать:

- что реально выросло;
- где есть подтверждение разными источниками;
- где только одиночный сигнал;
- что важно для книги/РБК;
- какие сюжеты повторяются или возвращаются;
- почему Radar считает это трендом.

## 1. Текущее состояние после предыдущей реализации

Предыдущие этапы уже частично реализованы.

По последнему видимому UI:

```text
date: 2026-07-28
profile: broad
sources: 47/49
materials: 1445
radar-ready: 40
cross-source: 12
compression ratio: 72%
singletons: 1006 из 1046 candidates
```

Read-only проверка БД ранее давала:

```text
run: 2026-07-28:broad
items: 1445
candidate stories: 1046
single-item candidates: 1006
multi-item stories: 40
cross-source stories: 12
```

Это лучше прежнего состояния, потому что UI теперь честно разделяет:

- материалы;
- кандидатов;
- radar-ready;
- cross-source.

Но это ещё не достаточно хорошо для сильного trendwatching.

Главная оставшаяся проблема:

```text
cross-source stories = 12 при 47 источниках — мало.
singletons = 1006 — слишком много raw candidate noise.
```

Это не обязательно значит, что нужно искусственно уменьшать candidates. Нужно лучше:

- нормализовать URL;
- раскрывать Google News / RSS / Ladder links;
- не склеивать generic titles;
- склеивать один и тот же сюжет из разных источников;
- выводить одиночные материалы в отдельный raw/single-source слой, а не в top analytics.

## 2. Что обязательно прочитать перед изменениями

- `AGENTS.md`
- `README.md`
- `ARCHITECTURE.md`
- `ROADMAP.md`
- `CHANGELOG.md`
- `docs/RADAR_TRENDWATCHING_IMPLEMENTATION.md`
- `docs/RADAR_CLUSTERING_IMPROVEMENT_TASK.md`
- `src/reddit_compass/intelligence/clustering.py`
- `src/reddit_compass/intelligence/compat.py`
- `src/reddit_compass/intelligence/ranking.py`
- `src/reddit_compass/intelligence/runner.py`
- `src/reddit_compass/intelligence/rebuild.py`
- `src/reddit_compass/api/query_service.py`
- `src/reddit_compass/api/templates/radar.html`
- `src/reddit_compass/api/templates/runs.html`
- `src/reddit_compass/sources/rss.py`
- `src/reddit_compass/sources/ladder.py`
- `tests/test_clustering.py`
- `tests/test_query_service.py`
- `tests/test_rebuild.py`
- `tests/test_ranking.py`

## 3. Ограничения

- Не добавлять React.
- Не добавлять frontend build chain.
- Не добавлять тяжёлые ML/embedding зависимости без отдельного решения.
- Не читать и не печатать `.env`, `.env.secrets`, токены, ключи.
- Не запускать сетевой сбор без отдельной команды пользователя.
- Не делать deploy без отдельного явного разрешения.
- Не ломать API v1.
- API v2 можно расширять backward-compatible полями.
- Сеть в unit tests не использовать.
- Фикстуры должны быть synthetic/safe.

## 4. Основная гипотеза

Сейчас clustering слабый не потому, что fuzzy title matching недостаточно агрессивный.

Главная причина:

1. `canonical_url` часто не является реальным canonical publisher URL.
2. Google News RSS URLs остаются `news.google.com/rss/articles/...`.
3. Ladder/RSS listing links иногда дают generic или repeated titles.
4. Historical story seeding не восстанавливает URL index.
5. Cross-source merge слишком зависит от title similarity.

Поэтому нельзя просто снижать threshold. Это создаст ложные мега-кластеры.

Правильная стратегия:

```text
1. Сначала URL canonicalization.
2. Потом stronger generic/low-signal guards.
3. Потом conservative cross-source second pass.
4. Потом historical URL seed.
5. Потом Radar-ready promotion rules.
```

## 5. Этап A — audit текущей реализации

Перед изменениями сделать read-only audit:

```bash
git status --short
uv run pytest tests/test_clustering.py tests/test_query_service.py
```

Проверить текущие функции:

- `canonicalize_url`
- `normalize_title`
- `is_generic_title`
- `is_low_signal_title`
- `is_radar_ready`
- `StoryClusterer._find_matching_story`
- `StoryClusterer._match_urls`
- `StoryClusterer.seed_from_stories`
- `build_run_summary`
- `build_trend_shelves`
- `build_goal_relevance_rankings`

Найти и удалить/исправить dead code, если оно осталось.

Подозрительный фрагмент, который нужно проверить:

```python
if item.provider == cluster.title.split()[0] if cluster.title else False:
    pass
```

Если он есть и ничего не делает — удалить или заменить реальной логикой.

## 6. Этап B — canonical URL normalization

### 6.1. Цель

Сделать так, чтобы одинаковая статья из разных входов получала один и тот же canonical URL.

Особенно важно для:

- Google News RSS;
- RSS feeds с tracking params;
- AMP/mobile URLs;
- Reddit link posts;
- Ladder-collected article links.

### 6.2. Где менять

Главный файл:

- `src/reddit_compass/intelligence/compat.py`

Возможные дополнительные файлы:

- `src/reddit_compass/sources/rss.py`
- `src/reddit_compass/sources/ladder.py`
- `src/reddit_compass/intelligence/models.py`

### 6.3. Требуемые функции

Добавить/расширить:

```python
def canonicalize_url(url: str) -> str:
    ...

def unwrap_google_news_url(url: str) -> str | None:
    ...

def normalize_known_publisher_url(url: str) -> str:
    ...

def normalize_host(host: str) -> str:
    ...
```

### 6.4. Правила canonical URL

Общие правила:

- разрешены только `http` и `https`;
- scheme нормализовать к `https`, если безопасно;
- host lowercase;
- убрать `www.` только если это не ломает canonical matching, либо привести к publisher-specific canonical host;
- убрать fragments;
- убрать tracking query params;
- убрать trailing slash, кроме root path;
- decode safe percent-encoding там, где это не ломает URL;
- query сохранить только для известных URL, где query является частью canonical identity.

Удалять query params:

```text
utm_source
utm_medium
utm_campaign
utm_term
utm_content
fbclid
gclid
yclid
ref
ref_src
ref_url
oc
cmpid
smid
partner
campaign_id
source
output
```

### 6.5. Publisher host normalization

Examples:

```text
m.nytimes.com -> www.nytimes.com
mobile.nytimes.com -> www.nytimes.com
www.nytimes.com -> www.nytimes.com
nytimes.com -> www.nytimes.com

amp.theguardian.com -> www.theguardian.com
www.theguardian.com -> www.theguardian.com
theguardian.com -> www.theguardian.com

www.reuters.com -> www.reuters.com
reuters.com -> www.reuters.com

washingtonpost.com -> www.washingtonpost.com
www.washingtonpost.com -> www.washingtonpost.com
```

Do not over-normalize unknown hosts.

### 6.6. Google News RSS URLs

Google News RSS often emits:

```text
https://news.google.com/rss/articles/...
```

Goal:

- If original publisher URL is embedded/decodable from the Google News URL, extract it.
- If not extractable without network, keep Google URL as canonical but preserve a separate field if available.
- Do not do network calls in unit tests.

Implementation options:

1. Pure parser for known Google News URL shapes.
2. Optional resolver function behind explicit network flag, not used in unit tests.
3. If resolving requires network, skip network and add TODO + tests for non-network extractable cases.

Minimum acceptable result:

- `canonicalize_url()` removes Google tracking params like `oc=5`.
- RSS adapter stores provider/source correctly.
- Matching can still use normalized titles/entities when Google URL cannot be unwrapped.

Better result:

- Google News link is unwrapped to publisher URL when possible.

### 6.7. Reddit URLs

For Reddit link posts:

- `discussion_url` should be reddit permalink.
- `target_url` should be external article/product URL.
- `canonical_url` for link posts should prefer `target_url`, not reddit discussion URL.
- For self posts, canonical can remain reddit discussion URL.

Clustering `_match_urls()` should use:

```text
canonical_url
target_url
discussion_url only as fallback, and only for Reddit discussion matching
```

It should not match all reddit discussion URLs as news story canonical URLs.

### 6.8. Tests for URL normalization

Add tests:

```text
utm params removed
fragments removed
trailing slash normalized
mobile NYT host normalized
Guardian AMP host normalized
Reuters URL with tracking equals clean Reuters URL
Google News URL with oc=5 cleaned
Reddit link target URL matches RSS article URL
invalid schemes return empty string
```

## 7. Этап C — harden generic title guard

### 7.1. Current state

В `clustering.py` уже есть:

- `is_generic_title`
- `is_low_signal_title`
- `_GENERIC_PREFIXES`
- `_LOW_SIGNAL_PATTERNS`

Нужно усилить и проверить, что guard применяется и к item, и к existing cluster.

### 7.2. Guard must check both sides

Current item:

```python
normalized = normalize_title(item.title, item.provider)
if is_generic_title(normalized) or is_low_signal_title(item.title):
    return None
```

Need cluster-side guard:

```python
cluster_normalized = normalize_title(cluster.title)
if is_generic_title(cluster_normalized) or is_low_signal_title(cluster.title):
    continue
```

Otherwise a normal item can attach to an old generic cluster.

### 7.3. Improve generic list

Generic or low-signal examples:

```text
opinion
analysis
live
live updates
latest news
top stories
morning briefing
newsletter
sign up
sign up for
methodology
tech life
tech now
podcast
audio
video
briefing
daily briefing
market digest
the daily
```

Rules:

- Generic title may create a candidate story.
- Generic title must not merge by title-only.
- Generic title may merge only by exact canonical/target URL.
- Low-signal title must not be promoted to top Radar sections.

### 7.4. Opinion/Analysis pipe handling

Examples:

```text
Opinion | Real title - The New York Times
Analysis | Real title - The Washington Post
```

Expected:

```text
normalize_title() uses the meaningful right side, not "opinion" or "analysis".
```

Also handle:

```text
AI News | The Verge
```

Expected:

```text
normalize_title() keeps "AI News", because right side is publisher suffix.
```

### 7.5. Tests for generic guard

Add tests:

```text
different Opinion articles do not merge
Tech Life items with different URLs do not merge
Newsletter signup is low-signal
Methodology page is low-signal
Generic cluster cannot absorb normal item by title similarity
Same exact URL can merge even if title is generic
```

## 8. Этап D — conservative cross-source second-pass merge

### 8.1. Why

Item-by-item clustering is too local. It can miss same story from different providers if:

- titles are paraphrased;
- URLs differ;
- one source uses Google News URL;
- one title has publisher suffix;
- entity overlap is strong but fuzzy title score is not high enough.

Add second pass over candidate stories.

### 8.2. New function

Add a pure function:

```python
def merge_cross_source_story_candidates(
    stories: list[Story],
    items_by_story: dict[str, list[ContentItem]],
) -> list[Story]:
    ...
```

Alternative name is acceptable, but keep it isolated and unit-testable.

### 8.3. Merge rules

Merge two stories if all are true:

```text
providers are different
source clusters are compatible or complementary
domain overlap exists
neither title is generic/low-signal
normalized title similarity >= 0.82
entity overlap >= 1
published/snapshot dates close enough
```

Softer merge:

```text
normalized title similarity >= 0.72
entity overlap >= 2
at least one shared numeric/entity anchor
```

Never merge if:

```text
only shared token is generic
only shared entity is publisher/source name
both titles are opinion/analysis without strong entity overlap
one title is newsletter/methodology/live updates
domains are incompatible and no strong entity overlap
```

### 8.4. Entity anchors

Improve entity extraction if needed.

Useful anchors:

- named companies: OpenAI, Meta, Oracle, Nvidia, Anthropic, Reuters, BBC;
- people: Trump, Netanyahu, Mamdani, Powell;
- places: Japan, Minnesota, Gaza;
- numbers: 7.1, $1.5B, 21,000, 8000%;
- product names: Claude, ChatGPT, iPhone;
- legal/policy terms if paired with entity.

Do not let common source names become matching anchors:

```text
Reuters
BBC
Guardian
New York Times
Washington Post
```

### 8.5. Merge output

When merging:

- combine item IDs;
- combine domain IDs;
- choose best title:
  - prefer non-generic;
  - prefer higher source diversity;
  - prefer title from primary/mainstream/business source if clearer;
- preserve first_seen/last_seen;
- canonical_key should be stable.

### 8.6. Tests for second pass

Synthetic fixture:

```text
Reuters: "Japan hit by 7.1 earthquake, tsunami warning issued"
BBC: "Tsunami alert after magnitude 7.1 quake strikes Japan"
NYT: "Japan issues tsunami warning after powerful earthquake"
```

Expected:

```text
one story
item_count = 3
source_count >= 2
cross-source true
```

Negative fixture:

```text
Opinion | Ban AR-style rifles? Virginia is a warning.
Opinion | The path forward for clean energy transition.
```

Expected:

```text
two stories
```

## 9. Этап E — historical URL seed

### 9.1. Problem

`seed_from_stories()` historically may load stories without `canonical_urls`.

If seeded clusters have no URLs, cross-date continuation relies mostly on title similarity.

### 9.2. Goal

When loading recent stories, also load their item URLs.

Options:

1. Extend `_load_recent_stories()` to return stories with URL metadata.
2. Add new seed method:

```python
def seed_from_story_items(
    stories: list[Story],
    items_by_story: dict[str, list[ContentItem]],
) -> None:
    ...
```

3. Add lightweight `StorySeed` dataclass.

Choose the smallest clean implementation.

### 9.3. Expected effect

If story appeared yesterday from Reuters and today from HN/Reddit with same target URL, it should continue the same story/trend.

### 9.4. Tests

Add fixture:

```text
day 1: Reuters URL
day 2: Reddit link-post target_url same Reuters URL
```

Expected:

```text
same story_id or same trend_id
direction can become stable/growing/resurfacing depending ranking logic
```

## 10. Этап F — source-aware matching

### 10.1. Provider section quality

Some feeds are more likely to be low-signal:

- podcast feeds;
- newsletter feeds;
- methodology/listing pages;
- Google News search feeds with generic snippets;
- Ladder listing pages.

Add source-aware quality hints:

```python
def source_section_quality(provider: str, section: str, title: str, url: str) -> Literal["low", "normal", "high"]:
    ...
```

Or keep it simpler:

```python
def is_low_signal_item(item: ContentItem) -> bool:
    ...
```

### 10.2. Use in ranking/Radar

Low-signal items:

- can exist in DB;
- can appear in Explore;
- should be penalized in ranking;
- should not dominate top Radar.

### 10.3. Tests

```text
BBC podcast repeated title -> low signal
methodology URL/title -> low signal
normal Reuters/BBC article -> normal/high signal
```

## 11. Этап G — Radar-ready promotion rules

### 11.1. Current target

Do not try to make candidate count tiny.

Target output:

```text
materials: ~1400
candidates: can be 900–1100
radar-ready: 60–100
cross-source: 25–50
false mega clusters: near zero
```

### 11.2. Radar-ready logic

Radar-ready story if:

```text
source_count >= 2
OR item_count >= 2 and not low-signal
OR trend_score >= threshold and confidence != low and not low-signal
OR project relevance very high and not low-signal
```

But avoid:

```text
single-source low-signal
newsletter
methodology
generic podcast/show title
pure listing page
```

### 11.3. UI labels

Keep top line honest:

```text
1445 материалов · 1046 кандидатов · 40 radar-ready · 12 cross-source
```

But warning should be less alarming:

Current:

```text
Compression ratio 72% — clustering пока почти не сжимает корпус...
```

Better:

```text
Много одиночных кандидатов: 1006/1046. Они доступны в Explore; Radar показывает 40 прошедших фильтр сигналов.
```

This is less technical and more useful.

## 12. Этап H — trend layer above stories

This can be a lightweight query layer, not necessarily a DB migration.

Problem:

Even good stories are event-level. Trendwatching needs pattern-level grouping.

Add derived trend groups:

```text
Story: "Judge approves $1.5B Anthropic settlement..."
Story: "Authors sue OpenAI..."
Story: "Publishers demand compensation from AI labs..."

Trend: "AI training data litigation becomes a real cost center"
```

### 12.1. Minimal implementation

Use existing fields:

- `theme_ids`
- `candidate_themes`
- `domain_ids`
- `pain_points`
- entities
- project_scores

Build `trend_groups` in query service:

```python
def build_trend_groups(conn, run_id, window="7d") -> list[TrendGroupView]:
    ...
```

Group stories by:

```text
stable theme
shared pain point
shared entity cluster
domain
```

No LLM required for first version.

### 12.2. UI

Radar should show:

```text
Сюжеты: concrete events
Тренды: repeated patterns
Мета-тренды: week/month narrative shifts
```

Do not overload `story`.

### 12.3. Tests

Fixture:

```text
3 AI copyright/lawsuit stories
2 AI workplace adoption stories
```

Expected:

```text
two trend groups
not five unrelated top cards
```

## 13. Этап I — measurement harness

Add a local diagnostic command or test helper that prints clustering quality:

```text
items
candidates
singletons
multi-item
cross-source
radar-ready
low-signal top count
false generic clusters
top providers by unmerged singleton count
```

Possible CLI:

```bash
reddit-compass db clustering-report --date 2026-07-28 --profile broad
```

If CLI feels too much, add pure function and tests first.

This report is useful before/after every clustering change.

## 14. Этап J — tests required before handoff

Add or update tests:

### URL normalization

- tracking params removed;
- fragments removed;
- trailing slash normalized;
- mobile NYT host normalized;
- Guardian AMP host normalized;
- Google News URL cleaned;
- invalid schemes rejected;
- Reddit link target URL matches RSS article URL.

### Title normalization

- `Opinion | Real title - The New York Times` does not normalize to `opinion`;
- `Analysis | Real title - Washington Post` keeps meaningful title;
- `AI News | The Verge` keeps `ai news`;
- source suffix removed.

### Generic guard

- different Opinion articles do not merge;
- `Tech Life` repeated URLs do not merge by title;
- newsletter signup is low-signal;
- methodology page is low-signal;
- generic cluster cannot absorb normal item.

### Cross-source merge

- Reuters/BBC/NYT same event merges;
- Reddit target URL + RSS article URL merges;
- HN discussion linking same article merges;
- unrelated same-domain opinion pieces do not merge;
- same numeric/entity anchors can merge at lower threshold.

### Historical seed

- previous-day story URL helps current-day story continue;
- resurfacing/growing does not reset to unrelated new story when URL matches.

### Radar/query

- radar-ready count excludes low-signal stories;
- single-source signals remain in Explore;
- top Radar blocks do not contain newsletter/methodology;
- API v2 fields remain backward compatible;
- HTML escaping still passes.

## 15. Required checks

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If only docs changed, checks can be lighter, but implementation work must run all four.

## 16. Acceptance criteria

The next implementation is successful if:

```text
cross-source stories increase materially without obvious false merges.
radar-ready count increases because good stories merge, not because thresholds are loosened.
generic false mega-clusters are gone.
singletons remain visible in Explore/raw sections.
top Radar shows fewer duplicates.
warnings explain quality clearly.
tests prove positive and negative merge cases.
```

Target for a broad run around 1400 materials:

```text
candidates: 900–1100 is acceptable
radar-ready: 60–100
cross-source: 25–50
false generic clusters: 0 known examples
analysis coverage: >=95% when analyze=True
```

Do not optimize for smaller candidate count alone.

Optimize for:

```text
precision in top Radar
source diversity
evidence quality
readability
honest diagnostics
```

## 17. Suggested implementation order

1. Add/extend tests for current known bad examples.
2. Canonical URL normalization.
3. Generic/low-signal guard hardening on both item and cluster side.
4. Remove dead code in matching.
5. Conservative cross-source second pass.
6. Historical URL seed.
7. Radar-ready promotion tuning.
8. Optional trend group layer above stories.
9. Clustering diagnostic report.
10. Docs/changelog.

## 18. Do not do

- Do not simply lower fuzzy threshold globally.
- Do not hide singletons by deleting them.
- Do not call all candidates “trends”.
- Do not merge by generic words like `opinion`, `analysis`, `live`, `tech`.
- Do not use network in unit tests.
- Do not deploy without explicit user approval.
- Do not read secrets.

## 19. Short prompt version

If you need a compact prompt for another LLM, use this:

```text
Improve reddit-compass Radar clustering quality.

Current state: broad run has 1445 materials, 1046 candidate stories, 1006 singletons,
40 radar-ready, 12 cross-source. UI is now honest, but clustering is still weak.

Goal: improve canonical URL normalization, generic title guards, conservative cross-source
merge, historical URL seed, and Radar-ready promotion so Radar surfaces real story/trend
clusters without false generic mega-clusters.

Do not add React, heavy ML deps, network tests, deploy, or read secrets.

Read AGENTS.md, README.md, ARCHITECTURE.md, docs/RADAR_TRENDWATCHING_IMPLEMENTATION.md,
src/reddit_compass/intelligence/clustering.py, compat.py, ranking.py, runner.py,
api/query_service.py, tests/test_clustering.py, tests/test_query_service.py.

Implement in order:
1. tests for bad examples;
2. canonicalize_url improvements, especially Google News/RSS/tracking/mobile/AMP;
3. generic/low-signal guard on both item and cluster sides;
4. remove dead matching code;
5. conservative second-pass cross-source merge;
6. historical URL seeding;
7. Radar-ready tuning and better diagnostics;
8. docs/changelog.

Run ruff, format check, mypy, full pytest.
Acceptance: cross-source materially improves, false generic clusters disappear, top Radar has
fewer duplicates, singletons remain in Explore, API compatibility is preserved.
```
