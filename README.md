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
| 14:00 / 17:00 | RSS sections from mainstream, business and tech/culture sources | The mainstream narrative — what the masses hear tomorrow |
| 14:10 / 17:10 | Hacker News front/search snapshots | What developers are building and arguing about |
| 14:20 / 17:20 | Optional Ladder-supported publisher listings | Extra headlines/abstracts from configured publisher pages |
| 14:30 / 17:30 | ProductHunt pulse | What's launching right now |
| 14:45 / 17:45 | Snapshot finalizer creates one raw run in `compass.db` | Completion is factual and does not depend on LLM |
| 16:00 / 19:00 | Versioned Engine runs facets → stories → bounded Qwen → trends → quality → shadow | Repeat analysis without re-collecting |
| manual | Operator inspects and publishes a complete gated version to `broad` | Radar never silently replaces a good version with a partial one |

> Reddit is collected read-only from configured broad packs. If the server route is unavailable,
> a local residential route can produce a JSONL artifact that is safely merged on the VPS.

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

### Current supported path

```bash
# Network-free preview of the configured collection surface:
reddit-compass collect --profile broad --sources reddit,hn,rss,ladder,ph --dry-run

# Collector: source adapters → JSONL snapshots → one raw run in compass.db.
reddit-compass collect --profile broad --sources reddit,hn,rss,ladder,ph

# Finalize a run from artifacts that were already collected (no network, no LLM):
reddit-compass collect --from-snapshots --profile broad --sources reddit,hn,rss,ladder,ph --date YYYY-MM-DD

# Engine: repeatable analysis over an immutable copy of that raw run.
reddit-compass engine release create --run RUN_ID
reddit-compass engine facets --release RELEASE_ID --profile broad
reddit-compass engine stories propose --facet-release FACET_ID --limit 50
reddit-compass engine trends propose --story-release STORY_ID --window 30d
reddit-compass engine cycle --profile broad --window 7 --publish-channel shadow

# Read the published UI.
reddit-compass serve               # REST API + UI on :8900
```

`fetch`, `hn`, `rss`, `ladder`, `ph`, `all` and `signals` remain compatibility commands for a
single adapter or legacy artifacts. They are not the production publication path: current Radar
is produced by `collect` and the versioned Engine. `db rebuild` is legacy recovery only, never a
normal way to iterate on stories or trends.

### What it will NOT do — by design

Written into [`AGENTS.md`](AGENTS.md). The service refuses, by architecture:

- **Post, vote, or comment** on Reddit. (Read-only. Always.)
- **Bypass account bans** or impersonate users. (Proxy is for rate limits only.)
- **Train ML models** on collected content. (Explicitly forbidden.)
- **Expose secrets** in git, logs, or API responses. (`scripts/secret-scan` + detect-secrets gate.)
- **Touch other services** on the VPS. (Isolated network + volume.)

> **Trust is built in what a system *won't* do.** A scraper that posts is a liability. A scraper that *can't* post is a tool you can leave running at 3 AM.

---

## How it works

reddit-compass has two jobs that are intentionally separated.

```text
public sources
  → adapter snapshots (JSONL)
  → Collector / compass.db             raw facts and source health
  → immutable Data Release
  → Trend Engine / trend_engine.db     facets → stories → trends → quality
  → RadarPublication pointer
  → News / Stories / Trends / Radar / Today, each with evidence links
```

1. **Collector builds the corpus.** Source adapters fetch public/read-only data from Reddit,
   Hacker News, RSS/Atom feeds, optional Ladder-supported publisher pages and ProductHunt. Raw
   materials, observations and source health go to `compass.db`; JSONL snapshots remain an
   exchange/debug format.
2. **Engine freezes a Data Release.** A finalized collection run is copied into
   `trend_engine.db` as an immutable release. Later changes in `compass.db` cannot move old
   experiments.
3. **Facets classify items.** The Engine assigns domains, themes, entities, event frames,
   source cluster, content scope and pain points. This is the layer that powers theme clouds and
   domain drill-down.
4. **Stories group concrete events.** URL canonicalization, Reddit target URLs, title/BM25
   similarity, entities, temporal windows, embeddings and bounded Qwen review decide whether
   items describe the same event. Stories keep direct evidence links.
5. **Trends group repeated patterns.** Trend discovery runs over accepted stories, not raw
   posts/articles. A trend needs multiple stories over time; reposts or syndicated copies should
   not inflate it.
6. **Quality gates decide publishability.** Engine releases are evaluated for overmerge risk,
   domain balance, naming quality, evidence coverage and regression against the baseline.
7. **Radar/Today read only publications.** UI does not read arbitrary experiments by default.
   A manual `RadarPublication` pointer selects the Story/Trend release shown in `broad`,
   `ai-native` or `shadow`. Rollback switches the pointer back.

### What “ready” means

| State | What is guaranteed | What UI can safely show |
|---|---|---|
| `collection complete` | Every requested source artifact was finalized into raw facts; source health is recorded. Qwen is not required. | A new immutable input can be created; it is not yet an editorial verdict. |
| `Data Release finalized` | Exact rows, observations and health are copied with checksums. Later collection cannot alter the experiment. | Engine/preview diagnostics. |
| `analysis evaluated` | Facets, stories, trends and the quality report exist for one immutable release. | Shadow/preview only until a human publishes it. |
| `Radar published` | Input is complete, quality gates passed and a manual channel pointer selected the release. | `Today`, Radar and direct News/Story/Trend evidence links. |
| `partial` / `failed` | A missing or failed source is visible; nothing is silently called complete. | Diagnostics or `shadow`; the last good Broad publication remains live. |

### Read the mechanics, not just the summary

| Need | Canonical reference |
|---|---|
| Collection handoff, completion stages, `/runs` journal and publish rules | [`docs/COLLECTION_LIFECYCLE.md`](docs/COLLECTION_LIFECYCLE.md) |
| End-to-end textual diagrams | [`docs/COLLECTOR_TO_TRENDS_FLOW.md`](docs/COLLECTOR_TO_TRENDS_FLOW.md) |
| Both SQLite databases, tables and ownership boundary | [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md) |
| Story/trend algorithms, Golden Set, review, quality and rollback | [`docs/TREND_ENGINE.md`](docs/TREND_ENGINE.md) |
| Quality floors and regression gates | [`docs/QUALITY_GATES.md`](docs/QUALITY_GATES.md) |
| Source capability registry and sections | [`docs/MULTI_SOURCE_PLAN.md`](docs/MULTI_SOURCE_PLAN.md) |

## Architecture at a glance

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

**Sources → 6 source clusters → 12 broad domains → stories/trends → Today + Radar**

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
| **Developers** | Hacker News | Algolia/search snapshots |
| **Product pulse** | ProductHunt | GraphQL/feed |

Implementation note: [`docs/archive/RADAR_TRENDWATCHING_IMPLEMENTATION.md`](docs/archive/RADAR_TRENDWATCHING_IMPLEMENTATION.md).
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
# See the configured broad surface without network access:
uv run reddit-compass collect --profile broad --sources reddit,hn,rss,ladder,ph --dry-run

# Create one raw collection run. This fetches only public/read-only sources.
uv run reddit-compass collect --profile broad --sources reddit,hn,rss,ladder,ph

# Iterate on the same frozen input without collecting again.
uv run reddit-compass engine cycle --profile broad --window 7 --publish-channel shadow

# Start the API:
uv run reddit-compass serve
# → http://localhost:8900/docs (Swagger UI)
```

Publishing to `broad` is deliberately manual after the quality gate. The exact release IDs,
completion rules and rollback command are in [`docs/COLLECTION_LIFECYCLE.md`](docs/COLLECTION_LIFECYCLE.md).

### Optional residential proxy for Reddit

Set `REDDIT_COMPASS_PROXIES` in `.env.secrets` to the provider-issued HTTP proxy
URL (see `.env.example` for the placeholder format). The Reddit bootstrap request
and public `.json` listing/comment requests then use that proxy; if the HTTP client
receives HTML or HTTP 403, the Playwright fallback launches Chromium through the
same configured proxy. Request pacing remains 4 seconds, with at most two 429
retries separated by 10 seconds. Proxies are only for rate-limit mitigation —
never for account bans, logins, posting, voting, or commenting.

For rotating residential pools set `REDDIT_COMPASS_ENGINE=playwright`:
Reddit serves `.json` to full browser traffic but frequently rejects bare HTTP
from pool IPs with 403 (verified 2026-07-27). If the provider offers a
sticky-session endpoint (same exit IP for ~20 minutes), prefer it — per-connection
IP rotation causes transient `Failed to fetch` errors (the browser engine retries
them automatically).

Keep credentials only in `.env.secrets` (gitignored); do not commit or log them.
An optional local proxy for other approved public read-only sources is documented
only in gitignored operations notes and secret files; never move it into tracked
configuration or `REDDIT_COMPASS_PROXIES`.

### Automation (every 2 nights)

**VPS** collects RSS, HN, Ladder and ProductHunt, then finalizes one raw snapshot run at 14:45 UTC
and runs the Engine in shadow at 16:00 UTC. Pipeline runs every 2 nights (odd days, `*/2` in cron) —
Engine cycle on 1-CPU VPS takes 30-60 min, daily runs are redundant with a 7-day window.
The exact version-controlled schedule is in
[`deploy/hostkey/reddit-compass.cron`](deploy/hostkey/reddit-compass.cron); the completion and
publication rules are in [`docs/COLLECTION_LIFECYCLE.md`](docs/COLLECTION_LIFECYCLE.md).
Full stage-by-stage timing: [`docs/COLLECTION_LIFECYCLE.md` §5.1](docs/COLLECTION_LIFECYCLE.md).

**Reddit** can be collected separately from a local approved route and synced to the VPS as a
JSONL artifact. Nightly `scripts/fetch-and-sync.sh` (launchd, 03:17) chooses the route from
gitignored config and supports an explicit `RC_PROXY_MODE=on|off` override:

```bash
./scripts/fetch-and-sync.sh               # fetch + sync (route chosen automatically)
RC_PROXY_MODE=on ./scripts/fetch-and-sync.sh --fetch   # force proxy route
# manual alternative:
uv run reddit-compass fetch --stealth     # collect locally from approved route
scp data/snapshots/$(date +%F)/posts.jsonl "${RC_DEPLOY_USER:-deploy}@${RC_DEPLOY_HOST:-reddit-compass-vps}:/tmp/"
ssh "${RC_DEPLOY_USER:-deploy}@${RC_DEPLOY_HOST:-reddit-compass-vps}" \
  "docker cp /tmp/posts.jsonl rc-api:/data/snapshots/$(date +%F)/"
```

### VPS deployment

```bash
./deploy/hostkey/deploy.sh
# Deploys: API (FastAPI) + Caddy (reverse proxy) + batch collector
# Target host and public URL come from deploy/hostkey/.env.secrets
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

## LLM-assisted analysis

The Engine is deterministic first and LLM-assisted second. URL normalization, title/entity
matching, temporal windows, embeddings and quality gates do the heavy lifting. Qwen is used for
bounded grey-zone review, Russian naming/interpretation and project lenses; it does not cluster
the whole corpus directly.

```bash
export DASHSCOPE_API_KEY=<qwen-api-key>
uv run reddit-compass engine cycle --profile broad --window 7 --publish-channel shadow
```

Engine facets and Qwen-reviewed outputs include:

- **Domains/themes** — stable broad taxonomy plus narrower topic tags.
- **Pain points** — problems people or institutions describe.
- **Evidence refs** — every accepted story/trend points back to source items.
- **Project scores** — book/RBC/business relevance can diverge by lens.
- **Trend interpretation** — why a pattern matters after the candidate passed clustering gates.

### Model pyramid (price / quality)

| Task | Model | Why |
|---|---|---|
| **Synthesis** (themes, column ideas, narrative shifts) | `qwen3.8-max-preview` | Complex, few calls, off-peak discount 17:00–03:00 MSK |
| **Classification / pair review** | `qwen3.6-flash` | High-volume bounded review and extraction |
| **Simple tasks** (filtering, summarization) | `qwen3.6-flash` | Cheapest |

---

## Dashboard & Trend Radar

Published UI workspaces:

| View | URL | Purpose |
|---|---|---|
| **Today** | `/today` | Утренний бриф: 3–5 изменений, персональная лента до 20 свежих материалов с прямыми ссылками и drill-down по рубрикам |
| **News** | `/news` | Сырой inbox опубликованного Data Release: материалы, источники, sections, связанный story |
| **Stories** | `/stories` | Конкретные события с evidence items; не raw news и не тренды |
| **Trends** | `/trends` | Повторяющиеся паттерны поверх нескольких stories |
| **Pulse** | `/pulse` | Reddit-native community signals: percentile внутри саба, velocity, discussion depth, gaps |
| **Radar** | `/radar` → `/runs/{date}/radar` | Полный аналитический workspace: landscape, shelves, coverage, project panels |
| **Project Lens** | `/projects/rbc`, `/projects/book` | Книга/РБК/business поверх опубликованных stories/trends |
| **Story detail** | `/stories/{id}` | Evidence опубликованного Engine story |
| **Trend detail** | `/trends/{id}` | Published trend pattern, member stories and evidence |
| **Runs** | `/runs` | История запусков с реальными counts |

Навигация — четыре раздела: **Сегодня · Лента · Тренды · Reddit Pulse**. Остальное
достижимо по прямым ссылкам с карточек и из `/runs`.

Все страницы читают **только опубликованный релиз**. Если публикации нет, страница честно
говорит об этом и предлагает `engine cycle` — параллельной проекции из сырого `compass.db`
больше не существует. Старые адреса сохранены редиректами: `/explore` → `/news` (с переносом
строки запроса), `/dashboard` → `/today`.

### Versioned Story/Trend Engine

Для итераций без full rebuild используется `trend_engine.db`: полные frozen Data Releases,
независимые Facet/Story/Trend attempts, Golden Set, Qwen-review только серой зоны и атомарные
publication pointers.

Published analysis is explicitly split into `News → Stories → Trends → Project Lens`.
See [`docs/NEWS_STORIES_TRENDS.md`](docs/NEWS_STORIES_TRENDS.md). End-to-end collection and
analysis lineage is documented in
[`docs/COLLECTOR_TO_TRENDS_FLOW.md`](docs/COLLECTOR_TO_TRENDS_FLOW.md).
Published Radar includes a cockpit section that links these layers.

```bash
reddit-compass engine release create --run 2026-07-29:broad
reddit-compass engine facets --release RELEASE_ID --profile broad
reddit-compass engine embeddings --release RELEASE_ID --model intfloat/multilingual-e5-small
reddit-compass engine stories propose \
  --facet-release FACET_ID \
  --limit 50 \
  --embedding-model intfloat/multilingual-e5-small \
  --dense-top-k 24 \
  --dense-threshold 0.55
reddit-compass engine stories inspect --story-release STORY_ID
reddit-compass engine reddit-pulse propose \
  --release RELEASE_ID \
  --date 2026-07-29 \
  --profile broad \
  --story-release STORY_ID
reddit-compass engine experiments compare --facet-release FACET_ID --limit 300
reddit-compass engine trends propose --story-release STORY_ID
reddit-compass engine publish --story-release STORY_ID --trend-release TREND_ID --channel shadow
```

`broad` publication закрыт gate-ами качества; экспериментальные версии публикуются только в
`shadow`. `compass.db` открывается read-only; Radar читает только опубликованную версию. Старый
`lab` остаётся compatibility alias на один релиз. Полный контракт:
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
http://127.0.0.1:8900/news               # лента выпуска + фильтры
http://127.0.0.1:8900/trends             # тренды поверх stories
http://127.0.0.1:8900/pulse              # Reddit-native сигналы
http://127.0.0.1:8900/radar              # полный аналитический Radar
```

Auth: Basic Auth (credentials in `.env.secrets`), Let's Encrypt TLS.

---

## Security Model

| Layer | Mechanism |
|---|---|
| **Secrets** | `.env.secrets` (gitignored) + repo-local `scripts/secret-scan` + `detect-secrets` pre-commit |
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
| [`docs/MULTI_SOURCE_PLAN.md`](docs/MULTI_SOURCE_PLAN.md) | Source capability registry and source-cluster plan |
| [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md) | SQLite schema: `compass.db`, Engine releases and publication pointers |
| [`docs/COLLECTION_LIFECYCLE.md`](docs/COLLECTION_LIFECYCLE.md) | Completion contract, Mac/VPS handoff, run stages, shadow/publish/rollback |
| [`docs/COLLECTOR_TO_TRENDS_FLOW.md`](docs/COLLECTOR_TO_TRENDS_FLOW.md) | Text diagrams from source collection to News, Stories, Trends and Radar |
| [`docs/DATA_FLOW_DIAGRAMS.md`](docs/DATA_FLOW_DIAGRAMS.md) | Mermaid-схемы: Reddit → stories → trends → Reddit Pulse → публикации |
| [`docs/QUALITY_GATES.md`](docs/QUALITY_GATES.md) | Полы качества + регрессионный harness (`engine quality report/check/snapshot`) |
| [`docs/ENGINE_REVIEW_V3.md`](docs/ENGINE_REVIEW_V3.md) | Ревью Engine v3 + план фаз 1–8 со статусом реализации |
| [`docs/TREND_ENGINE.md`](docs/TREND_ENGINE.md) | Canonical immutable Engine workflow, gates and rollback |
| [`docs/archive/CLUSTER_LAB.md`](docs/archive/CLUSTER_LAB.md) | Deprecated Cluster Lab compatibility guide |
| [`docs/STORY_TREND_CLUSTERING_RESEARCH.md`](docs/STORY_TREND_CLUSTERING_RESEARCH.md) | Research-backed story/trend clustering roadmap |
| [`docs/COMPETITIVE_ANALYSIS.md`](docs/COMPETITIVE_ANALYSIS.md) | GitHub landscape, Ladder |
| [`docs/archive/IMPROVEMENTS.md`](docs/archive/IMPROVEMENTS.md) | Ranked improvement plan |
| [`CHANGELOG.md`](CHANGELOG.md) | Keep a Changelog |
| [`SECURITY.md`](SECURITY.md) | Secret handling, scan workflow and incident response |
| [`docs/SECRET_SCANNING.md`](docs/SECRET_SCANNING.md) | Repo-local scanner rules and placeholders |
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
| Quality | ruff, mypy strict, pytest coverage gate, repo-local secret scan + detect-secrets |
| CI/CD | GitHub Actions → GHCR |

---

## License

[MIT](LICENSE) © 2026 Denis Ermilov
