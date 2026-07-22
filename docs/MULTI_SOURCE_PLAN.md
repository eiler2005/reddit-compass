# reddit-compass → trend-radar: Мульти-источники

> **Статус:** реализовано 2026-07-22. 21 источник, 5 кластеров, 4 адаптера.
> Деплой: VPS HostKey `204.168.239.217:/opt/reddit-compass/`.

---

## 1. Кластеры источников

### 📰 Кластер 1: «Мейнстрим-нарратив» (что слышат массы)

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 1 | **nytimes.com** | Ladder (Googlebot UA + cookie) | 🇺🇸 | Главный нарратив США |
| 2 | **washingtonpost.com** | Ladder (JS paywall removal) | 🇺🇸 | Политика + tech, regulation |
| 3 | **time.com** | Ladder (Googlebot UA) | 🇺🇸 | Массовый фрейминг |
| 4 | **usatoday.com** | Ladder (ad removal) | 🇺🇸 | Пульс «средней Америки» |
| 5 | **bbc.com** | RSS (бесплатно) | 🇬🇧 | Глобальный не-US взгляд |
| 6 | **theguardian.com** | RSS + Open API (бесплатно) | 🇬🇧 | UK, расследования |

### 💰 Кластер 2: «Бизнес и финансы» (куда идут деньги)

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 7 | **ft.com** | Ladder (referer t.co) | 🇬🇧 | Главный финансовый нарратив |
| 8 | **americanbanker.com** | Ladder (gate removal) | 🇺🇸 | Банки + финтех + AI |
| 9 | **foxbusiness.com** | Ladder (ad removal) | 🇺🇸 | Бизнес-консервативный взгляд |
| 10 | **reuters.com** | RSS (бесплатно) | 🌐 | Мировые новости, «первый сигнал» |

### 🔬 Кластер 3: «Технологии и культура»

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 11 | **wired.com** | Ladder (Condé Nast) | 🇺🇸 | Tech + общество, AI-тренды |
| 12 | **newyorker.com** | Ladder (Condé Nast) | 🇺🇸 | Лонгриды, AI-этика |
| 13 | **vanityfair.com** | Ladder (Condé Nast) | 🇺🇸 | Big Tech + власть + культура |
| 14 | **techcrunch.com** | RSS (бесплатно) | 🇺🇸 | Стартапы, funding rounds |
| 15 | **theverge.com** | RSS (бесплатно) | 🇺🇸 | Consumer tech, Big Tech |
| 16 | **arstechnica.com** | RSS (бесплатно) | 🇺🇸 | Глубокая tech-аналитика |

### 🗣 Кластер 4: «Голоса» (что говорят люди)

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 17 | **reddit.com** (18 сабреддитов) | Playwright JSON | 🌐 | Живые реакции, боли, кейсы |
| 18 | **news.ycombinator.com** | Algolia API (бесплатно) | 🌐 | «Голос разработчика» |
| 19 | **medium.com** | Ladder (referer t.co/amp) | 🌐 | Кейсы практиков, лонгриды |

### 📊 Кластер 5: «Массовый пульс»

| # | Источник | Доступ | Страна | Зачем |
|---|---|---|---|---|
| 20 | **foxnews.com** | Ladder (ad removal) | 🇺🇸 | «Другая Америка» |
| 21 | **producthunt.com** | GraphQL API (бесплатно) | 🌐 | Что запускают прямо сейчас |

---

## 2. Адаптеры (реализованы)

| Адаптер | Файл | Источников | CLI |
|---|---|---|---|
| **RSS** | `sources/rss.py` | 6 (BBC, Guardian, Reuters, TechCrunch, Verge, Ars) | `reddit-compass rss` |
| **Ladder** | `sources/ladder.py` | 12 (NYT, WaPo, FT, Wired, Medium, Time, USA Today, Fox×2, New Yorker, VF, AmBanker) | `reddit-compass ladder` |
| **Hacker News** | `sources/hackernews.py` | 1 (Algolia, 8 AI-запросов) | `reddit-compass hn` |
| **ProductHunt** | `sources/producthunt.py` | 1 (GraphQL) | `reddit-compass ph` |
| **Reddit** | `client.py` + `fetch_subreddits.py` | 18 сабреддитов | `reddit-compass fetch` |

---

## 3. Выходные данные

```
data/snapshots/YYYY-MM-DD/
├── posts.jsonl          ← Reddit (18 сабреддитов)
├── hackernews.jsonl     ← HN (Algolia)
├── rss.jsonl            ← RSS (6 источников)
├── ladder.jsonl         ← Ladder (12 paywall)
├── producthunt.jsonl    ← ProductHunt
├── virality.jsonl       ← Детекция виральности
├── signals.jsonl        ← LLM-анализ (Qwen)
├── trends-report.md     ← Trends analysis
└── signals-report.md    ← LLM-синтез
```

Все в формате PostCard (единая схема), поле `subreddit` = имя источника,
`monitoring_type` = "hot"|"top"|"search"|"rss"|"ladder"|"api".

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

Host-cron:
  17 3 * * *  reddit-compass nightly   (Reddit + trends + stealth)
  30 3 * * *  reddit-compass rss       (RSS-источники)
  45 3 * * *  reddit-compass hn        (Hacker News)
  00 4 * * *  reddit-compass ladder    (Ladder, если задеплоен)
  15 4 * * *  reddit-compass ph        (ProductHunt)
  30 4 * * *  reddit-compass signals   (LLM-анализ)
```

---

## 5. Ladder (инфраструктура для paywall)

**Ladder** (⭐8.7k, Go) — self-hosted proxy, деплой на HostKey:

```bash
docker run -p 127.0.0.1:8080:8080 -d \
  --env RULESET=https://raw.githubusercontent.com/everywall/ladder-rules/main/ruleset.yaml \
  --name ladder ghcr.io/everywall/ladder:latest
```

Ruleset: 33 домена (мы используем 12). Per-domain правила: Googlebot UA,
cookie-манипуляция, JS-инъекции, referer-трюки.

**Чего НЕТ в Ladder (и не будет):** WSJ, Bloomberg, The Economist (серверный paywall).

---

## 6. Ограничения и риски

| Риск | Митигация |
|---|---|
| NYT/WaPo изменят paywall | Ladder ruleset — community-maintained, мониторить |
| Cloudflare на СМИ | FlareSolverr (опциональный контейнер) |
| WSJ/Bloomberg недоступны | RSS-заголовки или подписка |
| Rate limit на RSS | 6 источников × 2 фида = 12 запросов, пауза не нужна |
| ProductHunt API лимиты | 6250 запросов/15мин — более чем достаточно |
| Юридический риск (paywall) | Только личный research, не публикуем сырые статьи |

---

## 7. Метрики успеха

- [ ] RSS: 50+ статей за прогон (6 источников)
- [ ] HN: 50+ stories по AI-темам
- [ ] Ladder: 10+ страниц из paywall-источников
- [ ] ProductHunt: 20 продуктов
- [ ] Reddit: 400+ постов (18 сабреддитов)
- [ ] Перекрёстная валидация: ≥1 тема в 3+ источниках в nightly-отчёте
- [ ] Время nightly (все источники): < 20 мин
