# reddit-compass — инструкции для агентов

@LEAN-CTX.md

## Начать отсюда

- Прочитать `README.md`, затем `ARCHITECTURE.md` (границы, контракты) и `ROADMAP.md`.
- Профили `config/profiles/*.json` — источник истины по тому, что собирать.
- Это автономный, config-driven сервис. Он ничего не импортирует из внешних проектов; связь с
  потребителями — только через файлы (JSONL + Markdown).

## Границы Reddit (соблюдать в коде)

- Основной движок — aiohttp + Reddit `.json` API (лёгкий HTTP), fallback — Playwright
  (headless Chromium), последний fallback — Atom RSS. Данные — только **публичные** посты
  и комментарии.
- **Read-only.** Сервис не постит, не голосует, не комментирует, не логинится под аккаунтом.
- Rate limit: пауза 4 c между запросами; retry на HTTP 429 — до 2 раз с паузой 10 c.
- **Proxy-ротация разрешена** исключительно для снижения HTTP 429 (rate limit). Запрещено:
  обход банов/блокировок аккаунтов, параллельные личности, имитация разных пользователей.
  Proxy — config-driven (`REDDIT_COMPASS_PROXIES` или поле в профиле); без proxy сервис
  работает как раньше.
- Не использовать собранный контент для обучения ML-моделей. Хранить минимум; примеры в тестах —
  синтетические и безопасные для публикации.

## Инженерный процесс

- Python 3.12 и `uv`.
- Перед сдачей: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`,
  `uv run pytest`.
- Тесты — на изменения поведения; не «чинить» тесты ослаблением проверок. Сеть/браузер не трогать
  из юнит-тестов — покрывать чистую логику фикстурами.
- Значимые изменения отражать в `README.md`, релевантных доках и `CHANGELOG.md`.
- Секреты (`.env`, токены, ключи) не читать, не печатать, не коммитить.

## Git

- Только явный staging. Никаких `git add .` / `git add -A` / `git commit -a`.
- Не коммитить и не пушить без явного разрешения автора в текущей сессии.

## Деплой (разрешено автором)

- VPS: `deploy@204.168.239.217` (HostKey «Hermes»), каталог `/opt/reddit-compass/`.
- Деплой через `deploy/hostkey/deploy.sh` (scp + docker compose up).
- Секреты: `deploy/hostkey/.env.secrets` (gitignored, НЕ коммитить).
- Разрешено: ssh/scp на VPS, docker compose up/down/restart, host-cron.
- Запрещено: трогать другие стеки на VPS (/opt/stealth, /opt/moex-futoi, /opt/cheap-intelligence).
