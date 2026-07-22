# ROADMAP

reddit-compass растёт от автономного коллектора трендов к «навигатору сигналов». Фазы независимы;
порядок — ориентир, не жёсткая последовательность.

## v0.1 — ядро (готово)

- Выделение в отдельный репозиторий, отвязка от монорепо, config-driven профили.
- Движок Playwright JSON API + RSS fallback.
- Сбор: `fetch / search / track / virality / report / all / nightly`.
- Детекция виральности (crosspost / score_surge / multi_subreddit).
- Ночной разбор трендов (config-driven, по кластерам профиля).
- Обвязка: uv, ruff, mypy (strict), pytest, pre-commit, CI; Docker; скелет VPS-деплоя.

## Phase 2 — планировщик на VPS (HostKey «Hermes»)

- App-owned compose-стек `/opt/reddit-compass`, изолированный от прочих стеков (своя сеть + volume).
- Batch-job: без публичного порта; resource limits, `no-new-privileges`, ротация логов.
- Расписание — host-cron: `docker compose run --rm reddit-compass nightly`.
- Регистрация владельца/контейнера/volume/backup в `vps_management`.
- Скелет: [deploy/hostkey/](deploy/hostkey/). Включение — по подтверждению, со сверкой живого HostKey.

## Phase 3 — LLM-анализ сигналов (`signals.py`)

- Проход Claude по постам snapshot → извлечение: pain points, buying intent, competitor mentions,
  lead-score (фича, вдохновлённая Reddit_Scrapper ⭐198 — «GPT-анализ маркетинговых болей»).
- Вход: `posts.jsonl`; выход: `signals.jsonl` + секция в отчёте.
- Anthropic SDK; модель — последняя (Haiku 4.5 для bulk-классификации, Sonnet 5 для синтеза), ID
  сверять через актуальную документацию. Ключ — `ANTHROPIC_API_KEY`.
- Заодно: сделать `trends_analysis.py` полностью profile-driven (редакторские подсказки — в профиль).

## Phase 4 — уведомления

- Дайджест топ-сигналов в Telegram/email после nightly-прогона.
- Переиспользовать telegram-скрипты и email-паттерны из соседних репозиториев.

## Phase 5 — веб-дашборд

- Read-only просмотр отчётов и сигналов в editorial-стиле (спокойный, статусный).
- Тогда к batch-стеку добавляется веб-контейнер: loopback-порт + внешний Caddy SNI `:443`.

## Технический долг

- Поднять порог покрытия тестами (сейчас гейт 60%, реально ~75%; сеть/браузер/оркестрация вне гейта).
- Опциональный движок OAuth (asyncpraw) как альтернатива Playwright для 100% ToS-чистоты.
