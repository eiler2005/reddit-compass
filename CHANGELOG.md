# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/);
версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- `docs/COMPETITIVE_ANALYSIS.md` — конкурентный анализ: ландшафт GitHub (2454 репо, топ-7),
  таблицы фич reddit-universal-scraper / yars / Reddit_Scrapper, вывод об уникальности ниши,
  направления для дальнейшего изучения.
- `docs/IMPROVEMENTS.md` — ранжированный план улучшений по итогам анализа конкурентов:
  LLM-анализ, SQLite, уведомления, dry run, Docker CI/CD, exploratory subreddits;
  секция по легальности скрапинга.
- ROADMAP: новые фазы 2.5 (dry run), 3.5 (SQLite); дополнения в Phase 2 (Docker CI/CD),
  Phase 3 (multi-dimensional scoring), техдолг (exploratory subreddits, запрет proxies).

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
