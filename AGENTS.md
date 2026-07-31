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
- Clustering/trendwatching-алгоритмы развивать через `trend_engine.db` и полные immutable
  Data Releases: сначала 50/100/300 локальных items, затем Golden Set и tests, затем full/shadow.
  `compass.db` для Engine всегда read-only. Не запускать full production rebuild для обычной
  итерации и не мутировать legacy `stories/story_metrics` из Engine-команд.
- Radar читает только `RadarPublication`. Production-каналы `broad`/`ai-native` публиковать
  вручную после quality gates; rollback только переключает immutable pointer.
- Канонический workflow и контракты: `docs/TREND_ENGINE.md`.

## Локальная машина — основная площадка для экспериментов

Mac автора (Apple M5 Pro, 18 ядер, 64 ГБ RAM) существенно быстрее VPS. Тяжёлые прогоны
Engine — калибровку порогов, A/B story-релизов, эксперименты с эмбеддингами — делать
локально, а не на проде. Ориентиры: кэширование эмбеддингов `potion-base-8M` для 5 000
items — ~10 секунд вместе с загрузкой модели; полный story-релиз на 7-дневном broad —
единицы минут.

- Полный стек ставится одной командой: `uv sync --extra embed` (model2vec, torch не нужен).
  `--extra engine` добавляет sentence-transformers и spaCy, они нужны только для E5.
- Работать на **копии** БД в скретче, а не на `data/*.db`: релизы иммутабельны, но
  эксперимент засоряет ledger. Прод-снимок — `data/prod_compass_snapshot.db`.
- Сеть при этом не нужна: `collect --from-snapshots` собирает raw-run из уже собранных JSONL.
  Исключение — первая загрузка модели с HuggingFace.
- На VPS выносить только то, что нельзя проверить локально (реальный ночной cron, публикация).

## Защита секретов (обязательно)

- **Pre-commit scan:** `detect-secrets` + `detect-private-key` — каждый коммит проходит проверку.
  Не обходить (`--no-verify` запрещено).
- **Gitignore:** `.env`, `.env.*`, `deploy/**/.env*`, `*.pem`, `id_rsa*` — НЕ попадают в git.
- **Перед пушем:** запустить `scripts/secret-scan --all` и убедиться что `git diff --cached` не содержит паттернов:
  `sk-`, `token=`, `password=`, `secret=`, `Bearer `, приватных ключей.
- **Ключи API** (DASHSCOPE, TELEGRAM, RC_API_SECRET) — только в `.env.secrets` (gitignored)
  и на VPS. Никогда в коде, доках, тестах, логах.
- **При добавлении нового секрета:** обновить `.env.example` (шаблон без значения) +
  `.secrets.baseline` (если detect-secrets ругается на ложное срабатывание).
- **При компрометации:** немедленно ротировать ключ, `git filter-branch` / BFG для удаления
  из истории.

## Git

- Только явный staging. Никаких `git add .` / `git add -A` / `git commit -a`.
- Не коммитить и не пушить без явного разрешения автора в текущей сессии.

## Деплой (разрешено автором)

- VPS: `${RC_DEPLOY_USER}@${RC_DEPLOY_HOST}` из gitignored `deploy/hostkey/.env.secrets`
  или SSH alias `reddit-compass-vps`; каталог `/opt/reddit-compass/`.
- Деплой через `deploy/hostkey/deploy.sh` (scp + docker compose up).
- Секреты: `deploy/hostkey/.env.secrets` (gitignored, НЕ коммитить).
- Разрешено: ssh/scp на VPS, docker compose up/down/restart, host-cron.
- Запрещено: трогать другие стеки на VPS (/opt/stealth, /opt/moex-futoi, /opt/cheap-intelligence).
