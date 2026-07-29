# Архитектура reddit-compass

> **Каноническое описание системы.** Границы, контракты, потоки данных, деплой.

---

## 1. Миссия

reddit-compass — **трендовый радар**: собирает «голос людей», «голос разработчиков»,
«голос СМИ», «голос бизнеса» и «product pulse», раскладывает материалы по стабильной
broad taxonomy и показывает, **куда смотреть**.

```
    🌐 Reddit packs    💬 Hacker News       📰 RSS/Ladder СМИ      🚀 ProductHunt
    broad profile      Algolia front/search sections + fallback    GraphQL/feed
         │                   │                    │                     │
         ▼                   ▼                    ▼                     ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                   два независимых runtime внутри репозитория             │
    │                                                                         │
    │   Collector → compass.db ──snapshot──► Engine → publication → UI         │
    └─────────────────────────────────────────────────────────────────────────┘
         │                   │                    │                     │
         ▼                   ▼                    ▼                     ▼
    JSONL snapshots     compass.db          trend_engine.db      REST API
    (обмен)             (raw facts)         (versions/pointers)  (FastAPI :8900)
```

**Главный инвариант:** сервис собирает данные и генерирует артефакты. Потребители
(книга, колонки, дайджест, практикум) читают артефакты, но не зависят от рантайма.
Collector не импортирует анализ, а Engine не запускает source adapters и не изменяет
`compass.db`. Radar и Today читают только опубликованную immutable-комбинацию.

---

## 2. Источники, кластеры и broad taxonomy

Данные идут через шесть source clusters:

- `voices`: Reddit, Medium.
- `developers`: Hacker News.
- `mainstream`: BBC, Guardian, NYT/WaPo/USA Today/Fox News via RSS/Ladder.
- `business`: Reuters, FT, Fox Business, American Banker.
- `tech_culture`: TechCrunch, Verge, Ars Technica, Wired, New Yorker, Vanity Fair.
- `product_pulse`: ProductHunt.

Каждый item/story получает один или несколько `domain_ids`:
`ai_technology`, `labor_career`, `business_markets`, `society_politics`,
`world_geopolitics`, `culture_media`, `sports`, `science_health_education`,
`finance_consumer`, `climate_energy_infrastructure`, `security_privacy`, `other`.

Default collection profile: `config/profiles/broad.json`.
`config/profiles/ai-native.json` остаётся отдельным узким профилем/линзой.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         КЛАСТЕРЫ ИСТОЧНИКОВ                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  📰 Мейнстрим (6)          💰 Бизнес (4)          🔬 Tech/культура (6)      │
│  ┌───────────────────┐    ┌──────────────────┐   ┌───────────────────────┐  │
│  │ NYT      (Ladder) │    │ FT       (Ladder)│   │ Wired      (Ladder)   │  │
│  │ WaPo     (Ladder) │    │ AmBanker (Ladder)│   │ New Yorker (Ladder)   │  │
│  │ Time     (Ladder) │    │ FoxBiz   (Ladder)│   │ VanityFair (Ladder)   │  │
│  │ USAToday (Ladder) │    │ Reuters  (RSS)   │   │ TechCrunch (RSS)      │  │
│  │ BBC      (RSS)    │    └──────────────────┘   │ The Verge  (RSS)      │  │
│  │ Guardian (RSS)    │                           │ Ars Tech   (RSS)      │  │
│  └───────────────────┘                           └───────────────────────┘  │
│                                                                             │
│  🗣 Голоса (3)             📊 Пульс (2)                                     │
│  ┌───────────────────┐    ┌──────────────────┐                              │
│  │ Reddit (Playwright)│    │ Fox News (Ladder)│                              │
│  │ HN     (Algolia)  │    │ ProductHunt (API)│                              │
│  │ Medium (Ladder)   │    └──────────────────┘                              │
│  └───────────────────┘                                                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Адаптеры и движки

```
┌────────────────────────────────────────────────────────────────────┐
│                        RedditEngine                                 │
│                                                                    │
│   ┌──────────────┐    403?     ┌──────────────┐    fail?          │
│   │   aiohttp    │ ─────────►  │  Playwright   │ ─────────►  RSS   │
│   │  (primary)   │  fallback   │  (Chromium)   │  fallback         │
│   │  лёгкий HTTP │             │  headless     │  (hot only)       │
│   └──────────────┘             └──────────────┘                    │
│                                                                    │
│   ProxyRotator: REDDIT_COMPASS_PROXIES (round-robin, для 429)      │
│   REDDIT_COMPASS_ENGINE: auto|playwright (playwright — ротационный │
│   residential proxy, где голый HTTP получает 403 с pool-IP)        │
│   Stealth: jitter 3–6с + exponential backoff (nightly)             │
└────────────────────────────────────────────────────────────────────┘

┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐
│  sources/rss.py  │  │ sources/ladder.py│  │ sources/producthunt  │
│  aiohttp + XML   │  │ Ladder proxy     │  │ GraphQL API          │
│  6 фидов         │  │ 12 доменов       │  │ Developer Token      │
└──────────────────┘  └──────────────────┘  └──────────────────────┘
```

---

## 4. Поток данных

Collection и analysis являются независимыми jobs. Текущий nightly остаётся переходным
оркестратором; целевой cron сначала завершает `collect`, затем создаёт Data Release и запускает
Engine в shadow-канале. Отсутствие LLM не меняет collection status.

```
  03:17 (Mac, launchd)                    04:00 (VPS, host-cron)
  ─────────────────────                    ───────────────────────
  ┌─────────────────────┐                  ┌─────────────────────┐
  │  fetch-and-sync.sh  │                  │  docker compose run  │
  │                     │                  │                     │
  │  1. reddit fetch    │                  │  1. rss             │
  │     (Playwright,    │                  │  2. hn              │
  │      stealth,       │                  │  3. ladder          │
  │      residential IP)│                  │  4. signals (Qwen)  │
  │  2. hn (Algolia)   │                  │                     │
  │  3. rss (6 фидов)  │                  │                     │
  │  4. signals (Qwen) │                  │                     │
  │  5. scp → VPS      │                  │                     │
  └────────┬────────────┘                  └────────┬────────────┘
           │                                        │
           ▼                                        ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    VPS: /opt/reddit-compass/data/                 │
  │                                                                  │
  │  snapshots/2026-07-22/                                           │
  │  ├── posts.jsonl          (Reddit, 400+ постов)                  │
  │  ├── hackernews.jsonl     (HN, 50+ stories)                      │
  │  ├── rss.jsonl            (BBC/Guardian/TC/Verge/Ars, 50+)       │
  │  ├── ladder.jsonl         (NYT/WaPo/FT/Wired/Medium, 10+)        │
  │  ├── producthunt.jsonl    (PH, 20 продуктов)                     │
  │  ├── virality.jsonl       (crosspost/surge сигналы)              │
  │  ├── signals.jsonl        (LLM: pain points, relevance)          │
  │  ├── trends-report.md     (тренды по кластерам)                  │
  │  └── signals-report.md    (LLM-синтез: темы, идеи для колонок)   │
  │                                                                  │
    │  compass.db               (raw collection facts)                 │
    │  trend_engine.db          (immutable analysis versions)          │
    │    └── published read models: News, Stories, Trends, Project Lens│
    │  cluster_lab.db           (deprecated compatibility DB)          │
  │  notifications/           (заготовки для Telegram/email)          │
  └─────────────────────────────────────────────────────────────────┘
           │
           ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    rc-api (FastAPI, :8900)                        │
  │                                                                  │
  │  GET /health              GET /api/v1/posts?date=&subreddit=     │
  │  GET /today               GET /api/v1/signals?date=              │
  │  GET /radar               GET /api/v1/stats                      │
  │  GET /runs/{date}/radar   GET /api/v2/briefings/{date}           │
  │  GET /explore             GET /api/v2/stories                    │
  │  GET /stories/{id}        GET /api/v2/runs                       │
  │  GET /runs                PATCH /api/v2/stories/{id}/research    │
  │  GET /docs (Swagger)      POST /oauth/token                      │
  │  GET /legacy/dashboard    GET /api/v1/snapshots                  │
  │                                                                  │
  │  Auth: OAuth2 client credentials → JWT (1h)                      │
  │  CORS: cheap-intelligence.vercel.app (Practicum)                 │
  │  UI: Jinja2 + dark/light theme + kinetic motion                  │
  └─────────────────────────────────────────────────────────────────┘
           │
           ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    Потребители                                    │
  │                                                                  │
  │  📖 Книга (3 тома)     ← harvests, signals-report                │
  │  📰 Колонки РБК        ← trends-report, column_ideas             │
  │  🌐 Practicum (Vercel) ← REST API (OAuth2)                       │
  │  📧 Дайджест           ← notifications/ (будущий sender)          │
  │  💬 Telegram           ← notifications/ (будущий sender)          │
  └─────────────────────────────────────────────────────────────────┘
```

---

### UI drill-down contract

- Radar chips are navigational, not decorative: stable themes, emerging themes and pain points link to `/explore`.
- Drill-down links preserve run context: `date` + `profile`.
- Theme filters use `theme=<theme_id>`; candidate theme filters use `candidate_theme=<candidate label>`; pain filters use `pain=<normalized pain label>`.
- `/explore` and `/api/v2/stories` resolve `theme`, `candidate_theme` and `pain` through
  `story_items → item_signals`, so the UI returns deduplicated stories, not raw item duplicates.

### Story/Trend Engine contract

- `trend_engine.db` содержит frozen Data Releases и отдельные Facet/Story/Trend attempts.
- Engine открывает `compass.db` только `mode=ro`; finalized release защищён SQLite triggers.
- Повторный запуск создаёт новую версию и не удаляет предыдущие результаты.
- Story и Trend можно пересчитывать независимо; full rebuild не входит в workflow.
- `broad`/`ai-native` публикуются только после Golden Set gates и ручного решения.
- Publish/rollback атомарно переключают immutable pointer.
- Старый `cluster_lab.db` и `lab` CLI — compatibility alias на один релиз.

Полный контракт: [`docs/TREND_ENGINE.md`](docs/TREND_ENGINE.md).

---

## 5. Деплой

```
┌─── Mac (DenisErmilov) ─────────────────────────────────────────────┐
│                                                                     │
│  ~/aiprojects/reddit-compass/                                       │
│  ├── scripts/fetch-and-sync.sh    ← nightly (launchd, 03:17)       │
│  ├── data/snapshots/              ← локальные данные                │
│  └── .env                         ← DASHSCOPE_API_KEY               │
│                                                                     │
│  Роль: Reddit fetch (домашний IP / IPRoyal по дням) + sync на VPS  │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ scp (после каждого прогона)
                              ▼
┌─── VPS HostKey «Hermes» (204.168.239.217) ─────────────────────────┐
│                                                                     │
│  /opt/reddit-compass/                                               │
│  ├── docker-compose.yml          ← 3 сервиса                       │
│  │   ├── rc-api                  ← FastAPI :8900 (restart: always)  │
│  │   ├── rc-caddy                ← reverse proxy (loopback)         │
│  │   └── rc-collector            ← batch (host-cron, не daemon)     │
│  ├── Dockerfile                  ← Playwright (batch)               │
│  ├── Dockerfile.api              ← Slim (API, без Chromium)         │
│  ├── Caddyfile                   ← :80 → api:8900                   │
│  ├── .env                        ← секреты (gitignored)             │
│  └── data/                       ← snapshots + обе SQLite DB       │
│                                                                     │
│  Security: read_only, no-new-privileges, cap_drop ALL, pids_limit   │
│  Network: loopback only (127.0.0.1:8900), без публичных портов      │
│                                                                     │
│  Роль: API + RSS + HN + Ladder + хранение данных                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Контракты данных

### PostCard (единая схема для всех источников)

```python
@dataclass
class PostCard:
    subreddit: str          # имя источника ("artificial", "hackernews", "nytimes")
    post_id: str            # уникальный ID
    title: str
    author: str
    created_utc: str | None
    score: int              # votes/points (0 для RSS/Ladder)
    upvote_ratio: float
    num_comments: int
    url: str
    selftext: str           # текст/описание (до 5000 символов)
    link_flair_text: str | None  # кластер источника
    is_self: bool
    permalink: str
    monitoring_type: str    # "hot"|"top"|"search"|"rss"|"ladder"|"api"
    snapshot_date: str      # "YYYY-MM-DD"
    keyword: str | None     # поисковый запрос / кластер
    top_comments: list[CommentCard]
    crosspost_parents: list[str]
```

### SignalCard (LLM-анализ)

```python
@dataclass
class SignalCard:
    post_id: str
    pain_points: list[str]      # боли/проблемы
    buying_intent: bool         # намерение купить AI-продукт
    business_relevance: int     # 1–10
    book_relevance: int         # 1–10
    themes: list[str]           # ключевые темы
    summary: str                # 1 предложение
```

---

## 7. Rate limiting и этика

| Правило | Значение |
|---|---|
| Пауза между запросами | 4с (обычный), 3–6с jitter (stealth) |
| Retry на 429 | 2 раза, backoff 10с → 20с (stealth: ×2^attempt) |
| Read-only | Не постим, не голосуем, не комментируем |
| Данные | Только публичные посты и комментарии |
| Proxy | Только для снижения 429, не для обхода банов |
| ML-обучение | Запрещено использовать контент для обучения моделей |

### Reddit Official API: доступ не получен

Заявка на Reddit Data API подана 2026-07-22. Статус: **SUBMITTED_AWAITING_REDDIT_REVIEW**
(фактически — без ответа). Reddit не предоставил OAuth-доступ.

Сервис работает через **Playwright JSON API** (headless Chromium → публичные .json endpoints,
без credentials). Это легально для личного research (публичные данные, read-only, rate-limited).

При одобрении заявки — переход на `asyncpraw` (100 req/min, 100% ToS-чистота).
До тех пор: Playwright + residential IP + stealth + proxy (для 429).
Создание script app на `reddit.com/prefs/apps` заблокировано для молодого
аккаунта (повторить, когда аккаунт наберёт возраст/карму).

**Статус на 2026-07-27 (проверено live):** Reddit выборочно блокирует
анонимные `.json` — 403 зависит от репутации IP. С чистого residential IP
голый aiohttp работает; с pool-IP ротационных proxy (IPRoyal) — почти всегда
403, тогда как браузерный трафик (Playwright) обслуживается стабильно
(goto 3/3, `.json` 200; редкие `Failed to fetch` — ротация exit IP
по соединениям, лечится sticky-сессией провайдера + retry в движке).
Анонимные OAuth grant мертвы (401 без client_id); `token_v2` из браузерной
сессии работает как Bearer на `oauth.reddit.com`, но добывается только
браузером — выигрыша над чистым Playwright нет. Для прогонов через
ротационный proxy: `REDDIT_COMPASS_ENGINE=playwright`.

---

## 8. CLI: все команды

```
reddit-compass fetch [--stealth] [--dry-run]   Reddit hot/top (Playwright)
reddit-compass search                          Keyword search
reddit-compass track                           Tracked threads
reddit-compass virality                        Crosspost/surge detection
reddit-compass report                          Markdown из snapshot
reddit-compass all                             fetch + search + track + virality + report
reddit-compass nightly                         all + trends + stealth
reddit-compass signals                         LLM-анализ (Qwen API)
reddit-compass hn                              Hacker News (Algolia)
reddit-compass rss                             RSS (BBC, Guardian, Reuters, TC, Verge, Ars)
reddit-compass ladder                          Ladder (NYT, WaPo, FT, Wired, Medium...)
reddit-compass ph                              ProductHunt (GraphQL)
reddit-compass collect --profile broad         Только сбор в compass.db
reddit-compass engine release create --run ID  Frozen Data Release
reddit-compass engine facets --release ID      FacetRelease
reddit-compass engine stories propose ...      Новый StoryRelease attempt
reddit-compass engine trends propose ...       Новый TrendRelease attempt
reddit-compass engine publish ...              Ручной pointer switch
reddit-compass engine rollback ...             Возврат pointer
reddit-compass db init|stats                   SQLite
reddit-compass serve                           REST API (FastAPI :8900)
```

---

## 9. Переносимость

| Область | Где | При переносе |
|---|---|---|
| Код | `src/reddit_compass/` | Переносится целиком |
| Профили | `config/profiles/*.json` | Переносятся |
| Данные | `data/` (JSONL + SQLite + MD) | Volume / scp |
| Docker | `Dockerfile`, `Dockerfile.api`, compose | Переносятся |
| VPS-стек | `deploy/hostkey/` | App-owned `/opt/reddit-compass` |
| Секреты | `.env`, `.env.secrets` | Вручную, НЕ в git |
| Скрипты | `scripts/` | Mac-specific (launchd) |

---

## 10. Документация

| Документ | Тема |
|---|---|
| [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md) | Полная схема SQLite: таблицы, колонки, индексы, примеры запросов |
| [`docs/PRODUCT_IMPLEMENTATION_PLAN.md`](docs/PRODUCT_IMPLEMENTATION_PLAN.md) | Продуктовый план: модели, ranking, UI, API |
| [`docs/TRENDWATCHING_DEEP_REVIEW_AND_V2_PLAN.md`](docs/TRENDWATCHING_DEEP_REVIEW_AND_V2_PLAN.md) | Глубокое ревью trendwatching + план V2 |
| [`docs/RADAR_TRENDWATCHING_IMPLEMENTATION.md`](docs/RADAR_TRENDWATCHING_IMPLEMENTATION.md) | Реализация broad Radar |
| [`CHANGELOG.md`](CHANGELOG.md) | Keep a Changelog |
