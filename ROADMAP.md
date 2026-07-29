# ROADMAP

reddit-compass растёт от автономного коллектора трендов к «навигатору сигналов». Фазы независимы;
порядок — ориентир, не жёсткая последовательность.

> Ранжированный план улучшений (анализ конкурентов, источники, rationale) —
> в [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md). Конкурентный анализ (ландшафт GitHub,
> таблицы фич) — в [docs/COMPETITIVE_ANALYSIS.md](docs/COMPETITIVE_ANALYSIS.md).

## v0.1 — ядро (готово)

- Выделение в отдельный репозиторий, отвязка от монорепо, config-driven профили.
- Движок Playwright JSON API + RSS fallback.
- Сбор: `fetch / search / track / virality / report / all / nightly`.
- Детекция виральности (crosspost / score_surge / multi_subreddit).
- Ночной разбор трендов (config-driven, по кластерам профиля).
- Обвязка: uv, ruff, mypy (strict), pytest, pre-commit, CI; Docker; скелет VPS-деплоя.

## v0.3 — intelligence layer + UI (готово, 2026-07-27)

- **Intelligence layer**: source-agnostic models (ContentItem, Story, Briefing),
  SQLite v2 (10 таблиц), story clustering (rapidfuzz), ranking (5 компонентов),
  deterministic briefing, LLM validation (Pydantic schemas).
- **Unified run**: `reddit-compass run --sources reddit,hn,rss,ladder,ph`.
- **Source registry**: 22 источника с метаданными. NYT API adapter.
- **Web UI** (Jinja2): `/today` (компактный бриф), `/runs/{date}/radar` (полный
  аналитический workspace), `/explore` (поиск/фильтры), `/stories/{id}` (research).
- **API v2**: briefings, stories, runs, source-health, PATCH research-state.
- **Дизайн-система**: kinetic motion-first, dark tech aesthetic, обе темы
  (dark default + light toggle), Space Grotesk / Inter / JetBrains Mono.
- **Карточки**: прямой переход на primary evidence, читаемые кластеры и источники.
- **Legacy**: `/legacy/dashboard`, `/legacy/runs/{date}/radar` на переходный релиз.
- **CLI**: `db rebuild` — перестройка SQLite v2 из snapshots.
- 268 тестов, coverage 71%, ruff/mypy strict зелёные.

## v0.4 — Broad Radar / trendwatching core (готово, 2026-07-28)

- **Broad taxonomy в коде**: 12 стабильных `domain_id` для AI/tech, труда,
  бизнеса, политики, мира, культуры, спорта, науки/здоровья/образования,
  потребителя, климата/инфраструктуры, security/privacy и fallback `other`.
- **Default profile `broad`**: широкий Reddit corpus + RSS sections + HN front/search.
  `ai-native` сохранён как отдельная линза/профиль.
- **SQLite schema v3**: `domain_ids`, `trend_id`, `lifecycle`, `project_scores`,
  `discussion_url`, `target_url`, `dedupe_group_id`, `evidence_refs`.
- **Radar workspace**: category tabs, category × source-cluster matrix, shelves
  (`new`, `growing`, `resurfacing`, `undercovered`, cross-source confirmed),
  project panels, theme clouds, pain points, mega stories и raw popular.
- **Signal fixes**: `run --analyze` создаёт `item_signals`, Radar не показывает
  фальшивый LLM-анализ при `0` разметок, item count считается через `observations`.
- **Dedup/continuity fixes**: Reddit `target_url` отделён от `discussion_url`;
  clustering использует canonical/target URL, историю последних runs и обрезает
  story до current-run items перед ranking.
- 271 тест проходит; ruff, format-check, mypy зелёные.

## v0.5 — Versioned Story/Trend Engine (реализовано локально, shadow rollout pending)

- Collector отделён от анализа: `collect` пишет только raw facts в `compass.db`.
- `trend_engine.db`: frozen Data Releases и независимые Facet/Story/Trend attempts.
- Hybrid Story Engine: URL/BM25/entities/optional E5/time/conflicts, constrained clustering,
  stable story IDs и merge/split provenance.
- Trend Engine работает только со stories; минимум три события и два дня, entity-only
  buckets запрещены.
- Строгие Qwen pair/trend review schemas с evidence IDs и cache.
- Golden Set export/import и publication gates по precision/recall/overmerge/evidence.
- `/engine`, Engine API, publication-backed `/news`, `/stories`, `/trends`, `/projects/{id}`,
  `/radar` и `/today`.
- Publish/rollback переключают immutable pointer; `lab` остаётся deprecated alias.
- Следующий gate: 50/100/300 real-item проверки, полный локальный release и семь shadow days.

## Phase 2 — планировщик на VPS (HostKey «Hermes»)

- App-owned compose-стек `/opt/reddit-compass`, изолированный от прочих стеков (своя сеть + volume).
- Batch-job: без публичного порта; resource limits, `no-new-privileges`, ротация логов.
- Целевое расписание — независимые host-cron jobs `collect` и `engine`; до shadow rollout
  существующий nightly остаётся compatibility orchestrator.
- Регистрация владельца/контейнера/volume/backup в `vps_management`.
- Скелет: [deploy/hostkey/](deploy/hostkey/). Включение — по подтверждению, со сверкой живого HostKey.
- **Docker CI/CD:** GitHub Actions → build & push образа в GHCR; VPS тянет из registry.

## Phase 2.5 — dry run

- `--dry-run` для `fetch / search / all`: показать, что соберётся (субреддиты, ключевые слова,
  примерный объём), без записи в `data/`. Быстрый win для проверки изменений профиля.

## Phase 3 — LLM-анализ сигналов (`signals.py`)

- Проход Claude по постам snapshot → извлечение: pain points, buying intent, competitor mentions,
  lead-score (фича, вдохновлённая Reddit_Scrapper ⭐198 — «GPT-анализ маркетинговых болей»).
- Вход: `posts.jsonl`; выход: `signals.jsonl` + секция в отчёте.
- Anthropic SDK; модель — последняя (Haiku 4.5 для bulk-классификации, Sonnet 5 для синтеза), ID
  сверять через актуальную документацию. Ключ — `ANTHROPIC_API_KEY`.
- Заодно: сделать `trends_analysis.py` полностью profile-driven (редакторские подсказки — в профиль).
- **Multi-dimensional scoring:** бизнес-релевантность, связь с темой книги, техническая глубина —
  оценки LLM по шкале 1–10 поверх score/num_comments (вдохновлено Reddit_Scrapper ⭐198).

## Phase 3.5 — SQLite-хранилище

- Аддитивно к JSONL (JSONL остаётся как формат обмена): `data/compass.db`.
- Таблицы: posts, comments, virality_signals, signals (LLM), snapshots.
- Запросы: «все посты по AI за месяц», «топ по score за неделю», «тренды по неделям»,
  «динамика нарратива за 3 месяца». Исторические данные для книги.
- CLI: `reddit-compass db init / migrate / query`.

## Phase 4 — уведомления

- Дайджест топ-сигналов в Telegram/email после nightly-прогона.
- Переиспользовать telegram-скрипты и email-паттерны из соседних репозиториев.

## Phase 5 — веб-дашборд

- Read-only просмотр отчётов и сигналов в editorial-стиле (спокойный, статусный).
- Тогда к batch-стеку добавляется веб-контейнер: loopback-порт + внешний Caddy SNI `:443`.

## Phase 6 — мульти-источники

Расширение за пределы Reddit: единый конвейер «сбор → JSONL → trends analysis» для нескольких
источников. Деплой — на VPS HostKey «Hermes» (app-owned стек, рядом с reddit-compass).

> **Детальный план:** [docs/MULTI_SOURCE_PLAN.md](docs/MULTI_SOURCE_PLAN.md) — архитектура,
> все СМИ из Ladder ruleset, пошаговая реализация, деплой, метрики успеха.

**Источники (приоритет):**

| # | Источник | API/доступ | Ценность |
|---|---|---|---|
| 1 | **Hacker News** | Algolia API (бесплатно, без ключей) | AI-стартапы, «голос разработчика» |
| 2 | **NYT / WaPo / FT / Wired** | Ladder proxy (paywall bypass) | Бизнес-нарратив, «что пишут СМИ» |
| 3 | **Medium** | Referer-трюк (t.co/amp) | Лонгриды про AI, кейсы |
| 4 | **ProductHunt** | GraphQL API (бесплатно) | Новые AI-продукты |
| 5 | **IndieHackers** | RSS + HTML parse | «Один человек + AI = компания» |

**Инфраструктура:**

- **Ladder** (⭐8.7k, Go) — self-hosted proxy на HostKey: per-domain ruleset (UA, cookies,
  paywall removal, FlareSolverr для Cloudflare). Docker-контейнер, loopback-порт.
- Source-адаптеры: `sources/reddit/`, `sources/hackernews/`, `sources/news/` — каждый со своим
  клиентом, общий выход в JSONL (поле `source`).
- `trends_analysis.py` уже source-agnostic (читает JSONL) — расширение минимально.

**Ограничения:**

- WSJ / Bloomberg / The Economist — серверный paywall, Ladder не поможет. Только RSS-заголовки
  или подписка.
- Twitter/X — API платный ($100/мес). Отложить до обоснования ROI.

## Phase 7 — Reddit-fetch на VPS (план; активация после ответа IPRoyal)

Цель: ночной сбор Reddit переезжает с Mac на VPS — расписание не зависит от того,
включён ли ноутбук. Mac остаётся резервным маршрутом.

**Проверено 2026-07-27 (live):**

- Datacenter IP VPS: `.json` = 403 даже в headless-браузере (Reddit режет DC-IP).
- VPS + IPRoyal: TCP-таймаут — residential endpoint пускает только whitelisted
  source IP (сейчас — домашний IP Mac).
- Browser-путь через IPRoyal с Mac: работает (200); голый HTTP с pool-IP — 403,
  поэтому за ротационным proxy нужен `REDDIT_COMPASS_ENGINE=playwright` (реализовано).

**Шаги (по порядку):**

1. **IPRoyal (тикет открыт):** whitelist IP VPS `204.168.239.217` (или переход
   на username/password-аутентификацию) + sticky-эндпоинт (один exit IP на ~20 мин —
   убирает транзитивные `Failed to fetch` от ротации IP по соединениям).
2. **Деплой-фиксы (обязательное условие):**
   - разнести теги образов: `reddit-compass-api:latest` / `reddit-compass-collector:latest`
     (сейчас оба сервиса делят `reddit-compass:latest`, и `up -d api` перезаписывает
     тег slim-образом без Chromium — проверено: collector на VPS не собирался никогда);
   - добавить сборку collector-образа в `deploy/hostkey/deploy.sh`;
   - `REDDIT_COMPASS_PROXIES` уже в `.env.secrets` → после деплоя попадает в `.env` VPS.
3. **VPS cron:** `docker compose run --rm reddit-compass fetch --stealth`
   с `REDDIT_COMPASS_ENGINE=playwright` в окружении сервиса; проверка стабильности
   (недели прогона, полнота snapshot).
4. **Переключение:** после серии стабильных прогонов — снизить роль Mac launchd
   до резервной (ручной запуск при сбоях VPS/proxy).

**Фон (без сроков):** OAuth/`asyncpraw` — после созревания аккаунта (prefs/apps
заблокирован для молодых аккаунтов) или одобрения официальной заявки Data API;
Arctic Shift — как дополнительный исторический источник.

## Технический долг

- Поднять порог покрытия тестами (сейчас гейт 60%, реально ~75%; сеть/браузер/оркестрация вне гейта).
- **Reddit Official API: ДОСТУП НЕ ПОЛУЧЕН.** Заявка на Reddit Data API подана 2026-07-22
  (статус: SUBMITTED_AWAITING_REDDIT_REVIEW → фактически игнорируется Reddit). Сервис работает
  через Playwright JSON API (публичные данные, без credentials). При одобрении — переход на
  asyncpraw (100 req/min, 100% ToS). До тех пор: Playwright + residential IP + stealth.
- **Exploratory subreddits:** если пост из нового сабреддита виральный → предложить добавить
  в monitoring (вдохновлено Reddit_Scrapper ⭐198). Опция, не ядро.
- Proxy-ротация реализована (9d912d8); при 429 на VPS — SSH-туннель через HostKey или
  tinyproxy. OAuth API — когда Reddit одобрит заявку.
- Убрать r/deepfakes из профиля (404, мёртвый сабреддит).
