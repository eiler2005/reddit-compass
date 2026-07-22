# Claude Code — инструкции

`AGENTS.md` — общий источник истины по проекту. Следуй ему вместе с правилами ниже.

## Стиль работы

- Проговаривай значимые допущения до реализации.
- Предпочитай минимальную реализацию, удовлетворяющую принятому поведению; правки — хирургические,
  не рефактори несвязанный код.
- Превращай запросы в проверяемые результаты и прогоняй релевантные quality-гейты.

## Роль репозитория

**Трендовый радар**: автономный, config-driven сервис сбора данных из 21 источника (Reddit,
Hacker News, RSS-СМИ, Ladder-paywall, ProductHunt) с LLM-анализом (Qwen API) и REST API
(FastAPI + OAuth2). 5 кластеров источников, единая схема PostCard, SQLite-история,
ночная автоматизация (Mac → VPS sync).

## Ключевые модули

- `src/reddit_compass/client.py` — RedditEngine (aiohttp → Playwright → RSS)
- `src/reddit_compass/sources/` — адаптеры: rss, ladder, hackernews, producthunt
- `src/reddit_compass/signals.py` — LLM-анализ (Qwen/DashScope)
- `src/reddit_compass/api/` — FastAPI REST API (OAuth2, JWT)
- `src/reddit_compass/db.py` — SQLite хранилище
- `src/reddit_compass/notify.py` — заготовки уведомлений (без отправки)
- `deploy/hostkey/` — VPS деплой (Docker + Caddy)
- `scripts/` — nightly automation (fetch-and-sync, launchd)

## Безопасность и соответствие

- Не читать, не печатать, не коммитить `.env`, токены и ключи.
- `detect-secrets` + `detect-private-key` — каждый коммит. `--no-verify` запрещён.
- Reddit — только read-only и публичный контент; rate limits и retry соблюдать (см. `AGENTS.md`).
- Proxy — только для снижения 429, не для обхода банов.
- Собранный контент не использовать для обучения ML.

## Quality gate

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src    # strict
uv run pytest      # 60% coverage minimum
```

## Git

- Явный staging; `git add -A` и `git commit -a` запрещены.
- Не коммитить/не пушить без явного разрешения в текущей сессии.
- Conventional commits: feat/fix/docs/refactor/test/chore.
