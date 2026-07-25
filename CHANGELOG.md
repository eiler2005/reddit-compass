# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/);
версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **Trend Radar v2** (`/runs/{date}/radar`): полноценный серверный рендер с LLM-аналитикой —
  карточки топ-тем с пояснениями, идеи для колонок, сдвиги нарратива, pain points (теги),
  топ-10 по релевантности для книги, облако тем. Тёмный editorial-стиль, единый с дашбордом.
- **Сила трендов** (`trend_strength.py`): composite score (кросс-источник × объём × новизна ×
  динамика) + метка 🆕/🔄. История тем в `data/theme-history.jsonl`. Таблица «📈 Сила трендов»
  на radar-странице: новые сильные тренды первыми, повторяющиеся — с указанием недель.
- **Signals без Reddit:** `reddit-compass signals` теперь загружает ВСЕ доступные JSONL
  (posts, hackernews, rss, ladder, producthunt). Раньше падал без `posts.jsonl`.
- **Radar: Ladder + ProductHunt:** `render_trend_radar` включает секции paywall-СМИ
  и ProductHunt в мега-тренды и отдельные блоки.

### Changed

- **`/runs/{date}/radar`**: переключён с regex-рендера markdown на `render_radar_page()`
  (структурный HTML из signals.jsonl + signals-report.md + JSONL-данных).
- **`_cmd_signals`** (cli.py): загрузка из 5 JSONL-файлов вместо только `posts.jsonl`.

### Fixed

- Signals на VPS падал с "Snapshot не найден: posts.jsonl" при отсутствии Reddit-данных
  (RSS/HN/Ladder/PH собираются автоматически, Reddit — вручную с Mac).

## [0.2.0] — 2026-07-23

### Added

- **Мульти-источники (Phase 6):** 5 адаптеров, 1282 единицы за прогон:
  - `sources/rss.py`: BBC, Guardian, Reuters, TechCrunch, Verge, Ars Technica (135 статей)
  - `sources/hackernews.py`: Algolia API, 14 запросов, фильтр 7 дней (197 stories)
  - `sources/ladder.py`: 12 paywall СМИ через Ladder proxy — парсинг статей из listing
    (NYT 28, WaPo 40, Wired 36, Time 28, AmBanker 20, VanityFair 20, NewYorker 10 = 183)
  - `sources/producthunt.py`: Atom feed (30 продуктов)
  - Reddit: Playwright JSON API (737 постов, 18 сабреддитов)
- **Ladder proxy** на VPS: `ghcr.io/everywall/ladder`, Docker network `reddit-compass_net`,
  `LADDER_URL=http://ladder:8080`. Ruleset: 33 домена (ladder-rules).
- **Run-манифест** (`manifest.py`): прозрачный лог каждого запуска — источник, статус,
  count, ошибки, длительность. Файл: `snapshots/YYYY-MM-DD/run-manifest.json`.
- **Dashboard v2** (интерактивный): кластеры (AI, Surveillance, Труд, Бизнес, Общество, HN, СМИ),
  все посты кликабельные, панель «📋 Статус запуска», навигация по якорям.
- **Trend radar** (`reddit-compass radar`): автогенерация отчёта по кластерам с прямыми
  ссылками на каждый пост. Секция «🔥 Мега-тренды» (топ-15 через все источники).
- **HTTPS-доступ** через хостовой Caddy: `https://rc.204.168.239.217.sslip.io/dashboard`
  (Basic Auth, Let's Encrypt TLS, SNI).
- **HN фильтр по дате:** `numericFilters=created_at_i>N` — только последние 7 дней
  (было: GPT-4 за 2023, стало: свежие stories).
- **SQLite-хранилище** (`db.py`): `data/compass.db`, таблицы posts/comments/signals/threads.
  Аддитивно к JSONL. CLI: `reddit-compass db init / stats`.
- **REST API** (FastAPI + OAuth2 client credentials): `/api/v1/snapshots|posts|signals|stats`,
  `/oauth/token` (JWT 1h), `/health`. CORS для Vercel-практикума. CLI: `reddit-compass serve`.
- **Уведомления-заготовки** (`notify.py`): `prepare_telegram_digest()`, `prepare_email_digest()` —
  формируют данные БЕЗ отправки, пишут в `data/notifications/`.
- **aiohttp JSON-клиент** (primary): лёгкий HTTP-движок без браузера. Playwright — fallback,
  RSS — last resort. `RedditEngine` переключается автоматически при блоке (HTML/403).
- **Proxy-ротация:** `REDDIT_COMPASS_PROXIES` — round-robin. Только для снижения 429.
- **`comments_for_top_n`**: комментарии только для top-N (default 5). Запросов: 526 → ~130.
- **Stealth-режим:** `--stealth` / `nightly` — jitter 3–6с + exponential backoff.
- **VPS деплой:** Docker compose (batch + api + caddy), host-cron (RSS 03:30, HN 03:45,
  Ladder 04:00), UFW, disk cleanup.
- `docs/COMPETITIVE_ANALYSIS.md`, `docs/IMPROVEMENTS.md`, `docs/MULTI_SOURCE_PLAN.md`.
- ROADMAP: фазы 2.5, 3.5, 6; дополнения в Phase 2/3.
- LICENSE (MIT), CONTRIBUTING.md, SECURITY.md.
- GitHub: description, topics, website, Docker CI/CD (GHCR).
- Тесты: 69+, coverage 65–84%.

### Changed

- AGENTS.md: proxy разрешены (только 429); движок — aiohttp primary; деплой разрешён.
- ARCHITECTURE.md: 5 источников, 3 движка, Ladder, манифест, dashboard.
- Убран r/deepfakes из профиля (404). Удалён проект `reddit_trends` (заменён).
- Зависимости: +fastapi, +uvicorn, +python-jose, +httpx (dev).
- Playwright proxy: раздельный формат (server/username/password), timeout 60с.
- Dashboard: источник определяется по subreddit + source (фикс "rss" вместо "HN").

## [0.1.0] — 2026-07-22

### Added

- Выделение `reddit-compass` в отдельный репозиторий из монорепо-сервиса `reddit-monitor`.
- Config-driven профили сбора (`config/profiles/ai-native.json`, `starter.json`).
- Движок Playwright JSON API + RSS fallback (без Reddit API credentials).
- CLI: `fetch / search / track / virality / report / all / nightly`.
- Детекция виральности: crosspost / score_surge / multi_subreddit.
- Ночной разбор трендов, сгруппированный по кластерам активного профиля.
- Docker-образ и compose для локального запуска; скелет VPS-деплоя (`deploy/hostkey/`).
- Обвязка: uv, ruff, mypy (strict), pytest (+cov), pre-commit, GitHub Actions CI.
- Тесты на парсеры, детектор виральности, рендер отчёта и конфигурацию.

### Changed

- Отвязка от монорепо: пути данных — внутри проекта, через `DATA_DIR` / `HARVESTS_DIR` /
  `REDDIT_COMPASS_CONFIG`; удалена привязка к `research/case-sources/...`.
- `trends_analysis.py` обобщён: разбор идёт по кластерам профиля, убраны книжные/брендовые секции.
- `PostCard.from_dict()` восстанавливает `top_comments` в `CommentCard` при чтении JSONL —
  устранён латентный баг рендера отчёта из файла (обращение к атрибутам dict-объектов).
