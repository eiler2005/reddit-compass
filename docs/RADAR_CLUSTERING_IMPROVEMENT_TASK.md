# Task for another LLM: улучшить story clustering, compression и честность Radar

Дата постановки: 2026-07-28.

## 0. Контекст и цель

`reddit-compass` должен быть trendwatching dashboard, а не просто списком собранных материалов.

Сейчас Radar формально показывает много “сюжетов”, но большая часть этих “сюжетов” — одиночные материалы. Это создаёт шум, повторы и ощущение слабой аналитики.

Цель задачи: сделать так, чтобы Radar честно разделял:

1. raw materials;
2. candidate stories;
3. реально склеенные stories;
4. cross-source confirmed stories;
5. radar-ready trends/stories.

Нельзя решать это только UI. Нужно поправить clustering, метрики, ranking и labels в интерфейсе.

## 1. Что обязательно прочитать перед работой

- `AGENTS.md`
- `README.md`
- `ARCHITECTURE.md`
- `ROADMAP.md`
- `docs/RADAR_TRENDWATCHING_IMPLEMENTATION.md`
- `src/reddit_compass/intelligence/clustering.py`
- `src/reddit_compass/intelligence/ranking.py`
- `src/reddit_compass/intelligence/runner.py`
- `src/reddit_compass/api/query_service.py`
- `src/reddit_compass/api/templates/radar.html`
- `src/reddit_compass/api/templates/runs.html`
- `tests/test_clustering.py`
- `tests/test_query_service.py`

## 2. Фактическая диагностика текущего состояния

Read-only анализ на VPS показал:

```text
run: 2026-07-28:broad
items: 1445
stories: 1031
item_signals: 996
story/item ratio: 0.713
```

Распределение story size:

```text
1 item  -> 988 stories
2 items -> 34 stories
3 items -> 6 stories
4 items -> 2 stories
11 items -> 1 story
```

Распределение source count:

```text
1 source  -> 1020 stories
2 sources -> 10 stories
3 sources -> 1 story
```

Старые runs ещё хуже:

```text
2026-07-27:broad     2184 items -> 2127 stories
2026-07-28:ai-native  638 items -> 613 stories, 0 item_signals
```

Вывод:

- `story_count` сейчас в основном равен количеству одиночных candidate stories.
- Cross-source подтверждённых сюжетов почти нет.
- Radar показывает raw candidate count как “сюжеты”, что вводит в заблуждение.
- `item_signals` покрывают не весь latest broad run: 996/1445.

## 3. Найденные конкретные проблемы

### 3.1. Under-merge

Большинство материалов остаются single-item stories.

Это не всегда ошибка: многие новости действительно уникальны. Но для Radar такие одиночные материалы не должны считаться полноценными “трендами” наравне с cross-source сюжетом.

### 3.2. Over-merge generic titles

Есть ложные крупные clusters.

Пример:

```text
Opinion | Mamdani’s Netanyahu Stunt Was a Waste of His Talent and Our Time - The New York Times
```

склеился с разными Washington Post opinion articles:

```text
Opinion | Ban AR-style rifles? Virginia is a warning. - The Washington Post
Opinion | The path forward for the clean energy transition - The Washington Post
Opinion | PEN America, the free speech group confused about free speech - The Washington Post
...
```

Вероятная причина: `normalize_title()` режет заголовок по `|` и оставляет generic part `opinion`.

Другой пример low-signal cluster:

```text
Tech Life
```

Он склеивает разные BBC podcast/audio items по generic show title, хотя это не один новостной сюжет.

### 3.3. Нестабильный canonical key

В `StoryClusterer._create_new_story()` сейчас:

```python
tokens = list(extract_tokens(normalized))
canonical_key = _canonical_key_from_tokens(tokens)
```

`extract_tokens()` возвращает `set`, затем он превращается в `list`. Порядок set не является полезным исходным порядком заголовка. `_canonical_key_from_tokens()` берёт первые 5 токенов из этой list, что может давать нестабильный/слабый canonical key.

Нужно перейти на ordered normalized tokens для canonical key.

### 3.4. История плохо помогает URL matching

`seed_from_stories()` загружает historical stories, но не восстанавливает `canonical_urls`.

Если historical story не имеет URL index, cross-date matching опирается в основном на title. Это ограничивает continuation/resurfacing.

Не обязательно решать полноценной миграцией в первом этапе, но нужно явно оценить impact.

### 3.5. Analysis coverage stale/partial

Latest broad:

```text
observed items: 1445
item_signals: 996
```

Radar показывает LLM/facet слой, но он покрывает не весь корпус.

Нужно:

- либо гарантировать signals для всех observed items при `--analyze`;
- либо явно показывать partial analysis и не делать вид, что весь run размечен.

## 4. Ограничения

- Не добавлять React.
- Не добавлять отдельный frontend build chain.
- Не добавлять тяжёлые ML/embedding зависимости без отдельного решения.
- Не читать и не печатать `.env`, `.env.secrets`, токены, ключи.
- Не запускать сетевой сбор без отдельной команды пользователя.
- Не делать deploy/commit/push без отдельного явного разрешения.
- Не ломать API v1.
- API v2 можно расширять backward-compatible параметрами/полями.

## 5. Этап 1 — честные метрики и терминология без изменения clustering

Цель: сначала перестать вводить пользователя в заблуждение.

### 5.1. Добавить derived metrics

В `RunSummary` / `query_service` добавить:

```text
raw_item_count
candidate_story_count
single_item_story_count
multi_item_story_count
cross_source_story_count
radar_ready_story_count
analyzed_item_count
analyzed_coverage_ratio
compression_ratio
```

Определения:

```text
raw_item_count = COUNT(DISTINCT observations.item_id)
candidate_story_count = COUNT(story_metrics)
single_item_story_count = stories where item_count = 1
multi_item_story_count = stories where item_count >= 2
cross_source_story_count = stories where source_count >= 2
analyzed_item_count = COUNT(item_signals)
analyzed_coverage_ratio = analyzed_item_count / raw_item_count
compression_ratio = candidate_story_count / raw_item_count
```

`radar_ready_story_count` определить как:

```text
stories where:
  source_count >= 2
  OR item_count >= 2
  OR high editorial/project relevance and not low-signal
```

Порог high relevance выбрать консервативно и покрыть тестом.

### 5.2. Обновить UI labels

В Radar верхняя строка должна показывать не просто `1031 сюжет`, а примерно:

```text
1445 материалов
1031 кандидатов
43 склеенных сюжета
11 cross-source
996/1445 размечено
```

В `/runs` тоже не называть raw candidate count главным “сюжеты”.

Рекомендуемый label:

```text
Кандидаты
Склеено
Cross-source
Разметка
```

### 5.3. Добавить warning states

Если:

```text
candidate_story_count / raw_item_count > 0.65
```

показать:

```text
Высокая доля одиночных сюжетов: clustering пока почти не сжимает корпус.
```

Если:

```text
analyzed_coverage_ratio < 0.95
```

показать:

```text
Разметка покрывает не весь run: 996/1445 материалов.
```

## 6. Этап 2 — исправить title normalization

Файл: `src/reddit_compass/intelligence/clustering.py`.

### 6.1. Правила для `|`

Текущая логика слишком грубая.

Нужно:

- Если `|` отделяет publisher suffix, удалять suffix.
- Если левая часть — generic prefix (`Opinion`, `Analysis`, `Live`, `Tech Life`, `Newsletter`), использовать правую meaningful часть.
- Если правая часть — source/publisher (`The Verge`, `BBC`, `Reuters`, `The New York Times`), использовать левую meaningful часть.
- Если обе части meaningful, не выбрасывать одну без проверки.

Примеры ожидаемого поведения:

```text
normalize_title("AI News | The Verge", "theverge")
-> "ai news"

normalize_title("Opinion | Mamdani’s Netanyahu Stunt Was a Waste of His Talent and Our Time - The New York Times", "nytimes")
-> "mamdani netanyahu stunt waste talent time"

normalize_title("Opinion | Ban AR-style rifles? Virginia is a warning. - The Washington Post", "washingtonpost")
-> "ban style rifles virginia warning"
```

### 6.2. Удаление trailing source suffix

Добавить удаление suffix вида:

```text
- The New York Times
- The Washington Post
- Reuters
- BBC
- The Guardian
```

Нужно provider alias map:

```text
nytimes: new york times, the new york times, nyt
washingtonpost: washington post, the washington post, wapo
theverge: the verge, verge
bbc: bbc
guardian: guardian, the guardian
reuters: reuters
ft: financial times, ft
techcrunch: techcrunch, tech crunch
arstechnica: ars technica
```

## 7. Этап 3 — generic title guard и low-signal detection

### 7.1. Generic title guard

Добавить helper:

```python
def is_generic_title(normalized_title: str) -> bool:
    ...
```

Generic examples:

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
sign up newsletter
methodology
tech life
tech now
podcast
```

Правило:

- Если title generic:
  - merge разрешён только по exact canonical/target URL;
  - title similarity merge запрещён.

### 7.2. Low-signal title detection

Добавить helper:

```python
def is_low_signal_title(title: str) -> bool:
    ...
```

Low-signal examples:

```text
Sign up for the Spin newsletter
Methodology for America's Top WorkTech Companies
Canada's Best Companies 2026 Methodology
Tech Life
Tech Now
Morning Briefing
```

Low-signal материалы:

- остаются в raw Explore;
- не попадают в top changes;
- не попадают в mega stories;
- не попадают в project panels;
- получают penalty в ranking.

## 8. Этап 4 — сделать canonical key стабильным

Вместо:

```python
tokens = list(extract_tokens(normalized))
```

нужно использовать ordered tokens из normalized title.

Вариант:

```python
def extract_ordered_tokens(normalized: str) -> list[str]:
    ...
```

`canonical_key` должен быть:

- детерминированным;
- построенным из meaningful ordered tokens;
- не зависеть от порядка Python set.

Тест:

```text
cluster_items(items) два раза на одном входе даёт те же story_ids.
```

## 9. Этап 5 — улучшить cross-source clustering без over-merge

Цель: лучше склеивать один и тот же событийный сюжет из разных источников, но не склеивать generic opinion/listing pages.

### 9.1. Matching order

Сохранить порядок:

1. exact canonical URL;
2. target URL против canonical URL;
3. normalized title/entity overlap;
4. conservative fuzzy title match.

### 9.2. Entity-aware threshold

Оставить или уточнить:

```text
similarity >= 0.72
OR similarity >= 0.62 AND entity overlap exists
```

Но добавить guard:

```text
if generic title -> no title-only merge
if no meaningful entities and token count < 4 -> no title-only merge
if provider/source_section indicates newsletter/methodology/listing -> no top Radar merge
```

### 9.3. Same provider repeated series

Для одного provider/source_section repeated series titles типа `Tech Life`:

- не объединять разные URLs в один story;
- либо помечать как low-signal series.

## 10. Этап 6 — Radar-ready слой

Сейчас `story_metrics` используется как будто это уже тренды. Нужно добавить query-level слой `radar_ready`.

Минимальный вариант без новой таблицы:

```python
def is_radar_ready_story(row) -> bool:
    return (
        row.source_count >= 2
        or row.item_count >= 2
        or row.trend_score >= HIGH_SCORE_THRESHOLD and not is_low_signal_title(row.title)
    )
```

Использовать `radar_ready` в:

- trend shelves;
- mega stories;
- project rankings;
- top changes.

Одиночные материалы показывать отдельно:

```text
Single-source signals
Raw popular by channel
```

Они не должны называться мега-сюжетами.

## 11. Этап 7 — consistency для `--analyze`

В `runner.py` проверить:

```text
if analyze=True:
  item_signals should cover all observed items in the run
```

Если не покрывает:

- run/manifest/UI должны показывать partial analysis;
- Radar warning должен быть видимым.

Не запускать сетевые источники в тестах.

## 12. Этап 8 — API/UI/docs

### 12.1. API v2

Backward-compatible добавить поля в responses:

```text
candidate_story_count
single_item_story_count
multi_item_story_count
cross_source_story_count
radar_ready_story_count
analyzed_coverage_ratio
```

### 12.2. UI

Обновить:

- `/runs`
- `/runs/{date}/radar`
- возможно `/today`, если там используется raw story count.

UI должен объяснять разницу:

```text
Материалы — всё, что собрано.
Кандидаты — первичные story clusters.
Склеенные — stories с 2+ материалами.
Cross-source — stories из 2+ независимых source clusters/providers.
Radar-ready — то, что можно считать аналитическим сигналом.
```

### 12.3. Документация

Обновить:

- `README.md`
- `ARCHITECTURE.md`
- `CHANGELOG.md`
- `docs/RADAR_TRENDWATCHING_IMPLEMENTATION.md`

## 13. Тест-план

Обязательные tests:

1. `normalize_title("Opinion | Real title - The New York Times", "nytimes")` не возвращает `opinion`.
2. Разные `Opinion | ...` материалы не склеиваются.
3. `AI News | The Verge` сохраняет meaningful left part.
4. Repeated `Tech Life` с разными URLs не склеивается по title-only.
5. `Sign up for ... newsletter` определяется как low-signal.
6. `Methodology for ...` определяется как low-signal.
7. Один и тот же event из Reuters/BBC/NYT с похожим title и entity overlap склеивается.
8. `cluster_items(items)` детерминирован по story IDs.
9. `RunSummary` считает candidate/single/multi/cross-source/radar-ready metrics.
10. Radar warning появляется при high single-item ratio.
11. Radar warning появляется при partial analysis coverage.
12. Top Radar blocks не включают low-signal stories.
13. `/explore` продолжает показывать raw/candidate stories.
14. API v1 compatibility не ломается.
15. HTML escaping/XSS tests остаются зелёными.

## 14. Проверки перед сдачей

Обязательно:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

## 15. Acceptance criteria

Готовность изменения:

- В Radar больше не выглядит так, будто 1000 одиночных материалов — это 1000 настоящих трендов.
- Пользователь видит честные метрики compression и analysis coverage.
- Generic opinion/newsletter/methodology/podcast titles не создают ложные mega-stories.
- Cross-source stories стали отдельной явной метрикой.
- Single-source материалы остаются доступны в Explore/raw sections, но не доминируют в top аналитике.
- `/today` остаётся коротким.
- `/radar` становится аналитическим workspace.
- `/explore` остаётся местом для полного корпуса и фильтров.

## 16. Что не делать

- Не делать сетевой сбор.
- Не делать deploy.
- Не делать commit/push без разрешения.
- Не переписывать весь clustering на embeddings.
- Не добавлять новую тяжёлую инфраструктуру.
- Не скрывать проблему UI-лейблами без фактических metrics.

## 17. Рекомендуемый порядок выполнения

1. Этап 1: metrics + UI labels/warnings.
2. Этап 2: `normalize_title()` fixes.
3. Этап 3: generic/low-signal guards.
4. Этап 4: deterministic canonical key.
5. Этап 5: conservative cross-source clustering improvements.
6. Этап 6: radar-ready filtering for top sections.
7. Этап 7: analysis coverage consistency.
8. Этап 8: API/docs/tests cleanup.

Такой порядок важен: сначала сделать продукт честным, потом улучшать качество clustering. Иначе можно случайно спрятать проблему за новым UI.
