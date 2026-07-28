# Radar trendwatching implementation

Дата: 2026-07-28.

## Что изменено

- Default collection profile теперь `broad`: широкий корпус для трендвочинга, а не только AI-native.
- `ai-native` сохранён как отдельная линза/профиль.
- В код добавлена стабильная broad taxonomy из 12 `domain_id`.
- `ContentItem`, `Story`, `StoryMetric`, `ItemSignal` расширены полями `domain_ids`,
  `trend_id`, `lifecycle`, `project_scores`, URL/evidence/dedupe metadata.
- SQLite projection поднята до schema v3 через `PRAGMA user_version`.
- Radar получил category navigation, source-cluster matrix, trend shelves и Broad/AI-native switcher.
- `run --analyze` создаёт `item_signals`; при `0` разметок UI не рендерит фальшивый LLM-анализ.
- Source coverage считается на уровне `provider × section/feed`.
- Item count считается через `observations`, а не `items.snapshot_date`.
- Reddit link-post хранит `discussion_url` и `target_url`; canonical URL для link-post — внешний target.
- Story clustering использует canonical/target URL, нормализованный title/entity overlap и recent history.
- HN adapter собирает front page/new/weekly-top, затем keyword search.
- RSS adapter расширен до broad sections для BBC, Guardian, Reuters, NYT/WaPo via Google News,
  FT/Fox Business/USA Today и tech/culture sources.

## Текущие границы реализации

- `Trend` и `Meta-trend` пока представлены через `trend_id`, `lifecycle`, shelves и историю stories;
  отдельные таблицы trends/meta_trends ещё не введены.
- LLM layer сейчас закрыт deterministic facets fallback. Это гарантирует непустые `item_signals`
  для `--analyze`, но полноценные Qwen prompts нужно подключать отдельным этапом.
- `AI-native Lens` пока фильтрует верхнюю аналитику к AI/tech, но не является отдельным
  мульти-доменным semantic lens.
- Project history для книги/РБК использует `project_scores` и rankings; отдельные таблицы
  recurring thesis/counterpoints/column history ещё не введены.

## Проверки

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Последний локальный прогон: 271 tests passed.
