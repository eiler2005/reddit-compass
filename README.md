# 🧭 reddit-compass

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-green.svg)](https://www.python.org/)
[![CI](https://github.com/eiler2005/reddit-compass/actions/workflows/ci.yml/badge.svg)](https://github.com/eiler2005/reddit-compass/actions)
[![Docker](https://github.com/eiler2005/reddit-compass/actions/workflows/docker.yml/badge.svg)](https://github.com/eiler2005/reddit-compass/actions)

**Your AI trend radar. 21 sources. One compass that shows where to look.**

---

You're writing a book about how AI is changing work. Or running a column. Or building a product and need to know what the market *actually* thinks — not what press releases say.

So you check Reddit. Then Hacker News. Then NYT. Then Wired. Then FT. Then TechCrunch. Then Medium. Then ProductHunt. Every. Single. Morning.

That's 45 minutes of tab-switching before your first coffee. And you *still* miss the thread that went viral at 2 AM — the one where 400 laid-off engineers described exactly the pain your product solves.

**reddit-compass does this for you. Every night. Across 21 sources. With LLM analysis that tells you not just *what* happened, but *why it matters* for your work.**

---

## What reddit-compass does for you

### Every night, on its own

| Cadence | What happens | Why you care |
|---|---|---|
| 03:17 nightly | Collects 400+ posts from 18 Reddit subreddits (Playwright, stealth mode) | The "voice of the street" — raw, unfiltered, real |
| 03:30 nightly | Pulls 50+ stories from Hacker News (Algolia API) | What developers are building and arguing about |
| 03:45 nightly | Fetches 50+ articles from BBC, Guardian, Reuters, TechCrunch, Verge, Ars (RSS) | The mainstream narrative — what the masses hear tomorrow |
| 04:00 nightly | Bypasses paywalls on NYT, WaPo, FT, Wired, Medium + 7 more (Ladder proxy) | The *real* analysis behind the paywall |
| 04:15 nightly | Qwen LLM reads all posts → pain points, business relevance (1–10), themes | Not just data — *intelligence* |
| 04:30 nightly | Cross-source synthesis: "This theme appeared in Reddit + HN + NYT" | **Strong signal** = topic in 3+ sources |
| 04:30 nightly | Syncs to VPS → REST API serves it to your tools | Your Practicum, digest, or notebook gets fresh data |

You wake up to a report that says:

> **Top themes today:**
> 1. "AI replaced my job, then they rehired humans" — viral on r/AskReddit + HN + NYT
> 2. "Vibe coding fixes one thing, breaks ten" — r/vibecoding + Ars Technica
> 3. "One person + AI = $1M company" — Medium + ProductHunt + r/Entrepreneur
>
> **Column ideas:** [3 specific angles with source links]
> **Pain points:** [12 extracted from today's posts]

### When you ask it

```bash
reddit-compass fetch --stealth     # Reddit: 18 subreddits, stealth mode
reddit-compass hn                  # Hacker News: AI stories
reddit-compass rss                 # RSS: 6 free sources
reddit-compass ladder              # Paywall: 12 sources via Ladder
reddit-compass ph                  # ProductHunt: top products
reddit-compass signals             # LLM analysis (Qwen API)
reddit-compass serve               # REST API on :8900
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
    🌐 Reddit (18 sub)     💬 Hacker News      📰 RSS (6)         🪜 Ladder (12)      🚀 ProductHunt
    Playwright + JSON      Algolia API         aiohttp + XML      Ladder proxy        GraphQL API
         │                      │                   │                   │                   │
         ▼                      ▼                   ▼                   ▼                   ▼
    ┌─────────────────────────────────────────────────────────────────────────────────────────────┐
    │                          reddit-compass (unified pipeline)                                   │
    │                                                                                             │
    │    collect ──► store ──► analyze (Qwen LLM) ──► report ──► notify ──► serve (API)           │
    └─────────────────────────────────────────────────────────────────────────────────────────────┘
         │                      │                        │                      │
         ▼                      ▼                        ▼                      ▼
    posts.jsonl            compass.db              signals.jsonl          REST API :8900
    (JSONL exchange)       (SQLite history)        (LLM synthesis)        (FastAPI + OAuth2)
```

**21 sources → 5 clusters → 1 unified schema → LLM intelligence → API**

Full architecture with deployment diagrams: [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## Sources (21, five clusters)

| Cluster | Sources | Access |
|---|---|---|
| 📰 **Mainstream** | NYT, WaPo, Time, USA Today, BBC, Guardian | Ladder + RSS |
| 💰 **Business** | FT, American Banker, Fox Business, Reuters | Ladder + RSS |
| 🔬 **Tech/Culture** | Wired, New Yorker, Vanity Fair, TechCrunch, Verge, Ars Technica | Ladder + RSS |
| 🗣 **Voices** | Reddit (18 subreddits), Hacker News, Medium | Playwright + Algolia + Ladder |
| 📊 **Pulse** | Fox News, ProductHunt | Ladder + GraphQL |

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

Keep credentials only in `.env.secrets` (gitignored); do not commit or log them.
An optional local proxy for other approved public read-only sources is documented
only in gitignored operations notes and secret files; never move it into tracked
configuration or `REDDIT_COMPASS_PROXIES`.

### Nightly automation (macOS)

```bash
cp scripts/com.reddit-compass.nightly.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.reddit-compass.nightly.plist
# Runs at 03:17 daily: fetch + HN + RSS + LLM + sync to VPS
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

Not just collection — **intelligence**.

```bash
export DASHSCOPE_API_KEY=sk-...  # QwenCloud pay-as-you-go: https://home.qwencloud.com/api-keys
uv run reddit-compass signals
```

For each post, the LLM extracts:
- **Pain points** — what problems people describe
- **Buying intent** — is someone looking to buy an AI product?
- **Business relevance** (1–10) — how relevant for enterprise AI
- **Book relevance** (1–10) — how relevant for the narrative
- **Themes** — key topics (1–3 per post)

Then synthesizes: **top 5 themes**, **3 column ideas**, **narrative shifts**.

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
| LLM | Qwen API (qwen-plus + qwen-max) |
| API | FastAPI + uvicorn + JWT |
| Deploy | Docker + Caddy + host-cron |
| Quality | ruff, mypy strict, pytest (84%), detect-secrets |
| CI/CD | GitHub Actions → GHCR |

---

## License

[MIT](LICENSE) © 2026 Denis Ermilov
