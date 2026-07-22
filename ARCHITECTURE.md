# Архитектура reddit-compass

> **Каноническое описание системы.** Границы, контракты, потоки данных, деплой.

---

## 1. Миссия

reddit-compass — **трендовый радар**: собирает «голос улицы», «голос разработчика», «голос СМИ»
и «голос рынка» из 21 источника, анализирует через LLM и показывает, **куда смотреть**.

```
    🌐 Reddit          💬 Hacker News       📰 СМИ (NYT, FT...)    🚀 ProductHunt
    18 сабреддитов     Algolia API          12 via Ladder          GraphQL API
         │                   │                    │                     │
         ▼                   ▼                    ▼                     ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                     reddit-compass (единый конвейер)                     │
    │                                                                         │
    │   collect → store → analyze → report → notify → serve                   │
    └─────────────────────────────────────────────────────────────────────────┘
         │                   │                    │                     │
         ▼                   ▼                    ▼                     ▼
    posts.jsonl         compass.db          signals.jsonl        REST API
    (JSONL обмен)       (SQLite история)    (LLM-синтез)         (FastAPI :8900)
```

**Главный инвариант:** сервис собирает данные и генерирует артефакты. Потребители
(книга, колонки, дайджест, практикум) читают артефакты, но не зависят от рантайма.

---

## 2. Источники (21, пять кластеров)

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
│   Stealth: jitter 3–6с + exponential backoff (nightly)             │
└────────────────────────────────────────────────────────────────────┘

┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐
│  sources/rss.py  │  │ sources/ladder.py│  │ sources/producthunt  │
│  aiohttp + XML   │  │ Ladder proxy     │  │ GraphQL API          │
│  6 фидов         │  │ 12 доменов       │  │ Developer Token      │
└──────────────────┘  └──────────────────┘  └──────────────────────┘
```

---

## 4. Поток данных (nightly)

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
  │  compass.db               (SQLite: вся история)                  │
  │  notifications/           (заготовки для Telegram/email)          │
  └─────────────────────────────────────────────────────────────────┘
           │
           ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    rc-api (FastAPI, :8900)                        │
  │                                                                  │
  │  GET /health              GET /api/v1/posts?date=&subreddit=     │
  │  GET /dashboard           GET /api/v1/signals?date=              │
  │  GET /docs (Swagger)      GET /api/v1/stats                      │
  │  POST /oauth/token        GET /api/v1/snapshots                  │
  │                                                                  │
  │  Auth: OAuth2 client credentials → JWT (1h)                      │
  │  CORS: cheap-intelligence.vercel.app (Practicum)                 │
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

## 5. Деплой

```
┌─── Mac (DenisErmilov) ─────────────────────────────────────────────┐
│                                                                     │
│  ~/aiprojects/reddit-compass/                                       │
│  ├── scripts/fetch-and-sync.sh    ← nightly (launchd, 03:17)       │
│  ├── data/snapshots/              ← локальные данные                │
│  └── .env                         ← DASHSCOPE_API_KEY               │
│                                                                     │
│  Роль: Reddit fetch (residential IP) + LLM + sync на VPS           │
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
│  └── data/                       ← volume (snapshots + compass.db)  │
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
