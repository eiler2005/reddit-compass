# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/);
версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **SQLite-хранилище** (`db.py`): `data/compass.db`, таблицы posts/comments/signals/threads.
  Аддитивно к JSONL. CLI: `reddit-compass db init / stats`.
- **REST API** (FastAPI + OAuth2 client credentials): `/api/v1/snapshots|posts|signals|stats`,
  `/oauth/token` (JWT 1h), `/health`. CORS для Vercel-практикума. CLI: `reddit-compass serve`.
- **Уведомления-заготовки** (`notify.py`): `prepare_telegram_digest()`, `prepare_email_digest()` —
  формируют данные БЕЗ отправки, пишут в `data/notifications/`.
- **aiohttp JSON-клиент** (primary): лёгкий HTTP-движок без браузера. Playwright — fallback,
  RSS — last resort. `RedditEngine` переключается автоматически при блоке (HTML/403).
- **Proxy-ротация:** `REDDIT_COMPASS_PROXIES="http://p1:port,http://p2:port"` — round-robin
  по запросам. Только для снижения 429 (разрешено AGENTS.md).
- **`comments_for_top_n`** в настройках профиля: комментарии только для top-N постов по score
  (default 5). Сокращение объёма запросов в ~5 раз (526 → ~130 за прогон).
- **Stealth-режим:** `--stealth` / `nightly` — jitter пауз (3–6с) + exponential backoff (429).
- `docs/COMPETITIVE_ANALYSIS.md` — конкурентный анализ: ландшафт GitHub, Ladder (⭐8.7k), СМИ.
- `docs/IMPROVEMENTS.md` — ранжированный план улучшений.
- `docs/MULTI_SOURCE_PLAN.md` — детальный план мульти-источников (Phase 6).
- ROADMAP: фазы 2.5, 3.5, 6; дополнения в Phase 2/3.
- Тесты: db (12), api (10), notify (7), engine (11) — итого 69 тестов, coverage 84%.

### Changed

- AGENTS.md: proxy разрешены (только 429); движок — aiohttp primary.
- ARCHITECTURE.md: 3 движка, rate limiting, proxy.
- Убран r/deepfakes из профиля (404, мёртвый сабреддит).
- Зависимости: +fastapi, +uvicorn, +python-jose, +httpx (dev).

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
