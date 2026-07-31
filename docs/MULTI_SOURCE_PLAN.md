# reddit-compass → trend-radar: Мульти-источники

> **Статус:** source capability registry; 21 configured source, 6 source clusters and 5 adapters.
> Деплой: VPS target из gitignored `deploy/hostkey/.env.secrets`, каталог `/opt/reddit-compass/`.

---

## 1. Кластеры источников

### 📰 Кластер 1: «Мейнстрим-нарратив» (что слышат массы)

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 1 | **nytimes.com** | RSS/Atom; official API when configured; optional listing | 🇺🇸 | Главный нарратив США |
| 2 | **washingtonpost.com** | RSS/Atom; optional listing | 🇺🇸 | Политика + tech, regulation |
| 3 | **time.com** | optional listing | 🇺🇸 | Массовый фрейминг |
| 4 | **usatoday.com** | RSS/Atom | 🇺🇸 | Пульс «средней Америки» |
| 5 | **bbc.com** | RSS/Atom | 🇬🇧 | Глобальный не-US взгляд |
| 6 | **theguardian.com** | RSS/Atom | 🇬🇧 | UK, расследования |
| 7 | **foxnews.com** | optional Ladder listing | 🇺🇸 | Контрастный массовый framing |

### 💰 Кластер 2: «Бизнес и финансы» (куда идут деньги)

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 8 | **ft.com** | optional Ladder listing / RSS | 🇬🇧 | Главный финансовый нарратив |
| 9 | **americanbanker.com** | optional Ladder listing | 🇺🇸 | Банки + финтех + AI |
| 10 | **foxbusiness.com** | RSS/optional listing | 🇺🇸 | Бизнес-консервативный взгляд |
| 11 | **reuters.com** | RSS/Atom | 🌐 | Мировые новости, «первый сигнал» |

### 🔬 Кластер 3: «Технологии и культура»

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 12 | **wired.com** | optional Ladder listing | 🇺🇸 | Tech + общество, AI-тренды |
| 13 | **newyorker.com** | optional Ladder listing | 🇺🇸 | Лонгриды, AI-этика |
| 14 | **vanityfair.com** | optional Ladder listing | 🇺🇸 | Big Tech + власть + культура |
| 15 | **techcrunch.com** | RSS/Atom | 🇺🇸 | Стартапы, funding rounds |
| 16 | **theverge.com** | RSS/Atom | 🇺🇸 | Consumer tech, Big Tech |
| 17 | **arstechnica.com** | RSS/Atom | 🇺🇸 | Глубокая tech-аналитика |

### 🗣 Кластер 4: «Голоса» (что говорят люди)

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 18 | **reddit.com** (profile packs) | Public read-only JSON/RSS fallback | 🌐 | Живые реакции, боли, кейсы |
| 19 | **medium.com** | RSS/optional listing | 🌐 | Кейсы практиков, лонгриды |

### 💻 Кластер 5: «Разработчики»

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 20 | **news.ycombinator.com** | Algolia/search API | 🌐 | Что разработчики строят и обсуждают |

### 🚀 Кластер 6: «Продуктовый пульс»

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 21 | **producthunt.com** | API/feed | 🌐 | Что запускают прямо сейчас |

### Выключенные до credentials / отдельного решения

| # | Источник | Статус | Причина |
|---|---|---|---|
| 21 | **NYT API** | `not_configured` by default | Нужен `NYT_API_KEY`; это official-first адаптер, не подмена RSS. |
| 22 | **WSJ / Dow Jones** | `not_configured` | Нужны лицензированные credentials; источник не должен отображаться как собранный. |

---

## 2. Адаптеры (реализованы)

| Адаптер | Файл | Источников | CLI |
|---|---|---|---|
| **RSS/Atom** | `sources/rss.py` | 12 configured providers/sections; direct feeds plus Google News RSS where needed | `reddit-compass rss` |
| **Ladder** | `sources/ladder.py` | 9 configured publisher listing sources | `reddit-compass ladder` |
| **Hacker News** | `sources/hackernews.py` | front/search snapshots through Algolia | `reddit-compass hn` |
| **ProductHunt** | `sources/producthunt.py` | product pulse | `reddit-compass ph` |
| **Reddit** | `client.py` + `fetch_subreddits.py` | profile-defined broad packs | `reddit-compass fetch` |

The capability registry in `sources/registry.py` is the source of truth for provider, source
cluster, scope, expected freshness and credentials. A configured source is not automatically a
collected source: the factual status and count for a day are recorded as `source_health` in the
raw run and exposed in `/runs`.

---

## 3. Выходные данные

```
data/snapshots/YYYY-MM-DD/
├── posts.jsonl          ← Reddit
├── hackernews.jsonl     ← Hacker News
├── rss.jsonl            ← RSS/Atom provider sections
├── ladder.jsonl         ← optional listing fallback, only when it actually ran
└── producthunt.jsonl    ← Product pulse
```

The Collector normalizes these artifacts into `ContentItem`/`Observation` rows in `compass.db`.
Legacy `PostCard` JSONL remains compatible during the transition, but the versioned Engine consumes
only an immutable copied `DataRelease` in `trend_engine.db`. Details and diagrams:
[`COLLECTOR_TO_TRENDS_FLOW.md`](COLLECTOR_TO_TRENDS_FLOW.md).

---

## 4. Инфраструктура на VPS

```
/opt/reddit-compass/
├── docker-compose.yml     ← 3 сервиса: batch + api + caddy
├── Dockerfile             ← Playwright (batch-коллектор)
├── Dockerfile.api         ← Slim (API, без Chromium)
├── Caddyfile              ← Reverse proxy :8900
├── .env                   ← Секреты (gitignored)
├── src/                   ← Исходники
└── config/                ← Профили

Host-cron uses separate Collector and Engine stages. The exact version-controlled schedule is
documented in `deploy/hostkey/reddit-compass.cron`; completion, shadow publication and rollback
are defined in [`COLLECTION_LIFECYCLE.md`](COLLECTION_LIFECYCLE.md). This document intentionally
does not duplicate a volatile timetable.
```

---

## 5. Ladder (optional publisher listing fallback)

**Ladder** is an optional adapter for configured publisher listing pages and personal research.
It is not a claim that every publisher is available, and it is not a source of truth for a full
paywalled article. The adapter stores title, canonical link and permitted excerpt/scope only; a
failed or unavailable source is recorded as `error`/`not_configured`, never as collected.

```bash
docker run -p 127.0.0.1:8080:8080 -d \
  --env RULESET=https://raw.githubusercontent.com/everywall/ladder-rules/main/ruleset.yaml \
  --name ladder ghcr.io/everywall/ladder:latest
```

Use it only where terms, access and content scope permit. WSJ/Dow Jones remains `not_configured`
without licensed credentials. The source definition and actual daily health are more authoritative
than a static ruleset count.

---

## 6. Ограничения и риски

| Риск | Митигация |
|---|---|
| Provider changes feed/listing | `source_health` exposes the failed provider/section; the run becomes partial when an expected input is absent |
| WSJ/Dow Jones unavailable | Mark `not_configured`; use licensed API only after credentials and legal approval |
| RSS/API rate limits | Adapter-specific pacing and error state; no green completion from a missing section |
| Ladder access/content scope | Optional fallback only; retain links and permitted excerpts, not paid full text |
| Reddit route unavailable | Use only an approved read-only route; JSONL handoff does not overwrite VPS corpus |

---

## 7. Метрики успеха

- [ ] `/runs` shows expected source/section health, count, freshness and error state for the raw run.
- [ ] A `complete` raw run has all requested artifacts; a missing provider is explicit `partial`.
- [ ] A frozen Data Release preserves the same source coverage after later collection changes.
- [ ] Stories/trends are evaluated on releases and pass quality gates before a manual Broad publish.
- [ ] UI publication has evidence links and can roll back by pointer without rebuilding raw data.
