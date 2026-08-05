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

Collection и analysis являются независимыми jobs. Host-cron template сначала записывает все
snapshot-артефакты, затем запускает `collect --from-snapshots`, а после этого — Engine в
shadow-канале. Отсутствие LLM не меняет collection status. Сейчас production cron намеренно
поставлен на паузу: владелец проходит этот же контракт вручную по
[`docs/MANUAL_RELEASE_RUNBOOK.md`](docs/MANUAL_RELEASE_RUNBOOK.md). При включении cron он
остаётся version-controlled шаблоном с прежней каденцией раз в две ночи; вручную запускать
второй collector или Engine поверх активного job запрещено.

### Текущий production flow

```text
Mac: Reddit posts.jsonl ──atomic Docker-volume handoff──┐
VPS: rss / hn / ladder / ph snapshots ──────────────────┤
                                                        ▼
14:45 UTC: collect --from-snapshots → one raw run in compass.db
                                                        │ read-only snapshot
16:00 UTC: engine cycle → DataRelease → facets → stories → Qwen → trends → quality → shadow
                                                        │
manual: complete + gated publication → published_channels["broad"] → Today/Radar
```

Полное описание этапов с таймингами: [`docs/COLLECTION_LIFECYCLE.md` §5.1](docs/COLLECTION_LIFECYCLE.md).

`/runs` раскрывает эти стадии для каждого `run_id`. Полный operational contract, статусы и
rollback: [`docs/COLLECTION_LIFECYCLE.md`](docs/COLLECTION_LIFECYCLE.md).

### Legacy compatibility artifact layout (not the scheduler)

```
  03:17 (Mac, launchd)                    04:00 (VPS, host-cron)
  ─────────────────────                    ───────────────────────
  ┌─────────────────────┐                  ┌─────────────────────┐
  │  fetch-and-sync.sh  │                  │  docker compose run  │
  │                     │                  │                     │
  │  1. reddit fetch    │                  │  1. rss/hn/ladder/ph│
  │     (approved route)│                  │  2. finalize run    │
  │  2. posts.jsonl     │                  │  3. engine shadow   │
  │  3. scp artifact    │                  │  4. gated publish   │
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
  │  GET /news                GET /api/v1/stats                      │
  │  GET /trends              GET /api/v2/briefings/{date}           │
  │  GET /pulse               GET /api/v2/stories                    │
  │  GET /stories/{id}        GET /api/v2/runs                       │
  │  GET /trends/{id}         PATCH /api/v2/stories/{id}/research    │
  │  GET /runs/{date}/radar   GET /api/v1/snapshots                  │
  │  GET /runs · /engine      POST /oauth/token                      │
  │  GET /docs (Swagger)                                             │
  │                                                                  │
  │  UI-фрагменты для догрузки (HTML, не JSON):                      │
  │  GET /ui/today-changes    GET /ui/today-reading                  │
  │                                                                  │
  │  Редиректы: /explore → /news · /dashboard → /today               │
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

- Navigation is four sections: **Сегодня · Лента · Тренды · Reddit Pulse**. Everything else is
  reachable through card links and `/runs`.
- Radar chips are navigational, not decorative: stable themes, emerging themes and pain points
  link into `/news` with the filter applied.
- Drill-down links preserve run context: `date` + `profile`.
- Theme filters use `theme=<theme_id>`; candidate theme filters use `candidate_theme=<candidate label>`; pain filters use `pain=<normalized pain label>`.
- `/news` and `/api/v2/stories` resolve `theme`, `candidate_theme` and `pain` through
  `story_items → item_signals`, so the UI returns deduplicated stories, not raw item duplicates.
- **Every page reads only a published release.** With no publication a page says so and points at
  `engine cycle`; there is no second projection built straight from `compass.db`. The legacy
  dashboard, its routes and its templates were removed — they showed a picture that never passed
  the quality gates.
- Card markup lives in Jinja only. `/ui/today-changes` and `/ui/today-reading` return HTML
  fragments rendered from the same partials as the first page, so the browser appends markup
  instead of rebuilding it in JavaScript.
- **Reddit surfaces trade cards for links where the post itself is the destination.** Pulse topic
  clouds (`/pulse`) carry live example headlines — a bare type name like «Прочее» explains nothing —
  and clicking one opens `?view=links`: the top twenty as direct Reddit URLs, ordered by the active
  sort. Today's «Новое на Reddit» block uses the same list, restricted to posts absent from the
  reading queue above it.
- Signal topics come from `signal_type`, not `domain_ids`: facets are computed from item text, and
  a Reddit post usually has none, so `domain_ids_json` is almost always `other`. Diversity is held
  by quotas — per topic, per subreddit, and a tighter cap on `policy_politics`, which carries the
  highest average pulse and otherwise crowds the block out.
- **Порядок читательских поверхностей стабилен и объясним.** News сортируется по силе
  доказательств/engagement, Stories — по числу независимых источников и items, Trends — по
  confidence и охвату, Pulse — по силе сигнала; при равенстве приоритет получает последняя
  дата evidence. Карточка показывает applicable `published_at` либо `first_seen → last_seen`,
  поэтому свежесть проверяется глазами, а не выводится из позиции в списке.

### Story/Trend Engine contract

- `trend_engine.db` содержит frozen Data Releases и отдельные Facet/Story/Trend attempts.
- Engine открывает `compass.db` только `mode=ro`; finalized release защищён SQLite triggers.
- Повторный запуск создаёт новую версию и не удаляет предыдущие результаты.
- Story и Trend можно пересчитывать независимо; full rebuild не входит в workflow.
- Reddit Pulse хранится как отдельный `SignalRelease`: метод, params hash, metrics и git SHA
  фиксируются в `signal_releases`; пересчёт создаёт новую попытку поверх frozen rows, а не запускает
  сетевой сбор и не перезаписывает старые analysis versions.
- Если для Pulse указан `--story-release`, engine связывает Reddit-сигналы с уже построенными
  stories и считает mainstream coverage из `release_items`; если истории нет, novelty становится
  нейтральной, а UI не должен выдавать это за подтверждённую динамику.
- **Пороги плотного сходства — свойство модели эмбеддингов, а не глобальные константы.**
  У E5 медиана косинуса на несвязанных парах ≈ 0.78, у `potion-base-8M` ≈ 0.13: один набор
  чисел на обе модели молча отключает слияние на одной из них. Профили лежат в
  `embeddings.DENSE_THRESHOLD_PROFILES` и подставляются в `params` релиза **до** вычисления
  `params_hash`, поэтому релиз воспроизводим даже после изменения таблицы. Новая модель
  калибруется без разметки: `engine calibrate` переносит не абсолютные значения, а квантили
  распределения на заведомо несвязанных парах.
- **Сборка групп ограничена медоидом** (`medoid_min_score`, дефолт 0.55): каждый член группы
  обязан иметь прямое ребро к медоиду. Порог — параметр релиза; жёстко зашитое 0.72 лежало
  выше всей серой зоны и обесточивало слой ревью целиком.
- **Fingerprint и exact-title — доказательство только между независимыми providers.** Внутри
  одного provider они служат лишь retrieval signal; auto-merge разрешён там только с общим
  event URL или другим независимым доказательством. Это защищает от шаблонных earnings,
  landing pages и повторяющихся Reddit-вопросов.
- **Приоритет источников меток**: `human > claude_review > qwen_review > auto_label`. Авто-метки
  на парах, которые лестница правил уже решила детерминированно, исключаются и из обучения,
  и из оценки: они пересказывают правило, а не судят независимо. Метрики релиза несут
  `label_source` и `labels_are_circular`, а label-гейт требует не-циркулярных меток.
- Дефолтный метод трендов совпадает с прод-путём (`embedding_v2`): расхождение давало результат,
  не проходящий полы качества, которые ночной прогон проходит.
- `broad`/`ai-native` публикуются только после quality gates и ручного решения. Гейт пропускает
  релиз либо по label-гейтам, либо по абсолютным полам качества (`quality.QUALITY_FLOORS`),
  среди которых есть полы **полноты** — иначе система оптимизируется в вырожденное состояние,
  где не сливается ничего.
- Publish/rollback атомарно переключают immutable pointer.
- **Qwen service routing:** Engine использует pay-as-you-go API; `qwen3.7-flash` — для
  извлечения и bounded JSON-review с выключенным thinking, `qwen3.8-max` — только для
  явно согласованного сложного synthesis. Роутер не предполагает бесплатный international
  грант: его размер включается лишь явной конфигурацией после проверки в console.
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
│  Роль: Reddit fetch (local approved route / proxy) + sync на VPS   │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ scp (после каждого прогона)
                              ▼
┌─── VPS HostKey «Hermes» (${RC_DEPLOY_HOST}) ─────────────────────────┐
│                                                                     │
│  /opt/reddit-compass/                                               │
│  ├── docker-compose.yml          ← 4 сервиса                       │
│  │   ├── rc-api                  ← FastAPI :8900 (restart: always)  │
│  │   ├── rc-caddy                ← reverse proxy (loopback)         │
│  │   ├── ladder                  ← paywall-прокси, только в сети    │
│  │   └── rc-collector            ← batch (host-cron, не daemon)     │
│  ├── Dockerfile                  ← Playwright (batch)               │
│  ├── Dockerfile.api              ← Slim (API, без Chromium)         │
│  ├── Caddyfile                   ← :80 → api:8900                   │
│  ├── .env                        ← секреты (gitignored)             │
│  └── data/                       ← snapshots + обе SQLite DB       │
│                                                                     │
│  Security: read_only, no-new-privileges, cap_drop ALL, pids_limit   │
│  Network: loopback only — все ports: с явным 127.0.0.1, иначе       │
│  Docker публикует наружу мимо UFW (см. docs/HOSTING.md)             │
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
До тех пор: Playwright + approved residential route + proxy (для 429).
Создание script app на `reddit.com/prefs/apps` заблокировано для молодого
аккаунта (повторить, когда аккаунт наберёт возраст/карму).

**Статус на 2026-07-27 (проверено live):** Reddit выборочно блокирует
анонимные `.json` — 403 зависит от репутации маршрута. С approved residential route
голый aiohttp работает; с pool-IP ротационных proxy — часто
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
| [`docs/archive/PRODUCT_IMPLEMENTATION_PLAN.md`](docs/archive/PRODUCT_IMPLEMENTATION_PLAN.md) | Продуктовый план: модели, ranking, UI, API |
| [`docs/archive/TRENDWATCHING_DEEP_REVIEW_AND_V2_PLAN.md`](docs/archive/TRENDWATCHING_DEEP_REVIEW_AND_V2_PLAN.md) | Глубокое ревью trendwatching + план V2 |
| [`docs/archive/RADAR_TRENDWATCHING_IMPLEMENTATION.md`](docs/archive/RADAR_TRENDWATCHING_IMPLEMENTATION.md) | Реализация broad Radar |
| [`CHANGELOG.md`](CHANGELOG.md) | Keep a Changelog |
