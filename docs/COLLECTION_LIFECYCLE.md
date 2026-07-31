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
| Local approved route | Берёт публичный Reddit и передаёт `posts.jsonl` в Docker volume VPS | Не копирует локальный `compass.db`; не строит stories/trends |
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
`engine cycle --publish-channel shadow --allow-partial` делает такую preview-публикацию
автоматически только после успешного quality gate. Без явного `--allow-partial` partial input
остаётся без publication; production-каналы блокируются в обоих случаях.

Health может содержать aggregate provider-row и section-rows одного провайдера. Например,
`reddit=0` не делает release partial, если в том же frozen window есть успешные
`reddit:<subreddit>` rows с материалами: это агрегатный reporting artifact, а не отсутствие
voice coverage. Настоящий пустой provider без успешной section-строки остаётся `partial`.

### 3.2. Stories и Qwen

Stories строятся из URL, title/BM25, entities, времени, чисел и локальных embedding candidates.
Сначала применяются детерминированные hard-conflicts и provenance anchors. Qwen получает только
серую зону (ограниченная порция pairs), с `temperature=0`, Pydantic JSON и evidence item IDs.
Pair-review и trend-review имеют независимые limits: запуск только top trend reviews не требует
повторять pair-review и всё равно создаёт Qwen runner.

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
как диагностика, но не превращаются в merge. Один Engine-review ограничен 75 секундами: timeout
сохраняется как ошибка attempt и не может зависшим запросом задержать quality/publication stage.
Этот budget применяется и к `aiohttp` connect/read timeout, а не только к coroutine: закрытый
провайдером TCP-сокет не должен удерживать Engine за пределами лимита.
Команда `engine trends review` продолжает следующую bounded-порцию после timeout/error и выводит
список неуспешных target IDs; временная ошибка не кэшируется как LLM-решение, поэтому кандидат
можно честно повторить в следующем review attempt.

### 3.3. Trends и качество

Trend Engine получает только concrete stories. Минимум — три разные stories, две даты,
повторяющийся не-entity паттерн и evidence story IDs. Bounded trend review Qwen переводит
кандидат в confirmed/rejected; после валидных ответов TrendRelease materialize заново.

Quality gate считает overmerge, coverage, разнообразие рубрик, имена трендов, Pulse `other` и
регрессии относительно baseline. Публикация не запускается при failed floor, partial input или
отсутствии production gate. Последняя хорошая publication продолжает обслуживать GUI.

Результат этой проверки сохраняется в `trend_engine.db.engine_quality_reports` с ключом
`DataRelease + StoryRelease + TrendRelease`. Это часть versioned Engine, а не кэш живой страницы:
повторная проверка той же тройки обновляет её audit-запись, а другой Story/Trend attempt получает
свою. Поэтому `/runs` только читает уже записанный outcome и не пересчитывает всю taxonomy для
каждого исторического run во время HTTP-запроса. У старого release может не быть этой записи:
интерфейс показывает «результат ещё не записан для этой версии», а не подменяет отсутствие
проверки зелёным статусом.

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

Строка запуска раскрывается нативным `<details>` и показывает source-level health, IDs
immutable версий и шесть стадий. Цвет — лишь дополнительный сигнал: текст стадии всегда
различает `passed`, `failed`, `pending`, `partial` и `published`. Раскрытие полностью
read-only: оно не запускает адаптеры, Qwen или publication.

Один raw `run_id` может породить несколько immutable попыток. В журнале выбирается не просто
самая новая строка Data Release: приоритет имеет current `RadarPublication`, затем наиболее
полная цепочка `Trends → Stories → Facets`. Так новый facet-only experiment не скрывает
проверенный shadow/production выпуск и его quality outcome.

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

## 5. Каноническое расписание (раз в 2 ночи)

Pipeline запускается **каждые 2 ночи** (нечётные дни месяца). Сбор данных дёшев,
но Engine cycle на VPS с 1 CPU занимает 30-60 минут — ежедневный прогон избыточен
при 7-дневном окне анализа.

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

Cron-выражение: `*/2` в day-of-month (1, 3, 5, …, 29, 31). Настоящая host-cron
конфигурация: `deploy/hostkey/reddit-compass.cron`.

Если `posts.jsonl` не пришёл к finalizer, raw run становится `partial`, Engine может дать preview,
но Broad не меняется. Это безопаснее, чем «зелёный» выпуск без voices.

### 5.1 Полный цикл: от сбора до GUI

Каждый запуск проходит 10 последовательных этапов. Время указано для двух конфигураций:
**Mac** (Apple M5 Pro, 18 ядер, 64 GB) и **VPS** (1 CPU, 1 GB RAM, residential proxy).

| # | Этап | Команда / процесс | Mac | VPS | Что происходит |
|---|------|-------------------|-----|-----|----------------|
| 1 | **Reddit fetch** | `fetch --stealth` (Mac launchd) | ~12 мин | — | Playwright + residential proxy, 19 сабреддитов, jitter 3-6с/запрос |
| 2 | **СМИ snapshots** | `rss`, `hn`, `ladder`, `ph` (VPS cron) | — | ~5 мин | 4 адаптера последовательно: RSS-ленты, HN Algolia, Ladder proxy, ProductHunt GraphQL |
| 3 | **Raw run** | `collect --from-snapshots` | ~10с | ~30с | JSONL → `compass.db`, без сети и LLM. Один factual run из всех snapshot-артефактов |
| 4 | **DataRelease + Facets** | `engine cycle` (внутри) | ~5с | ~15с | Immutable snapshot корпуса + детерминированные facets (домены, сущности, токены) |
| 5 | **Embedding cache** | `cache_release_embeddings` | ~10с | ~30-60с | model2vec `potion-base-8M`: загрузка модели + encode 5000 items. Кэш переиспользуется между релизами |
| 6 | **Story clustering** | `create_story_release` | ~1-2 мин | ~5-10 мин | URL/entity/dense top-K retrieval → constrained agglomeration → stable story IDs |
| 7 | **Auto-label + Qwen review** | `auto_label` + bounded Qwen | ~2-3 мин | ~10-20 мин | Авто-разметка серой зоны + до 80 пар на Qwen review (75с timeout/пара, async) |
| 8 | **Trend discovery** | `create_trend_release` | ~30с | ~2-5 мин | `embedding_v2`: кластеризация векторов историй, c-TF-IDF имена, дедуп, производная по дням |
| 9 | **Quality gate** | `compute_quality` + `evaluate_floors` | ~5с | ~10с | 12 полов (overmerge, полнота, таксономия, тренды, Pulse) + регрессии vs baseline |
| 10 | **Publication** | `publish_radar` (shadow) | ~1с | ~1с | Immutable pointer switch. GUI (`/today`, `/trends`, `/radar`) читает только published pointer |
| | **Итого engine cycle** | | **~5-8 мин** | **~30-60 мин** | |
| | **Итого весь pipeline** | | **~20 мин** | **~40-90 мин** | |

После этапа 10 GUI обновляется автоматически — все страницы (`/today`, `/news`, `/trends`,
`/pulse`, `/radar`) читают immutable publication pointer. Ручная публикация в `broad`
требует инспекции shadow-версии и явного `engine publish --channel broad`.

**Почему VPS медленнее в 5-10 раз:** 1 CPU против 18 ядер, 1 GB RAM против 64 GB,
сетевые вызовы через residential proxy (latency + ретраи на 429). Qwen review —
основной bottleneck на обеих платформах (зависит от API latency, не от CPU).

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
