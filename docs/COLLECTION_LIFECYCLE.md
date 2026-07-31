# Жизненный цикл данных: от источника до опубликованного Radar

> Операционный контракт для разработчиков, владельца продукта и следующих LLM-сессий.
> Канонические алгоритмические правила Engine — в
> [`TREND_ENGINE.md`](TREND_ENGINE.md); визуальный поток — в
> [`DATA_FLOW_DIAGRAMS.md`](DATA_FLOW_DIAGRAMS.md).

## Короткий ответ: когда данные действительно готовы

Слова «run завершён» недостаточно. В сервисе есть два независимых понятия:

1. **Collection complete** — все *запрошенные* адаптеры записали и успешно отдали
   snapshot-артефакты; raw-факты зафиксированы в `compass.db`.
2. **Broad Radar published** — complete Data Release прошёл stories, trends, quality gate
   и вручную опубликован в production-канал `broad`.

Первое не зависит от Qwen. Второе зависит от качества, но никогда не переписывает сырые данные.
Если Qwen недоступен, collection всё равно может быть complete; Engine создаёт
детерминированный preview/shadow и честно показывает, что reviewed слой отсутствует.

```text
                         Collector boundary                         Engine boundary

Mac Reddit ───────┐
                  │  snapshots/YYYY-MM-DD/*.jsonl
VPS RSS / HN / ───┼──────────────┐
Ladder / PH       │              │
                  ▼              ▼
                 [1] finalizer → compass.db → [2] Data Release → [3] Facets
                                            read-only                 │
                                                                      ▼
                     Today / Radar ← publication ← quality ← trends ← stories
                           ▲                            [7]      [6]      [4–5]
                           └──────── immutable pointer only ───────────────┘
```

## 1. Роли машин и ответственность

| Место | Что делает | Что **не** делает |
|---|---|---|
| Mac (residential IP) | Берёт публичный Reddit и передаёт `posts.jsonl` в Docker volume VPS | Не копирует локальный `compass.db`; не строит stories/trends |
| VPS adapters | Пишут `rss.jsonl`, `hackernews.jsonl`, `ladder.jsonl`, `producthunt.jsonl` | Не публикуют Radar |
| Collector finalizer | Проверяет артефакты, нормализует их и фиксирует raw run в `compass.db` | Не вызывает LLM и не меняет `trend_engine.db` |
| Trend Engine | Берёт read-only снимок raw run, строит версии facets/stories/trends | Не запускает source adapters и не изменяет `compass.db` |
| Quality + publish | Допускает готовую версию и атомарно меняет channel pointer | Не копирует и не удаляет старые версии |
| FastAPI UI | Читает только текущую publication или явный preview | Не управляет сбором и не скрывает partial input |

`rc-api` — намеренно лёгкий read-serving контейнер (512 MB, без Playwright и embedding extra).
Команды `engine cycle`, Qwen review и embedding materialization всегда запускаются через
`docker compose run --rm reddit-compass …`: этот collector runtime имеет 1 GB и зависимости
`.[embed]`. Запуск Engine через `docker exec rc-api` не является supported workflow и может
перейти на fallback либо быть остановлен лимитом памяти.

Это разделение нужно, чтобы алгоритм кластеризации можно было безопасно менять на старой
базе: новая гипотеза создаёт новый `StoryRelease`, а не full rebuild и не потерю исходников.

## 2. Raw collection: артефакты и единый run

### 2.1. Ожидаемые артефакты

Для профиля `broad` finalizer ожидает выбранный набор, обычно все пять файлов:

```text
snapshots/YYYY-MM-DD/
├── posts.jsonl          # reddit
├── hackernews.jsonl     # hn
├── rss.jsonl            # RSS provider sections
├── ladder.jsonl         # optional/paywalled sources that were actually requested
└── producthunt.jsonl    # product pulse
```

В каждом item сохраняются provider, source cluster/section, canonical URL, а для Reddit ещё
`discussion_url` и `target_url`. Именно `target_url` позволяет связать обсуждение Reddit с
оригинальной статьёй, не принимая комментарии за самостоятельную новость.

### 2.2. Handoff Mac → VPS

`scripts/fetch-and-sync.sh` отвечает только за Reddit. Он передаёт `posts.jsonl` через временный
файл в `/data/snapshots/YYYY-MM-DD/` внутри Docker volume. Он **никогда не передаёт**
`compass.db`: иначе локальная неполная копия могла бы затереть собранные на VPS RSS/HN/Ladder/PH.

После завершения VPS-адаптеров host-cron запускает один finalizer:

```bash
reddit-compass collect \
  --from-snapshots \
  --profile broad \
  --sources reddit,hn,rss,ladder,ph \
  --date YYYY-MM-DD
```

Команда не обращается к Reddit, Qwen, RSS или сети. Она читает только уже записанные JSONL,
создаёт/обновляет один `runs.run_id = YYYY-MM-DD:broad`, `items`, `observations` и
`source_health` в одной локальной транзакции.

### 2.3. Статусы raw collection

| Состояние | Значение |
|---|---|
| `running` | адаптеры или finalizer ещё не завершены; Engine такой run не берёт |
| `complete` | у всех выбранных артефактов есть валидный JSONL; zero items отображается отдельно как `empty` |
| `partial` | хотя бы один выбранный файл отсутствует или не читается; input пригоден только для inspect/preview/shadow |
| source `ok` / `empty` | адаптер выполнился; `empty` не равен падению, но может стать `degraded` в Data Release при expected-min |
| source `error` / `not_configured` / `skipped` | факт отсутствия или ошибки, не маскируется зелёным статусом |

`source_health` хранится и для adapter-level (`rss`) и для `provider:section`
(`reuters:business`). Поэтому Run page может показать и «RSS адаптер завершён», и «какая именно
секция Reuters дала материалы».

## 3. Как raw run превращается в immutable analysis

```text
compass.db (raw only, Engine opens mode=ro + query_only)
  runs + items + observations + source_health
                   │
                   │ single snapshot transaction + deterministic checksum
                   ▼
trend_engine.db
  DataRelease (finalized, immutable triggers)
      └─ FacetRelease       domains/themes/entities/event frames
          └─ StoryRelease   candidate pairs → deterministic score → bounded Qwen review
              └─ TrendRelease   patterns across distinct stories only
                  └─ RadarPublication → published_channels[channel]
```

### 3.1. Data Release

Data Release — не ссылка на живую БД, а полная frozen-копия rows, observations и source health.
У неё есть checksum, code/config metadata и `input_status`. Изменение `compass.db` после её
создания не меняет результат эксперимента. SQLite-триггеры запрещают менять finalized rows.

Если expected source cluster пуст или raw run partial, release имеет `input_status=partial`.
Такой release можно отлаживать, но его нельзя публиковать в `broad` или `ai-native` даже с
`--allow-partial`; исключение допустимо лишь в непроизводственном `shadow`.

### 3.2. Stories и Qwen

Stories строятся из URL, title/BM25, entities, времени, чисел и локальных embedding candidates.
Сначала применяются детерминированные hard-conflicts и provenance anchors. Qwen получает только
серую зону (ограниченная порция pairs), с `temperature=0`, Pydantic JSON и evidence item IDs.

Candidate retrieval не разворачивает полный `N×N`: dense layer сохраняет top-K соседей на item,
а sparse token/entity buckets ограничены 32 документами и общим budget 100 000 pairs на release.
Большой bucket `OpenAI`/`AI`/`US` сам по себе не создаёт сотни тысяч сравнений и не является
доказательством одного события. URL и near-duplicate anchors обрабатываются первыми; если budget
исчерпан, детерминированно остаются наиболее узкие buckets.

```text
provisional StoryRelease
    │   auto labels + Qwen pair answers
    ▼
reviewed StoryRelease      # новый immutable attempt в том же cycle
```

Таким образом валидный Qwen-ответ влияет на **тот же** ночной cycle, а не остаётся декоративной
разметкой до следующего дня. Invalid JSON, неизвестные evidence IDs и сетевые ошибки сохраняются
как диагностика, но не превращаются в merge.

### 3.3. Trends и качество

Trend Engine получает только concrete stories. Минимум — три разные stories, две даты,
повторяющийся не-entity паттерн и evidence story IDs. Bounded trend review Qwen переводит
кандидат в confirmed/rejected; после валидных ответов TrendRelease materialize заново.

Quality gate считает overmerge, coverage, разнообразие рубрик, имена трендов, Pulse `other` и
регрессии относительно baseline. Публикация не запускается при failed floor, partial input или
отсутствии production gate. Последняя хорошая publication продолжает обслуживать GUI.

## 4. Run journal в интерфейсе

`/runs` — не дашборд трендов, а раскрываемый журнал операций. Каждая строка раскрывается и
показывает по одному и тому же `run_id` шесть стадий:

```text
1. Сбор источников          фактические adapter/source-health статусы и item count
2. Frozen Data Release      release ID, checksum-backed input, complete/partial
3. Stories                  StoryRelease, stories, cross-source count
4. Trends / Qwen            TrendRelease, candidates/confirmed и history status
5. Quality gate             passed или конкретные непрошедшие floors
6. Publication              channel, current pointer и input status
```

Раскрытие не является кнопкой «запустить»: web UI намеренно read-only. Оно объясняет, почему
Today/Radar показывает предыдущую версию, preview или недостаточную историю. На коротком Today
технические стадии не дублируются: там только свежий reading queue и изменения опубликованного
выпуска.

### 4.1. Что должно быть видно в Today

Today не ждёт JavaScript, чтобы стать полезным: первые десять материалов reading queue рендерятся
сервером из опубликованного immutable release. Браузер догружает только следующие десять. Поэтому
сбой сети, расширения или кэш старого статического файла не должен оставлять пользователя на
вечном «Подбираю…». Если API не отвечает, уже показанные ссылки не заменяются ошибкой.

Верхний блок «Что изменилось» строже: он показывает лишь `confirmed` trend с допустимым именем.
Непроверенный embedding-кандидат, сырой token bag или тренд с недостаточной историей остаётся в
`/trends` и `/engine`, но не выдаётся за редакционный вывод. Когда таких трендов нет, Today прямо
сообщает об этом и предлагает reading queue; это нормальное промежуточное состояние, а не
«пустой сбор».

## 5. Каноническое nightly расписание

```text
00:17 UTC  Mac launchd: public Reddit fetch → posts.jsonl → Docker volume VPS
14:00 UTC  VPS RSS snapshot
14:10 UTC  VPS Hacker News snapshot
14:20 UTC  VPS Ladder snapshot
14:30 UTC  VPS ProductHunt snapshot
14:45 UTC  collect --from-snapshots: один raw broad run, без сети и LLM
16:00 UTC  engine cycle: frozen release → stories → bounded Qwen → trends → quality → shadow
manual      inspect `/runs`/`/engine`, then publish complete gated version to `broad`
```

Время Mac и VPS — пример; настоящая host-cron конфигурация живёт рядом с deploy runbook.
Если `posts.jsonl` не пришёл к finalizer, raw run становится `partial`, Engine может дать preview,
но Broad не меняется. Это безопаснее, чем «зелёный» выпуск без voices.

## 6. Работа на старых данных без сети

Нельзя и не нужно запускать source adapters, чтобы исследовать clustering:

```bash
# Только локальные SQLite + JSONL миграции/backfill; сети нет.
reddit-compass db repair --source-db data/compass.db --output-dir data/snapshots

# Новый immutable input из существующих finalized runs.
reddit-compass engine release create --run 2026-07-27:broad --source-db data/compass.db
reddit-compass engine release verify --release RELEASE_ID
reddit-compass engine facets --release RELEASE_ID --profile broad
reddit-compass engine stories candidates --facet-release FACET_ID --limit 50 --output candidates.jsonl
reddit-compass engine stories propose --facet-release FACET_ID --limit 50
reddit-compass engine trends propose --story-release STORY_ID --window 30d
```

Порядок обязательный: synthetic fixtures → 50 → 100 → 300 local items → Golden Set → shadow.
`db rebuild` оставлен только для legacy recovery и не является способом проверить новый
clustering/trend algorithm.

## 7. Проверка перед production publication

```text
□ raw run complete; все expected source adapters видны в /runs
□ Data Release finalized, checksum verify прошёл, input_status=complete
□ Story/Trend releases evaluated; Qwen diagnostics видны, но invalid reviews не стали merges
□ quality report/check passed; no regression against baseline
□ history is sufficient for lifecycle claims (7 releases; 30 дней для meta-trends)
□ selected publication inspected in shadow / Engine
□ operator publishes Broad manually; rollback pointer known
```

Rollback всегда безопасен: `reddit-compass engine rollback --channel broad --to PUBLICATION_ID`
меняет только current pointer. Никаких raw таблиц, Data Releases или старых публикаций при этом
не удаляется.
