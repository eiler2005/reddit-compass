# reddit-compass 🧭

Компас по трендам Reddit. Автономный сервис, который собирает «голос улицы» — живые
реакции, кейсы, боли и нарративы — и показывает **куда смотреть**: не просто выгружает ленту,
а ранжирует по score/обсуждаемости, ловит виральность и делает ночной разбор трендов.

**Не требует Reddit API credentials** — работает через Playwright (headless Chromium → Reddit
JSON API), с fallback на Atom RSS. Полностью config-driven и автономен: ничего не импортирует из
внешних проектов, вся связь с потребителями — через файлы (JSONL + Markdown).

Границы и контракты — в [ARCHITECTURE.md](ARCHITECTURE.md). Планы развития — в [ROADMAP.md](ROADMAP.md).

## Быстрый старт

```bash
uv sync --dev
uv run playwright install chromium      # один раз, для движка Playwright

uv run reddit-compass all               # полный цикл: fetch + search + track + virality + report
uv run reddit-compass nightly           # + ночной разбор трендов → data/harvests/
```

Без `playwright install` сервис использует RSS fallback (только hot, без score/комментариев).

## CLI

```
uv run reddit-compass <command> [options]
```

| Команда | Что делает |
|---------|------------|
| `fetch` | Hot/top по сабреддитам профиля (Playwright JSON API) |
| `search` | Keyword search по Reddit |
| `track` | Мониторинг tracked threads (Δ score, Δ comments) |
| `virality` | Cross-posting / всплески score / multi-subreddit |
| `report` | Markdown-отчёт из готового snapshot |
| `all` | Полный цикл: fetch + search + track + virality + report |
| `nightly` | `all` + подробный разбор трендов → `data/harvests/` |

Опции: `--config PATH` (профиль), `--output-dir PATH`, `--limit N`, `--time-filter day|week|month|year|all`, `-v`.

## Профили

Что собирать — задаётся профилем в `config/profiles/*.json` (сабреддиты по кластерам, keywords,
tracked threads, настройки). По умолчанию — `config/profiles/ai-native.json` (готовый AI-фокус:
работа/бизнес, доверие/подлинность, vibe coding/агенты, рынок труда). Нейтральный шаблон —
`config/profiles/starter.json`.

```bash
uv run reddit-compass all --config config/profiles/starter.json
# или через окружение:
REDDIT_COMPASS_CONFIG=config/profiles/ai-native.json uv run reddit-compass all
```

Добавить сабреддит/keyword/тред — правкой JSON-профиля, без изменения кода.

## Данные

Всё пишется внутрь проекта (git-ignored), каталог переопределяется через `DATA_DIR`:

```
data/
  snapshots/YYYY-MM-DD/
    posts.jsonl            карточки постов (score, комментарии, flair)
    keyword-search.jsonl   keyword search
    tracked-threads.jsonl  состояние тредов
    virality.jsonl         сигналы виральности
    trends-report.md       сводный отчёт
  harvests/
    reddit-compass-YYYY-MM-DD.md   ночной разбор с темами
  tracked-threads-state.jsonl      состояние между запусками
```

## Docker

```bash
docker compose run --rm reddit-compass all       # полный цикл в контейнере
docker compose run --rm reddit-compass nightly   # + ночной разбор
```

Данные — в volume `reddit-compass-data`. Деплой на VPS (HostKey) — см. [deploy/hostkey/](deploy/hostkey/).

## Этика

Read-only: сервис не постит, не голосует, не комментирует. Только публичные посты и комментарии,
с паузами между запросами и retry на 429. Подробнее — [AGENTS.md](AGENTS.md).

## Разработка

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest
```
