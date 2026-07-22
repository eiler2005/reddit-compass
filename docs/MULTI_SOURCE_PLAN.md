# reddit-compass → multi-source: План расширения на СМИ и другие источники

> **Статус:** план сформирован 2026-07-22. Реализация — Phase 6 ROADMAP, деплой на VPS HostKey.
> Цель: единый конвейер «сбор → JSONL → trends analysis → рекомендации» для Reddit + СМИ +
> других источников. Не просто скрапер, а **аналитический конвейер трендов** из «голоса улицы»
> (Reddit), «голоса разработчика» (HN) и «голоса СМИ» (NYT, WaPo, FT, Wired).

---

## 1. Зачем: ценность для книги и колонок

Книга «Когда интеллект стал дешёвым» (3 тома) строится на трёх слоях нарратива:

| Слой | Источник | Что даёт |
|---|---|---|
| **Голос улицы** | Reddit (19 сабреддитов) | Живые реакции, боли, кейсы, «что люди чувствуют» |
| **Голос разработчика** | Hacker News | Технические тренды, AI-стартапы, «что строят» |
| **Голос СМИ** | NYT, WaPo, FT, Wired, Medium | Бизнес-нарратив, «что пишут медиа», фрейминг |
| **Голос продуктов** | ProductHunt, IndieHackers | Новые AI-продукты, «один человек + AI = компания» |

Сейчас есть только Reddit. Расширение даст **перекрёстную валидацию трендов**: если тема
виральная на Reddit + обсуждается на HN + попала в NYT — это сильный сигнал для колонки/главы.

---

## 2. Ladder (⭐8.7k) — инфраструктура для СМИ

**Ladder** (github.com/everywall/ladder) — self-hosted HTTP-proxy (Go), альтернатива 12ft.io.
Per-domain ruleset: подмена User-Agent, cookies, удаление paywall-блоков, FlareSolverr для
Cloudflare. Docker-контейнер, ~50MB RAM.

### 2.1. СМИ в ruleset Ladder (ladder-rules)

#### США

| Сайт | Что делает правило | Ценность для нас |
|---|---|---|
| **nytimes.com** | Подмена UA на Googlebot + cookie-манипуляция (`nyt-a=; nyt-gdpr=0; nyt-geo=DE`) + удаление paywall-баннеров + referer `google.com` | Главный источник бизнес/AI-нарратива в США |
| **washingtonpost.com** | Удаление paywall-блоков (`subscribe-promo`), восстановление blur-изображений (`filter: ''`) | Политика + технологии, AI regulation |
| **time.com** | То же что NYT (Googlebot UA + cookie) | Массовый нарратив, «что думает мейнстрим» |
| **usatoday.com** | Удаление рекламных баннеров (`roadblock-container`, `gnt_nb`) | Массовый рынок, layoff-истории |
| **foxbusiness.com / foxnews.com** | Удаление видео и рекламы | Бизнес-консервативный взгляд |
| **theathletic.com** | Удаление overlay-paywall (`div[id*="overlay"]`, `slideup-`) | Спорт-бизнес (AI в спорте) |
| **medium.com** | Referer-трюк (`t.co/x?amp=1`) + удаление CSP + очистка cookies | Лонгриды про AI, кейсы, «голос практиков» |
| **americanbanker.com** | Удаление inline-gate (`.inline-gate` → remove class) | Финансы + AI, банковский сектор |

#### Condé Nast (9 сайтов, единое правило)

| Сайт | Ценность |
|---|---|
| **wired.com** | Технологии + культура, AI-тренды (приоритет) |
| **newyorker.com** | Лонгриды, AI-этика, общество |
| **vanityfair.com** | Культура + бизнес |
| **gq.com** | Мужская аудитория, карьера + AI |
| **vogue.com** | Мода + AI (AI-дизайн, deepfake) |
| **bonappetit.com** | AI в food-tech |
| **epicurious.com** | AI в food-tech |
| **cntraveler.com** | AI в travel |
| **architecturaldigest.com** | AI в дизайне |

Правило: удаление paywall-бара (`.paywall-bar`, `MessageBannerWrapper-`).

#### Финансы

| Сайт | Что делает | Ценность |
|---|---|---|
| **ft.com** (Financial Times) | Referer `t.co/x?amp=1` + удаление CSP + cookie-баннер + ads | Главный финансовый нарратив, AI-экономика |

#### Европа

| Сайт | Страна | Метод | Ценность |
|---|---|---|---|
| **tagesspiegel.de** | Германия | AMP-версия (`?amp=1`) | Немецкий взгляд на AI |
| **nzz.ch** | Швейцария | Удаление `.dynamic-regwall` | Качественная аналитика |
| **thestar.com** + 6 канадских | Канада | Torstar chain: расшифровка encrypted content, удаление `subscriber-offers` | Канадский AI-нарратив (Toronto AI cluster) |
| **demorgen.be** | Бельгия | Googlebot UA + `isBot=true` cookie + удаление temptation | Бельгийский взгляд |
| **apache.be** | Бельгия | Удаление `#spb-block-apachepopupblock` + overflow restore | Investigative journalism |
| **kw.be** | Бельгия | Googlebot UA + удаление `#paywall-modal` | Региональные новости |

### 2.2. Чего НЕТ в ruleset (и почему)

| Сайт | Почему нет | Альтернатива для нас |
|---|---|---|
| **WSJ** (Wall Street Journal) | Агрессивный серверный paywall — контент не отдаётся без подписки, клиентский JS не помогает | RSS-заголовки (бесплатно) или подписка |
| **Bloomberg** | Аналогично — серверная отдача только для подписчиков | RSS-заголовки, Bloomberg Terminal (платно) |
| **The Economist** | Серверный paywall, шифрование контента | RSS-заголовки, подписка |
| **Reuters / AP** | Бесплатны, не нужен bypass | Прямой доступ через RSS/API |

### 2.3. Приоритетные СМИ для книги

Для «Когда интеллект стал дешёвым» наиболее релевантны:

1. **wired.com** — AI-тренды, культура технологий (Condé Nast, лёгкий bypass)
2. **nytimes.com** — бизнес/AI нарратив, layoff-истории, regulation
3. **ft.com** — AI-экономика, финансы, enterprise adoption
4. **washingtonpost.com** — политика + AI, regulation, Big Tech
5. **medium.com** — практические кейсы, «голос практиков», AI-инструменты
6. **newyorker.com** — AI-этика, лонгриды, общество

---

## 3. Другие источники (без Ladder)

| # | Источник | API/доступ | Усилие | Ценность |
|---|---|---|---|---|
| 1 | **Hacker News** | Algolia Search API (`hn.algolia.com/api/v1/search`) — бесплатно, без ключей, без rate limit | ~1 дн | AI-стартапы, технические тренды, «что строят разработчики» |
| 2 | **ProductHunt** | GraphQL API (бесплатно, OAuth token) | ~0.5 дн | Новые AI-продукты, «что запускают» |
| 3 | **IndieHackers** | RSS (`indiehackers.com/feed.xml`) + HTML parse | ~1 дн | «Один человек + AI = компания», кейсы соло-фаундеров |
| 4 | **arXiv** | API (`export.arxiv.org/api/query`) — бесплатно | ~0.5 дн | Академические AI-тренды (опционально) |
| 5 | **Reddit** | Текущий клиент (Playwright JSON API) | ✅ готово | Голос улицы |

---

## 4. Архитектура мульти-источника

```
reddit-compass (расширение)
│
├── sources/
│   ├── reddit/           ← текущий клиент (Playwright + JSON API + RSS fallback)
│   ├── hackernews/       ← Algolia API (aiohttp, без браузера, без ключей)
│   ├── news/             ← Ladder proxy → NYT, WaPo, FT, Wired, Medium
│   ├── producthunt/      ← GraphQL API
│   └── indiehackers/     ← RSS + HTML parse
│
├── unified output:
│   data/snapshots/YYYY-MM-DD/
│     posts.jsonl          ← Reddit (текущий формат)
│     hackernews.jsonl     ← HN stories (тот же PostCard + source="hackernews")
│     news.jsonl           ← СМИ через Ladder (тот же PostCard + source="news")
│     ...
│
├── trends_analysis.py    ← уже source-agnostic (читает JSONL из snapshot)
│   расширение: поле `source` в PostCard, группировка по источникам в отчёте
│
└── deploy/hostkey/
    ├── docker-compose.yml   ← reddit-compass (текущий)
    ├── ladder/              ← Ladder proxy (Go, Docker, loopback:8080)
    │   ├── docker-compose.yml
    │   └── ruleset.yaml     ← NYT, WaPo, FT, Wired, Medium, Condé Nast
    └── flaresolverr/        ← опционально (Cloudflare bypass)
```

### Контракт данных (расширение PostCard)

```python
# Новое поле в PostCard:
source: str = "reddit"  # "reddit" | "hackernews" | "news" | "producthunt" | "indiehackers"
source_url: str = ""    # оригинальный URL статьи/поста
```

`trends_analysis.py` группирует по `source` и выдаёт секции:
- «Голос улицы (Reddit)» — текущий формат
- «Голос разработчика (HN)» — топ stories по AI
- «Голос СМИ» — заголовки + ключевые цитаты из NYT/WaPo/FT/Wired

---

## 5. Деплой на VPS HostKey «Hermes»

```
/opt/reddit-compass/          ← текущий стек (Phase 2)
  docker-compose.yml
  .env

/opt/ladder/                  ← новый стек (Phase 6)
  docker-compose.yml          ← ladder + flaresolverr (опционально)
  ruleset.yaml                ← per-domain правила для СМИ
  .env                        ← USERPASS для basic auth

Host-cron:
  03:17  reddit-compass nightly   (текущий)
  04:30  reddit-compass news      (новый: сбор СМИ через Ladder)
  05:00  reddit-compass hn        (новый: сбор HN)
```

**Сеть:** Ladder на loopback:8080, доступен только из docker-сети `reddit-compass_net`.
Наружу не выставлять. Basic auth (`USERPASS`) как дополнительная защита.

---

## 6. Пошаговый план реализации

### Шаг 1: Hacker News (первый мульти-источник)

- `sources/hackernews/client.py` — aiohttp GET к `hn.algolia.com/api/v1/search`
- Query: `"AI agents" OR "AI layoffs" OR "LLM" OR "vibe coding"`, tags: `story`, hitsPerPage: 50
- Выход: `hackernews.jsonl` в snapshot (PostCard + source="hackernews")
- CLI: `reddit-compass hn`
- Тесты: парсер Algolia JSON, дедупликация
- **Усилие:** ~1 день

### Шаг 2: Ladder на HostKey

- Docker-compose: `ghcr.io/everywall/ladder:latest` + ruleset.yaml
- Ruleset: NYT, WaPo, FT, Wired, Medium, New Yorker (из ladder-rules)
- Basic auth, loopback-only
- Проверка: `curl http://localhost:8080/api/https://www.wired.com/story/...` → полный текст
- **Усилие:** ~0.5 дня

### Шаг 3: Source-адаптер для СМИ

- `sources/news/client.py` — aiohttp GET через Ladder proxy
- Поиск: RSS-фиды NYT/WaPo/Wired по ключевым словам → URL → Ladder → parse HTML → текст
- Выход: `news.jsonl` (PostCard + source="news" + source_url)
- CLI: `reddit-compass news`
- **Усилие:** ~2 дня

### Шаг 4: Расширение trends_analysis

- Поле `source` в PostCard
- Группировка в отчёте: «Голос улицы», «Голос разработчика», «Голос СМИ»
- Перекрёстная валидация: тема в 2+ источниках → «сильный сигнал»
- **Усилие:** ~1 день

### Шаг 5: ProductHunt + IndieHackers (опционально)

- ProductHunt GraphQL API
- IndieHackers RSS
- **Усилие:** ~1.5 дня

---

## 7. Ограничения и риски

| Риск | Митигация |
|---|---|
| NYT/WaPo изменят paywall (ruleset устареет) | Ladder ruleset — живой (ladder-rules repo обновляется); мониторить |
| Cloudflare на СМИ | FlareSolverr (headless browser) — опциональный контейнер |
| WSJ/Bloomberg недоступны | RSS-заголовки (бесплатно) или подписка; не блокирует Phase 6 |
| Rate limit на HN Algolia | 10 000 hits/hour — более чем достаточно |
| Юридический риск (paywall bypass) | Только для личного research, не публикуем сырые статьи, только аналитику |
| Ladder на Go — чужой стек | Минимальная интеграция: только HTTP-вызовы к proxy; не модифицируем |

---

## 8. Метрики успеха

- [ ] HN: 50+ stories по AI-темам в каждом snapshot
- [ ] СМИ: 10+ статей из NYT/WaPo/FT/Wired в каждом snapshot
- [ ] Перекрёстная валидация: ≥1 тема в 2+ источниках в nightly-отчёте
- [ ] Время nightly (reddit + hn + news): < 20 мин
- [ ] Ноль падений из-за paywall (Ladder ruleset актуален)
