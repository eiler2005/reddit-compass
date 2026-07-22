# reddit-compass — Implementation Plan (для исполняющей LLM)

> **⚠️ СТАТУС: v0.1 УЖЕ РЕАЛИЗОВАН (2026-07-22).** Все шаги Task 0→7 ниже **выполнены** и
> проверены (ruff / mypy strict / pytest ~75% — зелёные); репозиторий собран. Текущее состояние —
> в [../CHANGELOG.md](../CHANGELOG.md) и [../README.md](../README.md). Этот документ сохранён как
> (1) спецификация/запись того, как v0.1 был собран (для аудита или пересборки с нуля) и
> (2) образец детализации для **оставшейся** работы — фаз 2–5 из [../ROADMAP.md](../ROADMAP.md).
> Разделы ниже намеренно оставлены в исходном «императивном» виде (Task 0→N).

> **Как пользоваться этим документом.** Это самодостаточная пошаговая спецификация для сборки
> проекта `reddit-compass` в отдельном репозитории. Исполняющая LLM НЕ имеет контекста монорепо и
> qwen-сессии — все нужные пути, шаблоны файлов и точечные правки приведены здесь. Выполняй задачи
> **по порядку** (Task 0 → N), после каждой прогоняй её *Acceptance*. Первое действие реализации —
> положить этот документ в репо как `docs/IMPLEMENTATION_PLAN.md`.

---

## 0. Context и цель

В монорепо книги уже есть **рабочий** сервис сбора Reddit-контента
`/Users/DenisErmilov/AiNativeBook_Draft_26/services/reddit-monitor` (Playwright JSON API + RSS
fallback, детекция виральности, ночной разбор трендов). Его нужно **выделить в самостоятельный
продукт `reddit-compass`** 🧭 («компас по трендам Reddit — показывает, куда смотреть»; имя выбрано
и проверено на GitHub как свободное), отвязать от книги, обернуть в профессиональную обвязку по
образцу эталонного репо, упаковать в Docker и заложить дорожную карту.

- **Целевой репозиторий:** https://github.com/eiler2005/reddit-compass (создан, пустой/почти пустой).
- **Рабочий каталог:** `/Users/DenisErmilov/aiprojects/reddit-compass`.
- **Definition of Done для v0.1:** `reddit-compass all` собирает snapshot и рендерит
  `trends-report.md` в собственные `data/`-каталоги, **ноль зависимостей от монорепо книги**; CI
  (ruff + mypy + pytest) зелёный; Docker-образ собирается и прогоняется.

## 1. Решения и жёсткие ограничения

1. **Reddit-движок:** оставить **Playwright** (headless Chromium → Reddit JSON API) как основной +
   RSS fallback. Код `client.py` переносится как есть. OAuth/asyncpraw — опциональный движок на
   будущее (Roadmap), НЕ в v0.1.
2. **Формат продукта:** **автономный навигатор** — полностью config-driven, свои `data/`-каталоги,
   никаких путей монорепо. Книга/дайджест/колонки — лишь потребители выходных файлов.
3. **Обвязка «be the best version, без фанатизма»:** брать из эталонного репо
   `/Users/DenisErmilov/aiprojects/reddit_trends` (uv + ruff + mypy + pytest + pre-commit + CI +
   AGENTS/CLAUDE/LEAN-CTX + CHANGELOG + .gitignore + .python-version). Внутренности кода
   (`argparse`, `dataclasses`) НЕ переписывать на typer/pydantic — они работают; это Roadmap-полировка.
4. **Этика/легальность (перенести в AGENTS.md, соблюдать в коде):** read-only — не постить, не
   голосовать, не комментировать; только публичные посты/комментарии; rate limit (пауза 4 c
   Playwright / 15 c RSS), retry на HTTP 429 (до 2 раз, пауза 10 c); стандартный браузерный
   User-Agent. Не добавлять proxy-ротацию/обход блокировок сверх текущего поведения.
5. **Вне скоупа:** копию `services/reddit-monitor` в монорепо книги НЕ трогать. Реальную правку
   VPS/Caddy/SNI не делать без отдельного подтверждения.

## 2. Источники (абсолютные пути; копировать/адаптировать)

| Что | Путь |
|---|---|
| **Код для переноса** (10 модулей) | `…/services/reddit-monitor/src/*.py` |
| Текущий конфиг | `…/services/reddit-monitor/config.json` |
| Docker-база | `…/services/reddit-monitor/Dockerfile`, `docker-compose.yml`, `requirements.txt` |
| Доки-исходники | `…/services/reddit-monitor/{ARCHITECTURE,README,PROJECT}.md` |
| **Эталон обвязки** | `/Users/DenisErmilov/aiprojects/reddit_trends/` → `pyproject.toml`, `.pre-commit-config.yaml`, `.gitignore`, `.github/workflows/ci.yml`, `.python-version`, `AGENTS.md`, `CLAUDE.md`, `LEAN-CTX.md` |
| **Эталон VPS-деплоя** | `…/services/digest-service/deploy/hermes.md` + `deploy/hermes-stack/docker-compose.yml` |

(`…` = `/Users/DenisErmilov/AiNativeBook_Draft_26`.)

## 3. Data-контракты (из `models.py`; сохраняются 1:1)

- **CommentCard** (`frozen`): `comment_id, author, score, body, created_utc?, is_submitter=False`.
- **PostCard**: `subreddit, post_id, title, author, created_utc?, score, upvote_ratio, num_comments,
  url, selftext, link_flair_text?, is_self, permalink, monitoring_type, snapshot_date, keyword?,
  top_comments[CommentCard], crosspost_parents[str], is_video, over_18, stickied`. Методы
  `to_dict/to_json`, свойство `full_url`. `monitoring_type ∈ {"hot","top","search"}`.
- **TrackedThreadState**: `url, post_id, subreddit, title, score, num_comments, last_checked,
  new_comments_since_last=0, score_delta=0`.
- **ViralitySignal**: `post_id, title, original_subreddit, crossposted_to[str], total_score,
  total_comments, signal_type∈{"crosspost","score_surge","multi_subreddit"}, detected_at, url`.

JSONL-выход и `trends-report.md` — стабильные контракты для потребителей; формат не менять.

## 4. Целевая структура репозитория

```
reddit-compass/
  pyproject.toml            # uv+hatchling; entry: reddit-compass = reddit_compass.cli:main
  uv.lock  .python-version  # 3.12
  .pre-commit-config.yaml  .gitignore  .dockerignore  .env.example
  .github/workflows/ci.yml  # ruff + ruff format --check + mypy src + pytest
  README.md  CHANGELOG.md  ARCHITECTURE.md  ROADMAP.md  AGENTS.md  CLAUDE.md  LEAN-CTX.md
  docs/IMPLEMENTATION_PLAN.md   # этот документ
  Dockerfile  docker-compose.yml
  config/profiles/
    ai-native.json          # = текущий config.json (дефолтный пример-профиль)
    starter.json            # нейтральный минимальный профиль
  src/reddit_compass/
    __init__.py  cli.py  config.py  models.py  client.py
    fetch_subreddits.py  search_keywords.py  track_threads.py
    detect_virality.py  export.py  trends_analysis.py
  tests/
    fixtures/               # сэмплы Reddit JSON listing/comments + Atom RSS
    test_client_parse.py  test_virality.py  test_report.py  test_config.py
  deploy/hostkey/           # Roadmap Phase 2 (скелет создаём в v0.1)
    docker-compose.yml  README.md
  data/.gitkeep             # snapshots/ harvests/ — git-ignored
```

---

## 5. Пошаговые задачи

### Task 0 — Инициализация репозитория
- В `/Users/DenisErmilov/aiprojects/reddit-compass`: `git ls-remote https://github.com/eiler2005/reddit-compass`.
  Если пусто → `git init`, `git remote add origin …`, `git branch -M main`. Если есть коммит
  (README/LICENSE) → `git clone` в отдельное место и слить, либо `git init` + `git pull origin main`.
- Скопировать этот файл в `docs/IMPLEMENTATION_PLAN.md`.
- **Acceptance:** каталог — git-репо с remote `origin` → eiler2005/reddit-compass; есть `docs/IMPLEMENTATION_PLAN.md`.

### Task 1 — Обвязка/тулинг (шаблоны ниже)
Создать `pyproject.toml`, `.python-version`, `.pre-commit-config.yaml`, `.gitignore`,
`.dockerignore`, `.github/workflows/ci.yml`, `.env.example`. Затем `uv sync --dev`,
`uv run pre-commit install`.

**`pyproject.toml`:**
```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "reddit-compass"
version = "0.1.0"
description = "Compass for Reddit trends — collects the voice of the street and points where to look"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
  "playwright>=1.40,<2",
  "aiohttp>=3.9,<4",
]

[project.scripts]
reddit-compass = "reddit_compass.cli:main"

[dependency-groups]
dev = [
  "mypy>=1.16,<2",
  "pre-commit>=4.2,<5",
  "pytest>=8.4,<9",
  "pytest-cov>=6.2,<7",
  "ruff>=0.12,<1",
]

[tool.hatch.build.targets.wheel]
packages = ["src/reddit_compass"]

[tool.ruff]
line-length = 100
target-version = "py312"
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = true
warn_unused_configs = true
files = ["src"]
[[tool.mypy.overrides]]
module = ["playwright.*", "aiohttp.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
addopts = "-q --strict-markers --cov=reddit_compass --cov-report=term-missing --cov-fail-under=60"
testpaths = ["tests"]

[tool.coverage.run]
branch = true
source = ["reddit_compass"]
# Сеть/браузер исключаем из порога на старте; поднимать по мере тестов.
omit = ["*/client.py", "*/fetch_subreddits.py", "*/search_keywords.py", "*/track_threads.py"]
```

- **`.python-version`:** `3.12`
- **`.pre-commit-config.yaml`** — копия из `reddit_trends/.pre-commit-config.yaml` (ruff-check --fix,
  ruff-format, check-json/toml/yaml, end-of-file-fixer, trailing-whitespace, detect-private-key).
- **`.gitignore`** — из `reddit_trends/.gitignore`; убедиться, что игнорируются `data/*`
  (кроме `data/.gitkeep`), `.env`, `.venv/`, `__pycache__/`, `.ruff_cache/`, `.mypy_cache/`,
  `.pytest_cache/`, `.DS_Store`.
- **`.github/workflows/ci.yml`** — копия из `reddit_trends`, но добавить установку браузера перед
  pytest, если тесты его требуют (для юнит-тестов на парсеры — НЕ требуется):
  ```yaml
  name: CI
  on: [push, pull_request]
  permissions: { contents: read }
  jobs:
    quality:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: astral-sh/setup-uv@v6
          with: { version: "0.11.6", enable-cache: true }
        - run: uv python install 3.12
        - run: uv sync --locked --dev
        - run: uv run ruff check .
        - run: uv run ruff format --check .
        - run: uv run mypy src
        - run: uv run pytest
  ```
- **`.env.example`:**
  ```env
  # Каталог данных (в контейнере монтируется volume).
  DATA_DIR=./data
  # Опц. переопределение каталога ночных разборов (по умолчанию $DATA_DIR/harvests).
  # HARVESTS_DIR=
  # Опц. путь к профилю (по умолчанию config/profiles/ai-native.json).
  # REDDIT_COMPASS_CONFIG=
  # --- Roadmap: OAuth-движок (asyncpraw), не нужен для Playwright ---
  REDDIT_CLIENT_ID=
  REDDIT_CLIENT_SECRET=
  REDDIT_USER_AGENT=reddit-compass:v0.1.0 (by /u/your_username)
  # --- Roadmap: LLM-анализ сигналов ---
  ANTHROPIC_API_KEY=
  ```
- **Acceptance:** `uv sync --dev` проходит; `uv run ruff check .` и `uv run pre-commit run -a` зелёные.

### Task 2 — Перенос пакета `src/reddit_compass/`
- Скопировать все 10 модулей из `…/reddit-monitor/src/` в `src/reddit_compass/`
  (`__init__.py, cli.py, config.py, models.py, client.py, fetch_subreddits.py, search_keywords.py,
  track_threads.py, detect_virality.py, export.py, trends_analysis.py`).
- **Глобальная замена** во всех модулях: логгер `getLogger("reddit_monitor")` → `"reddit_compass"`
  (встречается в 8 файлах: client, fetch_subreddits, export, cli, detect_virality, search_keywords,
  trends_analysis, track_threads).
- **`config.py` — заменить весь блок путей (строки ~10-15 оригинала)** на отвязанную версию:
  ```python
  import os
  from pathlib import Path

  PROJECT_ROOT = Path(__file__).resolve().parents[2]   # src/reddit_compass/config.py → корень репо

  def _dir(env: str, default: Path) -> Path:
      val = os.environ.get(env)
      return Path(val).expanduser() if val else default

  DEFAULT_DATA_DIR = _dir("DATA_DIR", PROJECT_ROOT / "data")
  DEFAULT_SNAPSHOTS_DIR = DEFAULT_DATA_DIR / "snapshots"
  DEFAULT_HARVESTS_DIR = _dir("HARVESTS_DIR", DEFAULT_DATA_DIR / "harvests")
  DEFAULT_CONFIG_PATH = _dir(
      "REDDIT_COMPASS_CONFIG", PROJECT_ROOT / "config" / "profiles" / "ai-native.json"
  )
  ```
  Удалить `SERVICE_ROOT`/`REPO_ROOT`. Остальной `config.py` (dataclasses `MonitorSettings`,
  `MonitorConfig`, `from_file`) — без изменений.
- **`cli.py`:** `prog="reddit-monitor"` → `prog="reddit-compass"`; строка `harvests_dir =
  DEFAULT_HARVESTS_DIR` остаётся (теперь указывает в `data/harvests/`); имя файла разбора
  `reddit-trends-<date>.md` можно оставить или переименовать в `reddit-compass-<date>.md`
  (переименование — обновить и в доках).
- `client.py, models.py, fetch/search/track, detect_virality.py, export.py, trends_analysis.py` —
  переносятся без правок логики.
- **Точка входа:** используем console-script `reddit-compass` (из pyproject). Старые
  `scripts/run.py`/`nightly_run.py` **не переносим** (их роль закрывает entry point:
  `reddit-compass all`, `reddit-compass nightly`).
- **Acceptance:** `uv run mypy src` зелёный; `uv run reddit-compass --help` показывает подкоманды
  `fetch/search/track/virality/report/all/nightly`; в коде нет ссылок на `research/`, `REPO_ROOT`,
  `reddit_monitor`.

### Task 3 — Профили конфигурации
- `config/profiles/ai-native.json` = **дословно** текущий `…/reddit-monitor/config.json` (кластеры
  ai_work_business / trust_authenticity / vibe_coding_agents / layoffs_labor, keywords,
  tracked_threads, settings).
- `config/profiles/starter.json` — минимальный нейтральный профиль (1-2 сабреддита, пустые keywords/
  tracked_threads, дефолтные settings) как шаблон для новых доменов.
- **Acceptance:** `uv run reddit-compass fetch --limit 3 --config config/profiles/starter.json`
  запускается без ошибок конфигурации.

### Task 4 — Тесты + фикстуры (детерминированное ядро)
Покрыть чистую логику без сети/браузера:
- `test_client_parse.py` — `parse_listing_json` (kind `t3`, поля PostCard), `parse_comments_json`
  (kind `t1`, фильтр `[removed]/[deleted]/stickied`, сортировка по score, limit), `parse_rss`
  (Atom-фид → RSSEntry). Фикстуры — сохранённые сэмплы в `tests/fixtures/`.
- `test_virality.py` — `detect_virality`: crosspost (≥`virality_crosspost_min`), score_surge
  (≥`virality_score_threshold`), multi_subreddit; проверка `signal_type` и агрегатов.
- `test_report.py` — `render_trends_report`: топ по score/обсуждаемости, секции по кластерам,
  корректный Markdown при пустых входах.
- `test_config.py` — `MonitorConfig.from_file` (парсинг профиля, дефолты settings, `all_subreddits`
  дедуп, `subreddit_clusters`); отвязка путей (env `DATA_DIR` переопределяет каталоги).
- **Acceptance:** `uv run pytest` зелёный, порог покрытия (60% по не-omit модулям) достигнут.

### Task 5 — Docker (локальный прогон)
- **`Dockerfile`** — адаптировать исходный (`python:3.12-slim` + системные libs для Chromium +
  `pip install` playwright/aiohttp + `playwright install chromium`). Изменения: копировать
  `src/reddit_compass` и `config/`; `CMD ["reddit-compass", "all"]` (пакет ставим `pip install .`
  или копируем и ставим). Пользователь `reddit`, `VOLUME ["/data"]`, `ENV DATA_DIR=/data`.
- **`docker-compose.yml`** — образ `reddit-compass:0.1.0`, `env_file: .env`, `volumes:
  reddit-compass-data:/data`. **Убрать** книжный bind-mount `../../research/...`. Добавить
  `restart: "no"` (batch), можно `profiles` для nightly.
- **`.dockerignore`** — `.venv`, `data`, `__pycache__`, `.git`, кэши, `tests`.
- **Acceptance:** `docker compose build` успешен; `docker compose run --rm reddit-compass all`
  пишет snapshot в volume.

### Task 6 — Документация
- **README.md** — обобщить (RU, можно + короткий EN-блок): что это (навигатор по трендам Reddit,
  без API-ключей через Playwright), быстрый старт (uv), CLI-таблица (из исходного README),
  профили, Docker, ссылка на ARCHITECTURE/ROADMAP. Убрать формулировки «для книги/дайджеста/колонок»
  из позиционирования (оставить как пример-кейс).
- **ARCHITECTURE.md** — адаптировать исходный: главный инвариант (собирает данные и артефакты;
  потребители читают файлы, не зависят от рантайма), движки (Playwright/RSS), контракты данных (§3),
  этика/rate-limit, сценарий Docker/переноса. Убрать книжные пути.
- **AGENTS.md / CLAUDE.md / LEAN-CTX.md** — по образцу `reddit_trends`; в AGENTS.md — engineering
  workflow (uv, ruff/mypy/pytest перед сдачей; explicit git staging; не коммитить без разрешения) +
  Reddit-этика (п.1.4).
- **CHANGELOG.md** — `0.1.0`: выделение из монорепо, отвязка, Playwright-движок, Docker.
- **ROADMAP.md** — см. §6.
- **Acceptance:** доки не ссылаются на монорепо/`research/`; README-команды соответствуют реальному CLI.

### Task 7 — Финальная верификация v0.1
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
uv run reddit-compass all          # реальный прогон (Playwright) → data/snapshots/<date>/
uv run reddit-compass nightly      # + data/harvests/reddit-compass-<date>.md
docker compose run --rm reddit-compass all
```
- **Acceptance = Definition of Done (§0).** Затем — коммит по явному разрешению пользователя
  (staging только явный, без `git add -A`).

---

## 6. ROADMAP.md (фазы после v0.1; заложить в документ, реализовывать поэтапно)

- **Phase 2 — Планировщик на VPS (HostKey «Hermes»).** app-owned compose-стек `/opt/reddit-compass`,
  **изолированный** от `/opt/stealth`, `/opt/moex-futoi`, `/opt/cheap-intelligence` (своя сеть+volume);
  **без публичного порта** (batch-job); resource limits, `no-new-privileges`, json-file логи с
  ротацией; расписание — **host-cron**: `docker compose run --rm reddit-compass nightly`; регистрация
  владельца/контейнера/volume/backup в `vps_management`. Скелет (`deploy/hostkey/docker-compose.yml`
  + `README.md`-runbook) создать уже в v0.1 по образцу `digest-service/deploy/hermes.md`. Правки
  живого VPS — только с подтверждения; Hetzner и `cheap-intelligence.vercel.app` не трогать.
- **Phase 3 — LLM-анализ сигналов (`signals.py`).** Проход Claude по постам snapshot → извлечение
  pain points, buying intent, competitor mentions, lead-score (фича из Reddit_Scrapper ⭐198, в
  qwen-сессии отмечена как самая близкая по смыслу). Anthropic SDK; модель — последняя (Haiku 4.5
  для bulk-классификации, Sonnet 5 для синтеза), точные ID сверить через skill `claude-api`. Вход —
  `posts.jsonl`, выход — `signals.jsonl` + секция в отчёте. Ключ из `ANTHROPIC_API_KEY`.
- **Phase 4 — Уведомления.** Дайджест топ-сигналов в Telegram/email после nightly (переиспользовать
  telegram-скрипты/email-паттерны из соседних репо пользователя).
- **Phase 5 — Веб-дашборд.** Read-only просмотр отчётов/сигналов в editorial-стиле digest-service
  (кобальт/чернила). Тогда добавляется loopback-порт + outer Caddy SNI `:443` (как у дайджеста) —
  и Phase-2 стек расширяется вторым контейнером.

## 7. Быстрый чек-лист приёмки v0.1
- [ ] Репо инициализирован, `docs/IMPLEMENTATION_PLAN.md` на месте.
- [ ] Обвязка из `reddit_trends`; `ruff/mypy/pytest` зелёные; pre-commit установлен.
- [ ] Пакет `reddit_compass` перенесён, логгер/prog переименованы, `config.py` отвязан.
- [ ] Профили `ai-native.json` (= старый config) + `starter.json`.
- [ ] Тесты на парсеры/виральность/отчёт/конфиг проходят.
- [ ] `Dockerfile`/`compose` без книжных путей; образ собирается и прогоняется.
- [ ] Доки обобщены, ROADMAP с 4 фазами, `deploy/hostkey/` скелет создан.
- [ ] Ни одной зависимости от `AiNativeBook_Draft_26`/`research/`.
