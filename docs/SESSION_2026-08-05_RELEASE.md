# Release handoff — 2026-08-05: непрерывные данные, gated Radar и честный Qwen API

> Снимок решений этой сессии для следующего ручного цикла. Не содержит адресов VPS,
> токенов, proxy URL или других секретов. Операционный порядок команд — в
> [`MANUAL_RELEASE_RUNBOOK.md`](MANUAL_RELEASE_RUNBOOK.md).

## Итог публикации

`broad` опубликован вручную только после checksum, quality floors и shadow inspection:

| сущность | ID / состояние |
|---|---|
| Data Release | `2026-07-30_2026-08-05-broad-r2`, `complete`, 10 927 materials |
| Story Release | `stories_d392050a16c5f412b814` |
| Trend Release | `trends_5a880292319845b46bf3` |
| Signal Release | `signals_aa72fd8d48188e23a6a5` |
| Shadow publication | `publication_83d05bcc93fa8db988ca` |
| Broad publication | `publication_5f42686a86628e14680a` |
| Broad rollback target | `publication_a3146901adce41bc6024` |

`engine release verify`, `engine quality report` и `engine quality check` прошли. В
отчёте: 9 449 stories, 878 multi-item, 500 cross-source, `overmerge_ge5=0`,
`overmerge_ge8=0`, 54 trends, 1 097 Pulse; failed floors и regressions отсутствуют.

## Фактический жизненный цикл данных

```text
per-source JSONL snapshots
        │
        ├── exact artifacts? ──► collect --recover-snapshots (без сети)
        └── artifact отсутствует ──► collect --historical-date (date-aware public APIs)
                                         │
                                         ▼
raw run + source_health in compass.db (complete only)
                                         │ read-only
                                         ▼
frozen Data Release → Facets → Stories → bounded Qwen → Trends → Pulse → quality
                                         │
                                         ▼
shadow pointer ──manual inspection──► broad pointer / rollback pointer
```

### Восстановление coverage

- Пропущенный 2026-08-04 восстановлен историческим public-data сбором; его raw run —
  `complete`.
- 2026-08-05 — `complete`: 3 077 observations (Reddit 2 081, HN 250, RSS 530,
  Ladder 186, Product Hunt 30), source health `ok`.
- Broad window 2026-07-30…2026-08-05 теперь содержит семь последовательных UTC-дней.
- `collect --coverage` находит gap по calendar/run/source-health/artifacts,
  `collect --recover-snapshots` финализирует только сохранённые date-specific JSONL,
  `collect --historical-date` не маскирует live items под прошлую дату. У Ladder нет
  надёжного historical listing, поэтому за прошлую дату он идёт не через listing, а через
  date-filtered Google News discovery с двойной защитой: провайдерский запрос
  `after:/before:` и локальная перепроверка `_published_on_date`.

> **Поправка 2026-08-06.** Здесь и в `CHANGELOG.md` было написано, что Ladder за прошлую
> дату остаётся explicit `empty`. Код так никогда не делал — `fetch_all_ladder` при
> `historical_date` уходит в `fetch_historical_ladder_google_news` и возвращает реальные
> карточки. Верным был `MANUAL_RELEASE_RUNBOOK.md`; текст выше приведён к коду.

## Изменения по архитектурным границам

### Collector: raw facts и воспроизводимое восстановление

- Collector по-прежнему пишет только raw facts в `compass.db`; Engine не запускает
  adapters и открывает эту БД исключительно read-only.
- Схема source health и recovery теперь отделяют: отсутствующий artifact, сохранённый
  artifact без finalizer, допустимый empty и транспортную ошибку.

> **Поправка 2026-08-06.** Последнее утверждение на момент релиза кодом не
> обеспечивалось: адаптеры HN/RSS/Ladder/Product Hunt ловили HTTP- и сетевые ошибки
> внутри себя и возвращали `[]`, поэтому отказ становился `empty`, run — `complete`, а
> `--coverage` не показывал gap. Исправлено `SourceTransportError` (см. ниже).
- Добавлены/укреплены adapters HN, RSS, Ladder и Product Hunt для date-aware recovery;
  recovery всегда создаёт обычный observable raw run, а не скрытую правку старого run.

### Trend Engine: точность важнее агрессивного склеивания

- В `trend_engine.db` все Data/Facet/Story/Trend/Signal attempts остаются immutable.
- Near-duplicate fingerprint и exact-title больше не auto-merge два материала одного
  provider без общего event URL. Для независимых sources syndicated headline и точный URL
  продолжают быть сильным доказательством.
- Production cycle использует cross-encoder и ограниченный review budget; некорректный
  либо timeout-ответ Qwen становится visible diagnostic и никогда не становится merge.
- Новая r2 попытка прошла ранее блокировавшие floors overmerge; pointer менялся лишь после
  отдельного quality report/check.

### Qwen: модель по задаче, API-цена без предположений

- `qwen3.7-flash` — default для bulk extraction, pair review и bounded trend review;
  `think=False` исключает ненужные reasoning tokens в JSON-задачах.
- `qwen3.8-max` сохранён для явно одобренного свободного сложного synthesis, а не для
  массовой классификации.
- Service routing использует только pay-as-you-go API. Token Plan остаётся отдельным
  интерактивным продуктом и не выбирается сервисом.
- Значение `RC_QWEN_PAYG_FREE_TOKENS` по умолчанию равно `0`: international API-грант
  нельзя считать бесплатным, пока владелец не подтвердит его в console. Локальный ledger
  считает usage, но не доказывает скидку.
- Для первого tier (input ≤32K) international list price: Flash ¥0.225/¥0.974 и Max
  ¥14.988/¥44.965 за 1M input/output. Актуальная промо-цена проверяется вручную в
  Model Studio console перед Max-задачей.

### Publication и UI: именно опубликованные evidence

- Cycle создал shadow только после quality gate; broad был переключён отдельной ручной
  командой. Rollback не удаляет релизы, а возвращает `published_channels["broad"]` на
  previous immutable pointer.
- News ранжируется по evidence/story strength и engagement, Stories — по source/item
  coverage, Trends — по confidence/source/story coverage, Pulse — по signal strength.
  При равенстве выше более свежий evidence.
- News, Stories, Trends, Today, Pulse, Radar, детали и Project Lens показывают
  `published_at` или `first_seen → last_seen`; API добавляет нужные strength/date fields.
  Сортировка и даты не вычисляются браузером из непубликованных raw rows.

### Operations и документы

- [`MANUAL_RELEASE_RUNBOOK.md`](MANUAL_RELEASE_RUNBOOK.md) теперь — одна операционная
  страница: статус, collection, gap recovery, engine cycle, quality, shadow/broad,
  rollback и live SQL diagnostics.
- [`QWEN_ROUTING.md`](QWEN_ROUTING.md) фиксирует model routing, API prices, гранты и
  ledger semantics; [`COLLECTION_LIFECYCLE.md`](COLLECTION_LIFECYCLE.md) описывает
  factual collection/recovery contract.
- Локальная VPS note хранится в `deploy/hostkey/VPS_ACCESS.local.md`; она gitignored и
  не добавляется в публичную историю.

## Проверки этого изменения

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Результат перед commit: ruff clean, mypy без ошибок, 704 passed / 1 skipped, coverage
79.17%. На VPS после deploy `/health` вернул `ok`; `qwen pick` выбрал Flash/Max через
pay-as-you-go с reason `list price`, а broad pointer указывает на указанную выше r2
publication.

## Что не считать завершённым

Гейты r2 прошли, но ручная shadow проверка обнаружила несколько trend names в форме
лексического набора слов. Это не повод переписывать immutable r2, но это приоритетное
следующее улучшение перед следующим release. Конкретный план и acceptance criteria — в
[`NEXT_IMPROVEMENTS.md`](NEXT_IMPROVEMENTS.md).

## Ревью 2026-08-06: три утверждения выше были неверны

Статическое ревью этой сессии (без пересбора и без Qwen) нашло шесть семантических
дефектов при полностью зелёных гейтах — ни один тест не проверял *порядок* сортировки,
*различимость* transport-ошибки и empty и *пересчёт* агрегатов тренда после ревью.

| дефект | что было | статус |
|---|---|---|
| `schema_v3` не применял `is_out_of_scope` к заголовку | v3 пускал в тренды то, что v2 отвергал: замер по двум локальным релизам — 66 и 109 сюжетов (1.9 % и 2.3 %) | исправлено |
| агрегаты тренда не пересчитывались после отсева ревью | `confirmed`-тренд мог быть одноакторным вопреки `min_distinct_actors ≥ 2`; UI рендерил акторов без сюжетов | исправлено |
| `source_scope` считался из множества *счётчиков*, а не провайдеров | тренд из сюжетов по два источника получал `single_source` (дефект предшествует сессии, `e80b743`) | исправлено |
| транспортная ошибка отмывалась в `empty` | провалившийся день писался `complete` и исчезал из `--coverage` | исправлено |
| `--historical-date` писал поверх существующих артефактов | настоящий дневной сбор уничтожался ретроспективным запросом; при отказе фетча — усечение до нуля | исправлено |
| `_news_date` сравнивал ISO-8601 и RFC 2822 как строки | `b048cdf` поднял дату в первичный ключ, и `/news?sort=fresh` сортировал по названию дня недели | исправлено |

r2 остаётся immutable: правки идут вперёд, в следующий shadow. Масштаб второго дефекта
на самом r2 локально не измерить — локальные релизы построены методом `story_graph_v1`
без акторов, а кэш `story_schemas` живёт на VPS. Это read-only проверка для следующего
цикла, вместе с вопросом, сработало ли уже затирание артефактов за 2026-08-04.


---

# Итог 2026-08-06: полный цикл на боевых данных и публикация r3

Сессия начиналась как ревью предыдущей и закончилась заменой broad-указателя.

## Опубликовано

| сущность | ID |
|---|---|
| Data Release | `2026-07-31_2026-08-06-broad-r3`, complete, 11 232 материала |
| Story Release | `stories_8775429b947a0d70df2e` |
| Trend Release | `trends_e4a968f66126b74eadda` |
| Broad publication | `publication_e3ad7e0e8fc500c8d325` |
| Цель отката | `publication_5f42686a86628e14680a` (прежний r2) |

`gate_bypassed = 0` — все пятнадцать полов пройдены честно, без `--force`.

## Что изменилось для читателя

```
было (r2, embedding_v2)          стало (r3, schema_v3)
207 | job career i'm work         51 | product launches in AI
154 | actually promote saas first 50 | earnings and results in business
 60 | credit retirement money      44 | new regulation in business
 57 | yorker keep truckin sitemap  31 | model releases in AI
```

104 тренда, **0 дефектных имён** против 79 из 80 у прежнего метода.

## Три дефекта, которые нашлись только на живых данных

1. **`merge_model` вычёркивала пары до cross-encoder.** Модель на 222 автометках
   (из них 7 ответов Qwen) делала `return None` на отказе — пара исчезала из набора
   до откалиброванного при precision ≥ 0.95 ранжировщика. Цена: 944 → 390 multi-item.
   Слабый сигнал побеждал сильный тем, что шёл первым.
2. **`embedding_v2` не способен на читаемое имя.** Не «несколько плохих имён», как
   считал бэклог, а свойство метода: он называет кластер частотными токенами.
3. **`collect --sources` объявлял прод-профиль полным без Reddit.** Guard стоял
   только в финализации, живой путь остался без него.

## Метод, которым они нашлись

Ни один не виден на синтетике. Все три дал прогон на настоящем корпусе плюс сравнение
двух релизов **внутри одного цикла** — приём, до которого я дошёл не сразу: сначала час
сравнивал финальные релизы между собой и объяснял расхождение дрейфом корпуса.

Дрейф корпуса при этом реален (15.6 % материалов окна перезаписываются за ночь,
`input_checksum` не воспроизводится) и записан в `COLLECTION_LIFECYCLE.md` — просто он
объяснял малую часть расхождения, а не главное.
