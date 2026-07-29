# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/);
версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **Versioned Story/Trend Engine**: новая `trend_engine.db` с frozen Data Releases,
  независимыми Facet/Story/Trend attempts, checksum verification и SQLite immutability triggers.
- **Hybrid Story Engine**: bounded top-K retrieval по URL/title/entities/optional E5,
  event conflicts, stable-landing URL guard, RFC/ISO date normalization, constrained
  agglomeration, stable story IDs и merge/split provenance.
- **Trend Engine**: pattern graph только поверх разных stories, минимум три события/два дня,
  specific-pattern guard (pain/theme alone cannot form a trend), source scope, mandatory Qwen
  confirmation gate, history status и lifecycle без искусственной динамики.
- **Golden Set и release gates**: stratified export/import, precision/recall/overmerge,
  cross-source recall, evidence coverage и Qwen-budget перед production publish.
- **Engine control plane**: `engine release/facets/embeddings/stories/trends/golden/publish/rollback`,
  `/engine`, `/api/v2/engine/*`, publication-backed Radar и короткий Today.
- **Published analysis layers**: separate News inbox (`/news`, `/api/v2/news`), Stories workspace
  (`/stories`, `/api/v2/engine/stories`), Trends workspace (`/trends`, `/api/v2/engine/trends`)
  and Project Lens (`/projects/{project_id}`, `/api/v2/projects/{project_id}/lens`) over the same
  immutable RadarPublication.
- **GUI drill-down**: published Story detail (`/stories/{story_id}`,
  `/api/v2/engine/stories/{story_id}`), Trend detail (`/trends/{trend_id}`,
  `/api/v2/engine/trends/{trend_id}`) and Radar cockpit links across News/Stories/Trends/Projects.
- **Strict Qwen adjudication**: pair/trend Pydantic schemas, evidence validation, prompt/model/input
  cache; невалидный ответ не влияет на clustering.
- **Cluster Lab sandbox**: отдельный `cluster_lab.db` для immutable data releases,
  experiments, story proposals и trend proposals без mutation production `stories`.
  CLI: `lab release create/list`, `lab experiment create`, `lab propose`, `lab compare/eval`.
- **Cluster Lab trend fallback**: trend proposals строятся не только из `item_signals`,
  но и из entity/title-topic buckets, чтобы sandbox работал на неполных/legacy DB.
- **Story/trend clustering research notes**: documented story identification vs topic modeling,
  entity-aware sparse+dense representation stack, eval metrics и roadmap.
- **Честные метрики clustering** (RADAR_CLUSTERING_IMPROVEMENT_TASK):
  `candidate_story_count`, `single_item_story_count`, `multi_item_story_count`,
  `cross_source_story_count`, `radar_ready_story_count`, `analyzed_coverage_ratio`,
  `compression_ratio` в RunSummary. Radar KPI показывает все 6 метрик.
  Warning при compression > 65% и analysis coverage < 95%.
- **normalize_title v2**: `Opinion | Real title - NYT` → использует правую часть;
  trailing publisher suffix (`- The New York Times`) удаляется; 16 provider aliases.
- **Generic/low-signal guards**: `is_generic_title()`, `is_low_signal_title()`.
  Generic titles (opinion, tech life, newsletter) не склеиваются по title-only.
  URL-based story_id для generic/low-signal материалов.
- **Deterministic canonical key**: `extract_ordered_tokens()` вместо `set→list`.
  `cluster_items()` детерминирован по story_ids.
- **Conservative clustering**: короткие заголовки (<4 tokens) без entity overlap
  требуют similarity ≥ 0.85 (было 0.72).
- **Radar-ready filtering**: `is_radar_ready()` — single-source материалы
  не доминируют в top аналитике, доступны в Explore.
- **Дизайн-система v2** (icreon.com palette через VPS + Wayback Machine):
  deep blue → purple → magenta gradient, Outfit (geometric sans-serif),
  ambient radial gradients, kinetic motion (reveal-up stagger, pulse-glow,
  spring easing), gradient text clip на KPI/scores, backdrop-filter nav.
  Обе темы: dark (deep navy #06080f) + light (#f6f8fa).
- **Broad Radar / trendwatching core**: стабильная taxonomy из 12 `domain_id`
  (`ai_technology`, `labor_career`, `business_markets`, `society_politics`,
  `world_geopolitics`, `culture_media`, `sports`, `science_health_education`,
  `finance_consumer`, `climate_energy_infrastructure`, `security_privacy`, `other`).
- **Default `broad` profile** (`config/profiles/broad.json`): широкие Reddit packs,
  broad keywords и goal profiles для книги, РБК и business signal.
- **SQLite schema v3**: новые поля `domain_ids`, `trend_id`, `lifecycle`,
  `project_scores`, `discussion_url`, `target_url`, `dedupe_group_id`, `evidence_refs`.
- **Radar workspace**: category tabs, category × source-cluster matrix, trend shelves,
  Broad/AI-native mode switcher, source-section coverage и domain labels.
- **Radar drill-down**: pain points, stable themes и emerging theme chips открывают
  `/explore` с сохранёнными `date/profile`; `/api/v2/stories` и `/api/v2/trends`
  поддерживают фильтры `pain` и `candidate_theme`.
- **API v2 additions**: `/api/v2/domains`, `/api/v2/radar/{date}`,
  `/api/v2/trends`, `/api/v2/trends/{trend_id}`,
  `/api/v2/projects/{project_id}/radar`.
- **Дизайн-система** (kinetic motion-first, dark tech aesthetic по описанию Awwwards
  icreon-digital-velocity): обе темы (dark default + light toggle с persistence),
  типографика Space Grotesk / Inter / JetBrains Mono, CSS design tokens, scroll-reveal,
  hover lift + glow, count-up KPI, ambient background с radial gradients.
- **Разделение Radar и Today**: `/today` — компактный briefing (3-5 изменений, что
  прочитать, кнопка в Radar); `/runs/{date}/radar` — полный аналитический workspace
  (KPI, LLM-анализ, relevance Книга/РБК, облака, сила трендов, мега-сюжеты, raw
  popularity, охват источников); `/radar` — redirect на последний Radar.
- **Legacy routes**: `/legacy/dashboard`, `/legacy/runs/{date}/radar` — старые
  renderers на один переходный релиз.
- **Intelligence layer** (`src/reddit_compass/intelligence/`): source-agnostic domain models
  (ContentItem, Story, Briefing), SQLite v2 migrations, story clustering (rapidfuzz),
  ranking (goal relevance, cross-source coverage, momentum, novelty, evidence quality),
  deterministic briefing.
- **Unified run** (`reddit-compass run`): единая команда для сбора из указанных источников.
  Флаги: `--sources reddit,hn,rss`, `--profile`, `--analyze`, `--allow-partial`.
- **Source registry** (`sources/registry.py`): 22 источника с метаданными (provider, cluster,
  access, required env). NYT API и WSJ: `enabled_by_default=False`.
- **NYT adapter** (`sources/nytimes.py`): Top Stories API + Article Search API.
  Требует `NYT_API_KEY`. Без ключа: status `not_configured`.
- **Web UI** (Jinja2): `/today` (briefing), `/stories/{id}` (detail + timeline),
  `/explore` (search/filter/pagination), `/runs` (history). Research actions с CSRF.
  Security headers: CSP, X-Content-Type-Options, Referrer-Policy.
- **API v2** (`/api/v2/`): briefings, stories, runs, source-health, PATCH research-state.
  Pydantic schemas. v1 остаётся совместимым.
- **LLM validation** (`llm_schemas.py`): Pydantic schemas для валидации ответов Qwen.
  Stratified selection (70% clusters, 20% global, 10% exploration). Retry policy.
- **Profile schema v2**: goals, themes в `config/profiles/ai-native.json`.
  Совместимость с v1.
- **CLI: `db rebuild`**: перестройка SQLite v2 из snapshots. Идемпотентен,
  research_state переживает rebuild.

### Changed

- `collect` теперь является collection-only runtime и не импортирует clustering, ranking,
  briefing или LLM; `run` временно остаётся compatibility alias.
- Radar и Today читают только текущий immutable publication pointer. Если новая версия не
  опубликована, UI сохраняет предыдущую проверенную публикацию и показывает предупреждение.
- `cluster_lab.db` и `lab` CLI deprecated на один переходный релиз; безопасный импорт разрешён
  только при точном совпадении checksum исходного корпуса.
- `reddit-compass run --analyze` теперь создаёт `item_signals` для каждого item
  через deterministic facets layer; Radar больше не показывает фальшивый блок анализа,
  если разметок `0`.
- Item count в UI считается через `observations`, а не через `items.snapshot_date`.
- Hacker News adapter собирает front page/new/weekly-top перед keyword search, поэтому
  больше не является только AI-keyword источником.
- RSS adapter расширен до section-level coverage: BBC, Guardian, Reuters, NYT/WaPo
  via Google News RSS, FT/Fox Business/USA Today и tech/culture sources.
- Reddit link-post теперь хранит два URL: `discussion_url` и внешний `target_url`;
  canonical URL для link-post — внешний target, чтобы RSS/HN могли склеиться с Reddit.
- Story clustering использует canonical/target URL, нормализованный title/entity overlap
  и recent history; current-run ranking больше не тащит historical item_ids в метрики run.
- **`REDDIT_COMPASS_ENGINE`** (`auto|playwright`): выбор движка Reddit-запросов.
  `playwright` пропускает aiohttp-попытку и сразу стартует Chromium — основной режим
  для ротационных residential proxy, где Reddit отдаёт 403 голому HTTP с pool-IP,
  но обслуживает браузерный трафик (проверено на IPRoyal 2026-07-27).
- **`fetch-and-sync.sh`**: страховка домашнего IP — чередование маршрута Reddit:
  чётные дни = домашний IP, нечётные = IPRoyal proxy (движок playwright); скрипт
  сам source'ит `deploy/hostkey/.env.secrets`. Override — `RC_PROXY_MODE=on|off`.
- **ROADMAP Phase 7**: план переноса Reddit-fetch на VPS (whitelist/sticky IPRoyal,
  разнесение тегов образов api/collector, сборка collector в deploy.sh).
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

- **Деплой:** теги образов разнесены — `reddit-compass-api:latest` (slim) и
  `reddit-compass-collector:latest` (Playwright/Chromium); `deploy.sh` собирает оба образа.
  Раньше сервисы делили `reddit-compass:latest`, и `up -d api` перезаписывал тег
  slim-образом — collector с Chromium на VPS не собирался никогда.
- **RedditBrowser** (client.py): retry на транзитивные сетевые ошибки (`Failed to fetch` —
  ротация exit IP у residential proxy) и retry `goto` на новой странице (новое соединение —
  другой exit IP у ротационного proxy).
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
