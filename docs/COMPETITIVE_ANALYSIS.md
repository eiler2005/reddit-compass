# reddit-compass — Конкурентный анализ

> **Статус:** сформирован 2026-07-22. Живой документ — дополнять по мере анализа новых проектов.
> Ранжированный план улучшений на основе этого анализа — в [IMPROVEMENTS.md](archive/IMPROVEMENTS.md).

---

## 1. Ландшафт GitHub: Reddit-скраперы

2454 репозитория по запросу «reddit scraper». Топ:

| Проект | ⭐ | Что делает | Чем отличается от reddit-compass |
|---|---|---|---|
| **bulk-downloader-for-reddit** | 2592 | Архивирует контент (медиа, посты) | Downloader, не мониторинг |
| **RedditDownloader** | 1163 | Скачивает медиа по выбору | Downloader, не тренды |
| **URS** (Universal Reddit Scraper) | 1009 | Comprehensive scraping/archival CLI | Архивация, не анализ |
| **reddit-universal-scraper** | 569 | Любой сабреддит или юзер | Скрапер, не мониторинг |
| **yars** (Yet Another Reddit Scrapper) | 224 | Без API keys, search + posts + images | Ближе всего, но без trends analysis |
| **Reddit_Scrapper** | 198 | Marketing pain points через GPT | Ближе всего по смыслу, но без кластеров |
| **easy-reddit-downloader** | 174 | Headless downloader | Downloader |

### Вывод: аналога reddit-compass на GitHub нет

Все топ-проекты — downloaders / archival tools. Ни один не делает:

- ❌ Мониторинг трендов по **кластерам** сабреддитов (AI, доверие, vibe coding, увольнения)
- ❌ Ночной разбор с привязкой к **бизнес-контексту** (колонки, дайджест, книга)
- ❌ Playwright JSON API из браузера (без API credentials) + RSS fallback
- ❌ Trends analysis с рекомендациями «что взять в работу»
- ❌ Комментарии (top-N по score) как «голос улицы»

**Самые близкие:**

- **yars** (⭐224) — без API keys, но только search/posts/images, без анализа
- **Reddit_Scrapper** (⭐198) — marketing pain points через GPT, но без кластеров и ночного разбора

reddit-compass уникален в нише **«мониторинг трендов → редакционный разбор →
бизнес-рекомендации»**. Это не скрапер, а аналитический конвейер. На GitHub нет аналога,
который делает trends analysis с привязкой к конкретному бизнес-контексту.

---

## 2. Что можно взять: reddit-universal-scraper (⭐569) — самый зрелый

| Фича | Что это | Зачем нам |
|---|---|---|
| **Plugin System** | Расширяемая пост-обработка: sentiment, dedupe, keywords | У нас `trends_analysis.py` захардкожен. Плагины позволят добавлять анализаторы без изменения ядра |
| **Scheduled Scraping** | Cron-style job scheduling внутри сервиса | У нас `nightly_run.py` запускается вручную. Встроенный scheduler = автономность |
| **Notifications** | Discord & Telegram alerts по завершении | Уведомление в Telegram: «Ночной разбор готов, 73 поста, 3 рекомендации» |
| **Parquet Export** | Analytics-ready формат для DuckDB/warehouses | JSONL → Parquet: можно открыть в DuckDB и делать SQL-запросы по трендам |
| **SQLite Database** | Structured storage с auto-backup | JSONL files → SQLite: запросы «все посты по AI за месяц», «топ по score за неделю» |
| **Dry Run Mode** | Тест scrape rules без сохранения данных | `--dry-run`: проверить, что соберётся, без записи |
| **Proxies** | Rotating proxies для избежания rate limit | У нас 429 на 15/19 сабреддитов. Proxies решат |
| **REST API** | Connect Metabase, Grafana, DuckDB | Dashboard с графиками трендов |
| **Docker Build & Publish** | CI/CD для Docker image | У нас Dockerfile есть, но нет CI/CD |

---

## 3. Что можно взять: yars (⭐224) — самый простой

| Фича | Что это | Зачем нам |
|---|---|---|
| **`.json` requests без API keys** | Простой `requests.get(url + '.json')` | У нас Playwright (тяжёлый). yars показывает, что `.json` работает без браузера — но мы проверяли, curl возвращает HTML. Возможно, yars использует cookies или другой User-Agent |
| **Rotating proxies warning** | «Use with rotating proxies, or Reddit might gift you with an IP ban» | Подтверждение: без proxies Reddit банит |
| **Max 2552 posts at once** | Лимит без proxies | Ориентир для нашего rate limiting |

---

## 4. Что можно взять: Reddit_Scrapper (⭐198) — самый близкий по смыслу

| Фича | Что это | Зачем нам |
|---|---|---|
| **GPT-4 analysis of pain points** | LLM анализирует посты, находит маркетинговые боли | У нас `trends_analysis.py` без LLM. Можно добавить: «найди в этих постах темы для колонок» |
| **Multi-dimensional scoring** | Technical depth, implementability, emotional intensity | У нас только score + num_comments. Можно добавить: «бизнес-релевантность», «связь с книгой» |
| **Focused + exploratory subreddits** | Auto-refresh exploratory list based on discoveries | У нас фиксированный список 19 сабреддитов. Можно: «если пост из r/xxx viral, добавить r/xxx в monitoring» |
| **Streamlit dashboard** | Interactive web UI для browsing/filtering | У нас только Markdown. Dashboard удобнее для ежедневного просмотра |
| **Cost controls** | Лимиты на API calls | У нас нет лимитов на Playwright requests |

---

## 5. Предварительный топ-5: что взять в первую очередь

> ⚠️ Это первичная оценка из сессии 2026-07-22. Финальное ранжирование —
> в [IMPROVEMENTS.md](archive/IMPROVEMENTS.md) (учитывает ограничения AGENTS.md, архитектуру, усилия).

1. **Proxies** (yars + reddit-universal-scraper) — решит 429 на 15/19 сабреддитов. Без этого
   сервис собирает ~30% данных.
   > **Ограничение:** AGENTS.md запрещает proxy-ротацию. Альтернатива — OAuth API (asyncpraw);
   > заявка на Reddit Data API подана (SUBMITTED_AWAITING_REDDIT_REVIEW, 2026-07-22).

2. **SQLite** (reddit-universal-scraper + Reddit_Scrapper) — JSONL → SQLite. Запросы: «все посты
   по AI за месяц», «топ по score», «тренды по неделям». Исторические данные.

3. **LLM-анализ** (Reddit_Scrapper) — `trends_analysis.py` + LLM: «прочитай эти 73 поста, найди
   5 тем для колонок, оцени бизнес-релевантность по шкале 1–10». Вместо захардкоженных маппингов.

4. **Notifications** (reddit-universal-scraper) — Telegram-уведомление: «Ночной разбор готов».
   Или email.

5. **Exploratory subreddits** (Reddit_Scrapper) — авто-расширение: если пост из нового сабреддита
   viral, добавить его в monitoring. Сервис сам находит новые источники.

---

## 6. Что НЕ брать

| Фича | Почему нет |
|---|---|
| **Streamlit dashboard** | Избыточно для одного пользователя. Markdown + JSONL достаточно. Если понадобится — Grafana + REST API |
| **Parquet** | Преждевременно. SQLite + JSONL покрывают потребности |
| **REST API** | Преждевременно. Сервис локальный, не серверный |
| **Built-in scheduler** | host-cron (Phase 2 ROADMAP) проще, надёжнее, не тащит зависимости в рантайм |

---

## 7. Направления для дальнейшего анализа

Проекты для детального изучения (README, архитектура, код):

- [ ] **yars** — как именно работает `.json` без браузера? Cookies? User-Agent? Может ли заменить
      Playwright на лёгкий `aiohttp` + `.json`?
- [ ] **Reddit_Scrapper** — как устроен GPT-анализ pain points? Промпты, структура, scoring.
      Перенести паттерн в `signals.py` (Phase 3).
- [ ] **reddit-universal-scraper** — plugin system: интерфейс, хуки, порядок обработки. Стоит ли
      переносить или достаточно модулей `trends_analysis.py` + `signals.py`?
- [ ] **URS** (⭐1009) — comprehensive CLI: какие команды, как устроен конфиг, есть ли trends?
- [ ] **bulk-downloader-for-reddit** (⭐2592) — архитектура, тесты, CI/CD. Образец зрелого
      Python-проекта в Reddit-нише.

---

## 8. Ladder (⭐8.7k) — мульти-источники и paywall bypass

**Ladder** (github.com/everywall/ladder) — self-hosted HTTP-proxy (Go), альтернатива 12ft.io.
Per-domain ruleset: подмена UA, cookies, удаление paywall-блоков, FlareSolverr для Cloudflare.

### СМИ в ruleset Ladder

| Группа | Сайты | Метод |
|---|---|---|
| **США (top)** | nytimes.com, washingtonpost.com, time.com | Googlebot UA + cookie + JS-удаление paywall |
| **Финансы** | ft.com, americanbanker.com | Referer-трюк, удаление gate |
| **Condé Nast** | wired.com, newyorker.com, vanityfair.com, gq.com, vogue.com + 4 | Удаление paywall-бара |
| **Массовые** | usatoday.com, foxnews.com, foxbusiness.com | Удаление рекламы/видео |
| **Спорт** | theathletic.com | Удаление overlay |
| **Платформы** | medium.com | Referer t.co/amp |
| **Европа** | tagesspiegel.de, nzz.ch, thestar.com + 6 CA, 3 BE | AMP / Googlebot / расшифровка |

### Чего НЕТ (серверный paywall — Ladder не поможет)

WSJ, Bloomberg, The Economist, Reuters (бесплатен, не нужен).

### Что взять для reddit-compass (Phase 6)

1. **Ladder на HostKey** — Docker-контейнер, loopback:8080. Ruleset: NYT, WaPo, FT, Wired, Medium.
2. **Source-адаптеры** — `sources/hackernews/` (Algolia API), `sources/news/` (через Ladder).
3. **Единый JSONL** — поле `source`, общий `trends_analysis.py`.
4. **Hacker News** — первый кандидат (бесплатно, без ключей, без paywall, ~1 дн).

### Результаты интеграционного теста (2026-07-22)

Полный прогон `reddit-compass fetch` (19 сабреддитов, Playwright):

- **15/16** доступных сабреддитов вернули данные (622 поста)
- **1 × 429** (r/AskReddit, восстановлен retry 1/2) — vs 15/19 до оптимизации
- **r/deepfakes** — 404 (мёртвый, убрать из профиля)
- **aiohttp** — 403 сразу (Reddit блокирует без cookies); Playwright — de facto движок
- **Время:** ~35с/сабреддит, полные 19 ≈ 12 мин
- **Вывод:** proxy пока не нужен; stealth (jitter + backoff) на nightly снизит остаточный 429
