# Архитектура reddit-compass

Статус: **каноническое описание границ, контрактов и переносимости.**

## 1. Цель и главный инвариант

reddit-compass — автономный сервис сбора «голоса улицы» с Reddit: живые реакции, кейсы, боли и
нарративы. Он не пересказывает всю ленту, а показывает, **куда смотреть**.

Главный архитектурный инвариант:

> Сервис собирает данные и генерирует артефакты. Потребители (контент, дайджест, ресёрч) читают
> артефакты, но не зависят от рантайма сервиса.

- Сервис ничего не импортирует из внешних проектов.
- Связь с потребителями — только через файлы: JSONL (данные) и Markdown (отчёты).
- Всё поведение — config-driven (профиль), пути — внутри проекта и через переменные окружения.

## 2. Контекст системы

```text
Reddit (www.reddit.com)
    |  Playwright headless Chromium → JSON API   (основной)
    |  Atom RSS через aiohttp                     (fallback: только hot, без score)
    v
reddit-compass
    |
    +--> data/snapshots/YYYY-MM-DD/
    |      posts.jsonl · keyword-search.jsonl · tracked-threads.jsonl
    |      virality.jsonl · trends-report.md
    |
    +--> data/harvests/reddit-compass-YYYY-MM-DD.md   ночной разбор
```

## 3. Границы (переносимость)

| Область | Где | При переносе |
|---|---|---|
| Код сервиса | `src/reddit_compass/` | переносится целиком |
| Профили | `config/profiles/*.json` | переносятся |
| Данные | `data/` (JSONL + MD) | монтируются как volume |
| Docker | `Dockerfile`, `docker-compose.yml` | переносятся |
| VPS-стек | `deploy/hostkey/` | app-owned стек `/opt/reddit-compass` |

## 4. Контракты данных

Стабильные dataclasses в `src/reddit_compass/models.py` (формат JSONL не менять):

- **PostCard** — карточка поста: `subreddit, post_id, title, author, created_utc, score,
  upvote_ratio, num_comments, url, permalink, selftext, link_flair_text, is_self, monitoring_type
  (hot|top|search), snapshot_date, keyword?, top_comments[CommentCard], crosspost_parents[str],
  is_video, over_18, stickied`. `PostCard.from_dict()` восстанавливает `top_comments` в CommentCard
  при чтении JSONL.
- **CommentCard** — `comment_id, author, score, body, created_utc?, is_submitter`.
- **TrackedThreadState** — `url, post_id, subreddit, title, score, num_comments, last_checked,
  new_comments_since_last, score_delta`.
- **ViralitySignal** — `post_id, title, original_subreddit, crossposted_to[str], total_score,
  total_comments, signal_type (crosspost|score_surge|multi_subreddit), detected_at, url`.

## 5. Движки сбора

| Движок | Что даёт | Зависимости |
|---|---|---|
| **Playwright** (основной) | hot, top, rising, search, score, комментарии | playwright, chromium |
| **RSS** (fallback) | только hot, без score/комментариев | aiohttp |

Playwright открывает headless Chromium и делает `fetch()` к Reddit JSON API из контекста браузера —
полные данные без API credentials. OAuth (asyncpraw) — опциональный движок на будущее (см. ROADMAP).

## 6. Rate limiting и этика

- Пауза между запросами: 4 c (Playwright), 15 c (RSS).
- Retry при HTTP 429: до 2 раз с паузой 10 c.
- Read-only: сервис не публикует, не голосует, не комментирует.
- Данные: только публичные посты и комментарии.

## 7. Перенос / деплой

```bash
# Локально
docker compose run --rm reddit-compass all

# VPS (HostKey «Hermes»): app-owned стек, host-cron nightly — см. deploy/hostkey/README.md
```

При переносе `src/`, `config/`, `Dockerfile`, `docker-compose.yml` переносятся; `data/` —
монтируется как volume.
