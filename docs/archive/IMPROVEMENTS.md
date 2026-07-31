# reddit-compass — Ранжированный план улучшений

> **Статус:** сформирован 2026-07-22 по итогам анализа конкурентов, текущего ROADMAP и рабочего
> контекста монорепо книги. Живой документ — обновлять по мере реализации.
> Полный конкурентный анализ (ландшафт GitHub, таблицы фич, направления для изучения) —
> в [COMPETITIVE_ANALYSIS.md](../COMPETITIVE_ANALYSIS.md).

---

## Легальность скрапинга Reddit

Коротко: **серая зона, на практике безопасная для личного research.**

| Аспект | Статус |
|---|---|
| GitHub | 2454+ репо «reddit scraper», топ ⭐2592. Не удаляет. |
| Reddit ToS | Формально запрещает автоматизированный доступ без API (§3). На практике — блокирует IP (429), не судит. Ни одного известного кейса Reddit vs scraper. |
| Наш случай | Публичные данные, read-only, rate limit 4 c, стандартный UA, не коммерческий продукт, не публикуем сырые данные. Риск минимальный. |
| 100% легально | OAuth API (asyncpraw) — бесплатно, 100 req/min. В техдолгу ROADMAP. |

**Рекомендация:** текущий подход (Playwright JSON API) оставить как основной; OAuth (asyncpraw) —
как опциональный движок для полной ToS-чистоты. Proxies — **не добавлять** (запрет в AGENTS.md).

> **Контекст:** заявка на Reddit Data API уже подана (статус: SUBMITTED_AWAITING_REDDIT_REVIEW,
> 2026-07-22; см. `AiNativeBook_Draft_26/services/reddit-monitor/REDDIT-DATA-API-ACCESS.md`).
> Инструкция по asyncpraw и `secrets/reddit/reddit.env` — в `secrets/reddit/README.md` монорепо.
> При одобрении заявки OAuth-движок (п. 8) становится приоритетнее.

---

## Ранжирование

Критерии: влияние на рабочий процесс автора книги → усилие → соответствие архитектуре →
зависимости → ограничения AGENTS.md.

### Tier 1 — Делать в первую очередь

| # | Улучшение | Источник | Усилие | Зачем |
|---|---|---|---|---|
| 1 | **LLM-анализ сигналов** (`signals.py`) | Reddit_Scrapper (GPT-анализ болей) + ROADMAP Phase 3 | ~3–5 дн | Главный мультипликатор ценности: 73 сырых поста → «5 тем для колонок, бизнес-релевантность 1–10, pain points, buying intent». Заменяет захардкоженные маппинги `trends_analysis.py`. Multi-dimensional scoring (техническая глубина, бизнес-релевантность, связь с книгой) — естественно ложится сюда. |
| 2 | **SQLite-хранилище** | reddit-universal-scraper + Reddit_Scrapper | ~2–3 дн | JSONL → SQLite (аддитивно, JSONL остаётся). Запросы: «все посты по AI за месяц», «топ по score за неделю», «тренды по неделям». Исторические данные для книги — нарративы меняются месяцами, без БД не отследить. |
| 3 | **Уведомления** (Telegram) | reddit-universal-scraper + ROADMAP Phase 4 | ~1 дн | «Ночной разбор готов: 73 поста, 3 рекомендации, 1 виральный сигнал». Минимум усилий, максимум удобства для ежедневного workflow. |

### Tier 2 — Делать дальше

| # | Улучшение | Источник | Усилие | Зачем |
|---|---|---|---|---|
| 4 | **Dry run mode** | reddit-universal-scraper | ~2–4 ч | `--dry-run`: проверить, что соберётся, без записи. Полезно при добавлении новых сабреддитов/ключевых слов в профиль. |
| 5 | **VPS-деплой** (host-cron nightly) | ROADMAP Phase 2 | ~1–2 дн | Автономный ночной прогон на HostKey «Hermes». Скелет готов (`deploy/hostkey/`). Включение — по подтверждению. |
| 6 | **Docker CI/CD** (build & publish) | reddit-universal-scraper | ~0.5 дн | GitHub Actions уже есть для lint/test. Добавить Docker build & push в GHCR — инкрементально. VPS будет тянуть образ из registry. |
| 7 | **Exploratory subreddits** | Reddit_Scrapper (auto-refresh exploratory list) | ~2–3 дн | Если пост из нового сабреддита виральный → предложить добавить в monitoring. Сервис сам находит новые источники. Но: у автора кураторский список для книги, поэтому приоритет ниже. |

### Tier 3 — Отложить / по ситуации

| # | Улучшение | Источник | Усилие | Зачем / Почему отложено |
|---|---|---|---|---|
| 8 | **OAuth API** (asyncpraw) | ROADMAP техдолг | ~2 дн | 100% ToS-чистота. Текущий Playwright-подход работает; OAuth — страховка на случай ужесточения Reddit. |
| 9 | **Повышение покрытия тестами** | ROADMAP техдолг | ongoing | Гейт 60%, реально ~75%. Сеть/браузер/оркестрация вне гейта. |
| 10 | **Plugin system** | reddit-universal-scraper | ~3–5 дн | Расширяемая пост-обработка (sentiment, dedupe, keywords). Для одного пользователя и двух модулей анализа (trends + signals) — преждевременно. Вернуться, если анализаторов станет 4+. |

### НЕ брать

| Улучшение | Почему нет |
|---|---|
| **Proxies** (rotating) | Прямой запрет в AGENTS.md: «Не добавлять proxy-ротацию, параллельные личности и обход блокировок». Альтернатива для 429 — OAuth API (п. 8). |
| **Streamlit dashboard** | Избыточно для одного пользователя. Markdown + JSONL покрывают. Если понадобится — Phase 5 (веб-дашборд в editorial-стиле). |
| **Parquet export** | Преждевременно. SQLite + JSONL покрывают аналитические потребности. |
| **REST API** | Преждевременно. Сервис локальный/batch, не серверный. Появится с Phase 5, если понадобится. |
| **Built-in scheduler** | host-cron (Phase 2) проще, надёжнее, не тащит зависимости. Встроенный scheduler — лишний рантайм. |

---

## Маппинг на ROADMAP

| ROADMAP фаза | Что добавляется из этого плана |
|---|---|
| Phase 2 (VPS) | + Docker CI/CD (п. 6) — образ в GHCR до деплоя |
| Phase 3 (LLM) | + Multi-dimensional scoring (п. 1) — часть signals.py |
| Phase 4 (Уведомления) | Без изменений (п. 3) |
| Phase 5 (Дашборд) | Без изменений |
| **Новая: Phase 3.5** | **SQLite-хранилище (п. 2)** — между LLM-анализом и уведомлениями: данные уже структурированы, сигналы пишутся в БД |
| **Новая: Phase 2.5** | **Dry run (п. 4)** — быстрый win, можно сделать до VPS |
| Техдолг | + Exploratory subreddits (п. 7) как опция |

---

## Источники анализа

- **Полный конкурентный анализ:** [COMPETITIVE_ANALYSIS.md](../COMPETITIVE_ANALYSIS.md) — ландшафт
  GitHub (2454 репо), таблицы фич по 3 проектам, направления для дальнейшего изучения.
- **reddit-universal-scraper** (⭐569): plugin system, scheduled scraping, notifications, parquet,
  SQLite, dry run, proxies, REST API, Docker CI/CD.
- **yars** (⭐224): `.json` requests без API keys, rotating proxies warning, max 2552 posts.
- **Reddit_Scrapper** (⭐198): GPT-4 analysis of pain points, multi-dimensional scoring,
  focused + exploratory subreddits, Streamlit dashboard, cost controls.
- Текущий ROADMAP reddit-compass (Phases 2–5, техдолг).
- Рабочий контекст: `AiNativeBook_Draft_26/services/reddit-monitor`, `publishing/ru-platforms.md`.
- Reddit Data API access: `AiNativeBook_Draft_26/services/reddit-monitor/REDDIT-DATA-API-ACCESS.md`
  (заявка подана, ожидает рассмотрения).
- Harvest-данные: `research/case-sources/reddit/harvests/` (73 поста, 16 сабреддитов, маппинг
  на колонки RBC и главы книги).
- Reddit-сентимент для Тома 3: `research/next-book-tom3-ideas-2026-07.md` (§2.4 «Voice of users»).
