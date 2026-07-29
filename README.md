# 🧭 reddit-compass

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-green.svg)](https://www.python.org/)
[![CI](https://github.com/eiler2005/reddit-compass/actions/workflows/ci.yml/badge.svg)](https://github.com/eiler2005/reddit-compass/actions)
[![Docker](https://github.com/eiler2005/reddit-compass/actions/workflows/docker.yml/badge.svg)](https://github.com/eiler2005/reddit-compass/actions)

**Your broad trendwatching radar. 12 stable domains, multi-source evidence, one compass.**

---

You're writing a book about how AI is changing work. Or running a column. Or building a product and need to know what the market *actually* thinks — not what press releases say.

So you check Reddit. Then Hacker News. Then NYT. Then Wired. Then FT. Then TechCrunch. Then Medium. Then ProductHunt. Every. Single. Morning.

That's 45 minutes of tab-switching before your first coffee. And you *still* miss the thread that went viral at 2 AM — the one where 400 laid-off engineers described exactly the pain your product solves.

**reddit-compass does this for you. Every night. Broad Radar watches the world; AI-native Lens
keeps book/RBC research focused on AI, work, institutions and markets.**

---

## What reddit-compass does for you

### Every night, on its own

Runs in the Qwen discount window (**17:00–03:00 Moscow = 14:00–00:00 UTC**) so LLM
analysis uses the cheaper off-peak rate.

| Time (UTC / MSK) | What happens | Why you care |
|---|---|---|
| 14:00 / 17:00 | 227 articles from BBC, Guardian, Reuters, TechCrunch, Verge, Ars (RSS) | The mainstream narrative — what the masses hear tomorrow |
| 14:10 / 17:10 | 197 stories from Hacker News (Algolia API, last 7 days) | What developers are building and arguing about |
| 14:20 / 17:20 | 183 articles from NYT, WaPo, FT, Wired, Medium + 7 more (Ladder paywall proxy) | The *real* analysis behind the paywall |
| 14:30 / 17:30 | 30 products from ProductHunt (GraphQL) | What's launching right now |
| independent | Collector finalizes raw facts in `compass.db` | Collection status does not depend on LLM |
| manual/shadow | Versioned Engine runs facets → stories → trends | Repeat analysis without re-collecting |

> Reddit (737 posts, 18 subreddits) is collected manually from a residential IP
> (Reddit blocks datacenter IPs) and synced to the VPS.

You wake up to a report that says:

> **Top themes today:**
> 1. **AI-агенты выходят из-под контроля: побеги из песочниц и взломы компаний**
>    Продвинутые модели самостоятельно находят уязвимости и выходят за пределы
>    изолированных сред. Критично для тома «Общество»...
> 2. **Физическое сопротивление строительству ИИ-дата-центров**
>    Активисты мобилизуют общественность против экологического ущерба...
>
> **Column ideas:** «ИИ-колониализм» и бунт на местах · Конец эпохи AI-washing ·
> Агент вышел из-под контроля: кто несёт ответственность?
> **Pain points:** AI safety failures, sandbox escape, AI feature bloat...

### When you ask it

```bash
reddit-compass collect --profile broad --sources reddit,hn,rss,ladder,ph
reddit-compass engine release create --run RUN_ID
reddit-compass engine facets --release RELEASE_ID --profile broad
reddit-compass engine stories propose --facet-release FACET_ID --limit 50
reddit-compass engine trends propose --story-release STORY_ID --window 30d
reddit-compass fetch --stealth     # Reddit: 40 subreddits, stealth mode
reddit-compass hn                  # Hacker News: AI stories
reddit-compass rss                 # RSS: 6 free sources
reddit-compass ladder              # Paywall: 12 sources via Ladder
reddit-compass ph                  # ProductHunt: top products
reddit-compass signals             # LLM analysis (Qwen API, all sources)
reddit-compass serve               # REST API + UI on :8900
reddit-compass db rebuild          # Только legacy recovery, не Engine workflow
reddit-compass db stats            # SQLite history
reddit-compass fetch --dry-run     # Preview without network
```

### What it will NOT do — by design

Written into [`AGENTS.md`](AGENTS.md). The service refuses, by architecture:

- **Post, vote, or comment** on Reddit. (Read-only. Always.)
- **Bypass account bans** or impersonate users. (Proxy is for rate limits only.)
- **Train ML models** on collected content. (Explicitly forbidden.)
- **Expose secrets** in git, logs, or API responses. (detect-secrets pre-commit gate.)
- **Touch other services** on the VPS. (Isolated network + volume.)

> **Trust is built in what a system *won't* do.** A scraper that posts is a liability. A scraper that *can't* post is a tool you can leave running at 3 AM.

---

## Architecture

```
    🌐 Reddit packs       💬 Hacker News      📰 RSS sections    🪜 Ladder optional  🚀 ProductHunt
    Playwright + JSON     Algolia front/search aiohttp + XML     fallback            GraphQL API
         │                      │                   │                   │                   │
         ▼                      ▼                   ▼                   ▼                   ▼
    ┌─────────────────────────────────────────────────────────────────────────────────────────────┐
    │                    two independent runtimes in one repository                                │
    │                                                                                             │
    │    Collector → compass.db ──read-only snapshot──► Trend Engine → publication → Radar/Today │
    └─────────────────────────────────────────────────────────────────────────────────────────────┘
         │                      │                        │                      │
         ▼                      ▼                        ▼                      ▼
    JSONL snapshots        compass.db              trend_engine.db        REST API :8900
    (exchange format)      (raw facts)             (immutable versions)   (FastAPI + OAuth2)
```

**Sources → 5 source clusters → 12 broad domains → stories/trends → Today + Radar**

Collection and analysis are separate runtimes. `collect` writes raw facts to `compass.db`;
the versioned Engine freezes them into `trend_engine.db`. Radar reads only a manually published
Story/Trend combination, so a collection failure or experimental attempt cannot erase the last
verified dashboard.

Full architecture with deployment diagrams: [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## Sources and domains

Default collection profile is [`config/profiles/broad.json`](config/profiles/broad.json):
AI/technology, labor/career, business/markets, society/politics, world/geopolitics,
culture/media, sports, science/health/education, finance/consumer,
climate/energy/infrastructure, security/privacy, and `other`.

| Cluster | Sources | Access |
|---|---|---|
| **Mainstream** | BBC, Guardian, NYT/WaPo/USA Today via RSS/Google News; optional Ladder | RSS + Ladder fallback |
| **Business** | Reuters, FT, Fox Business, American Banker | RSS + Ladder fallback |
| **Tech/Culture** | TechCrunch, Verge, Ars Technica, Wired, New Yorker, Vanity Fair | RSS + Ladder fallback |
| **Voices** | Reddit broad packs, Medium | Public JSON/RSS + Ladder fallback |
| **Developers / Pulse** | Hacker News, ProductHunt | Algolia + GraphQL/feed |

Implementation note: [`docs/RADAR_TRENDWATCHING_IMPLEMENTATION.md`](docs/RADAR_TRENDWATCHING_IMPLEMENTATION.md).
Prompt contracts: [`docs/RADAR_PROMPTS.md`](docs/RADAR_PROMPTS.md).
Full source map: [`docs/MULTI_SOURCE_PLAN.md`](docs/MULTI_SOURCE_PLAN.md)

---

## Quick Start

### Prerequisites

- Python 3.12+, [`uv`](https://docs.astral.sh/uv/)
- Playwright: `uv run playwright install chromium`
- (Optional) Docker for VPS deployment

### Install

```bash
git clone https://github.com/eiler2005/reddit-compass.git
cd reddit-compass
uv sync
```

### First run

```bash
# Preview what will be collected (no network):
uv run reddit-compass fetch --dry-run

# Collect Reddit posts:
uv run reddit-compass fetch

# Collect everything (Reddit + HN + RSS):
uv run reddit-compass all

# Start the API:
uv run reddit-compass serve
# → http://localhost:8900/docs (Swagger UI)
```

### Optional residential proxy for Reddit

Set `REDDIT_COMPASS_PROXIES` in `.env.secrets` to the provider-issued HTTP proxy
URL (see `.env.example` for the placeholder format). The Reddit bootstrap request
and public `.json` listing/comment requests then use that proxy; if the HTTP client
receives HTML or HTTP 403, the Playwright fallback launches Chromium through the
same configured proxy. Request pacing remains 4 seconds, with at most two 429
retries separated by 10 seconds. Proxies are only for rate-limit mitigation —
never for account bans, logins, posting, voting, or commenting.

For rotating residential pools (e.g. IPRoyal) set `REDDIT_COMPASS_ENGINE=playwright`:
Reddit serves `.json` to full browser traffic but frequently rejects bare HTTP
from pool IPs with 403 (verified 2026-07-27). If the provider offers a
sticky-session endpoint (same exit IP for ~20 minutes), prefer it — per-connection
IP rotation causes transient `Failed to fetch` errors (the browser engine retries
them automatically).

Keep credentials only in `.env.secrets` (gitignored); do not commit or log them.
An optional local proxy for other approved public read-only sources is documented
only in gitignored operations notes and secret files; never move it into tracked
configuration or `REDDIT_COMPASS_PROXIES`.

### Nightly automation

**VPS** (RSS, HN, Ladder, PH, LLM, radar) — host-cron in the Qwen discount window
(14:00–15:30 UTC = 17:00–18:30 MSK). See [`deploy/hostkey/README.md`](deploy/hostkey/README.md).

**Reddit** (residential IP) — collected from Mac (Reddit blocks datacenter IPs),
then synced to the VPS. Nightly `scripts/fetch-and-sync.sh` (launchd, 03:17) alternates
the route to avoid burning the home IP: even days — direct home IP, odd days — IPRoyal
residential proxy with `REDDIT_COMPASS_ENGINE=playwright` (proxy from the gitignored
`deploy/hostkey/.env.secrets`). Force a route with `RC_PROXY_MODE=on|off`:

```bash
./scripts/fetch-and-sync.sh               # fetch + sync (route chosen automatically)
RC_PROXY_MODE=on ./scripts/fetch-and-sync.sh --fetch   # force proxy route
# manual alternative:
uv run reddit-compass fetch --stealth     # collect on Mac (~11 min)
scp data/snapshots/$(date +%F)/posts.jsonl deploy@VPS:/tmp/
ssh deploy@VPS "docker cp /tmp/posts.jsonl rc-api:/data/snapshots/$(date +%F)/"
```

### VPS deployment

```bash
./deploy/hostkey/deploy.sh
# Deploys: API (FastAPI) + Caddy (reverse proxy) + batch collector
# → http://VPS_IP:8900/health
```

---

## API

OAuth2 client credentials → JWT. CORS-configured for external consumers.

```bash
# Get token:
curl -X POST http://localhost:8900/oauth/token \
  -H "Content-Type: application/json" \
  -d '{"client_id":"practicum","client_secret":"<secret>"}'

# Query posts:
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8900/api/v1/posts?date=2026-07-22&subreddit=artificial&limit=10"
```

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health` | — | Liveness probe |
| `GET /dashboard` | — | HTML dashboard (editorial style) |
| `GET /docs` | — | Swagger UI |
| `POST /oauth/token` | — | Client credentials → JWT |
| `GET /api/v1/snapshots` | Bearer | List snapshot dates |
| `GET /api/v1/posts` | Bearer | Posts with filters + pagination |
| `GET /api/v1/signals` | Bearer | LLM-extracted signals |
| `GET /api/v1/stats` | Bearer | Aggregated statistics |

---

## LLM Analysis (Qwen API)

Not just collection — **intelligence**. Analyzes **all available sources**
(Reddit, HN, RSS, Ladder, ProductHunt) — works even without Reddit data.

```bash
export QWEN_TOKEN_PLAN_KEY=sk-...  # QwenCloud: https://home.qwencloud.com/api-keys
uv run reddit-compass signals
```

For each post, the LLM extracts:
- **Pain points** — what problems people describe
- **Buying intent** — is someone looking to buy an AI product?
- **Business relevance** (1–10) — how relevant for enterprise AI
- **Book relevance** (1–10) — how relevant for the narrative
- **Themes** — key topics (1–3 per post)

Then synthesizes: **top 5 deep themes** (with explanations), **3 column ideas**,
**narrative shifts**, **top-10 by book relevance**, **all pain points**.

### Model pyramid (price / quality)

| Task | Model | Why |
|---|---|---|
| **Synthesis** (themes, column ideas, narrative shifts) | `qwen3.8-max-preview` | Complex, few calls, off-peak discount 17:00–03:00 MSK |
| **Classification** (per-post pain points, relevance) | `qwen3.7-plus` | High volume, balanced price/quality |
| **Simple tasks** (filtering, summarization) | `qwen3.6-flash` | Cheapest |

---

## Dashboard & Trend Radar

Три режима, три сценария:

| View | URL | Purpose |
|---|---|---|
| **Today** | `/today` | Утренний бриф: 3–5 изменений, что прочитать, что в работе |
| **News** | `/news` | Сырой inbox опубликованного Data Release: материалы, источники, sections, связанный story |
| **Stories** | `/stories` | Конкретные события с evidence items; не raw news и не тренды |
| **Trends** | `/trends` | Повторяющиеся паттерны поверх нескольких stories |
| **Radar** | `/radar` → `/runs/{date}/radar` | Полный аналитический workspace: landscape, shelves, coverage, project panels |
| **Project Lens** | `/projects/rbc`, `/projects/book` | Книга/РБК/business поверх опубликованных stories/trends |
| **Explore** | `/explore` | Legacy/compat search по старой projection |
| **Story detail** | `/stories/{id}` | Published Engine story evidence; falls back to legacy detail if needed |
| **Trend detail** | `/trends/{id}` | Published trend pattern, member stories and evidence |
| **Runs** | `/runs` | История запусков с реальными counts |

Legacy (один переходный релиз): `/legacy/dashboard`, `/legacy/runs/{date}/radar`

### Versioned Story/Trend Engine

Для итераций без full rebuild используется `trend_engine.db`: полные frozen Data Releases,
независимые Facet/Story/Trend attempts, Golden Set, Qwen-review только серой зоны и атомарные
publication pointers.

Published analysis is explicitly split into `News → Stories → Trends → Project Lens`.
See [`docs/NEWS_STORIES_TRENDS.md`](docs/NEWS_STORIES_TRENDS.md).
Published Radar includes a cockpit section that links these layers.

```bash
reddit-compass engine release create --run 2026-07-29:broad
reddit-compass engine facets --release RELEASE_ID --profile broad
reddit-compass engine stories propose --facet-release FACET_ID --limit 50
reddit-compass engine stories inspect --story-release STORY_ID
reddit-compass engine trends propose --story-release STORY_ID
reddit-compass engine publish --story-release STORY_ID --trend-release TREND_ID --channel shadow
```

`compass.db` открывается read-only; Radar читает только опубликованную версию. Старый `lab`
остаётся compatibility alias на один релиз. Полный контракт:
[`docs/TREND_ENGINE.md`](docs/TREND_ENGINE.md).

### Дизайн

Kinetic motion-first, dark tech aesthetic (по описанию Awwwards: icreon-digital-velocity).
Обе темы: dark (default) + light toggle. Типографика: Space Grotesk / Inter / JetBrains Mono.
Scroll-reveal, hover lift + glow, count-up KPI, ambient background.

### Карточки сюжетов

Title → прямой переход на самый значимый источник (primary evidence).
Ранжирование: content_scope (full > excerpt > abstract > headline) × cluster weight.
📋 рядом — полный сюжет с timeline и evidence matrix.
Кластеры: 🗣 Голоса, 💻 Разработчики, 📰 Мейнстрим, 💰 Бизнес,  Tech/Культура, 🚀 Продукты.

```
http://127.0.0.1:8900/today              # утренний бриф
http://127.0.0.1:8900/radar              # полный Radar
http://127.0.0.1:8900/explore            # поиск + фильтры
http://127.0.0.1:8900/explore?date=2026-07-28&profile=broad&pain=security+breach
http://127.0.0.1:8900/legacy/dashboard   # legacy
```

Auth: Basic Auth (credentials in `.env.secrets`), Let's Encrypt TLS.

---

## Security Model

| Layer | Mechanism |
|---|---|
| **Secrets** | `.env.secrets` (gitignored) + `detect-secrets` pre-commit |
| **API auth** | OAuth2 client credentials → JWT (1h expiry) |
| **Network** | Loopback only, Caddy reverse proxy |
| **Containers** | `read_only`, `no-new-privileges`, `cap_drop: ALL` |
| **Reddit** | Read-only, rate-limited, public data only |
| **Git** | Secret scan on every commit, no `--no-verify` |

Full rules: [`AGENTS.md`](AGENTS.md)

---

## Documentation

| Document | Topic |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Full system architecture with diagrams |
| [`ROADMAP.md`](ROADMAP.md) | Phases 2–6, status |
| [`docs/MULTI_SOURCE_PLAN.md`](docs/MULTI_SOURCE_PLAN.md) | 21 sources, 5 clusters |
| [`docs/TREND_ENGINE.md`](docs/TREND_ENGINE.md) | Canonical immutable Engine workflow, gates and rollback |
| [`docs/CLUSTER_LAB.md`](docs/CLUSTER_LAB.md) | Deprecated Cluster Lab compatibility guide |
| [`docs/STORY_TREND_CLUSTERING_RESEARCH.md`](docs/STORY_TREND_CLUSTERING_RESEARCH.md) | Research-backed story/trend clustering roadmap |
| [`docs/COMPETITIVE_ANALYSIS.md`](docs/COMPETITIVE_ANALYSIS.md) | GitHub landscape, Ladder |
| [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) | Ranked improvement plan |
| [`CHANGELOG.md`](CHANGELOG.md) | Keep a Changelog |
| [`AGENTS.md`](AGENTS.md) | LLM agent contract |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12, strict mypy |
| Collection | Playwright, aiohttp, Ladder proxy |
| Storage | JSONL + SQLite |
| LLM | Qwen API — pyramid: qwen3.8-max-preview / qwen3.7-plus / qwen3.6-flash |
| API | FastAPI + uvicorn + JWT |
| Deploy | Docker + Caddy + host-cron |
| Quality | ruff, mypy strict, pytest (84%), detect-secrets |
| CI/CD | GitHub Actions → GHCR |

---

## License

[MIT](LICENSE) © 2026 Denis Ermilov
