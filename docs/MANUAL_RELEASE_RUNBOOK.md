# Manual Release Runbook — от сбора до Broad Radar

> Операционный чек-лист для ручного режима. Автоматические collection/finalization/Engine
> jobs на VPS приостановлены; этот документ **не** включает cron и не публикует `broad`
> автоматически.
>
> Алгоритмический контракт Engine — в [`TREND_ENGINE.md`](TREND_ENGINE.md), состояния и
> границы машин — в [`COLLECTION_LIFECYCLE.md`](COLLECTION_LIFECYCLE.md), абсолютные и
> регрессионные гейты — в [`QUALITY_GATES.md`](QUALITY_GATES.md), выбор Qwen — в
> [`QWEN_ROUTING.md`](QWEN_ROUTING.md). Конфигурация
> [`config/profiles/broad.json`](../config/profiles/broad.json) — единственный источник
> истины для собираемой поверхности.

## 1. Что считается завершённым

У одного ручного запуска есть два независимых результата:

```text
JSONL snapshots
  → raw run complete in compass.db
  → immutable Data Release (checksum verified)
  → facets → stories → bounded Qwen → trends → quality
  → shadow publication
  → explicit human decision → broad publication
```

`collection complete` означает, что все пять артефактов (`reddit`, `hn`, `rss`, `ladder`,
`ph`) конкретной UTC-даты финализированы в `compass.db` и их health виден в `/runs`. Для
production Data Release даты должны также образовывать непрерывное календарное окно: семь
raw runs с пропуском дня остаются `input_status=partial`, даже если каждый отдельный run complete.
Это ещё не редакционный выпуск. `broad published` означает, что complete input прошёл
quality gate, был просмотрен в `shadow`, а оператор явно переключил immutable pointer.
Последний шаг всегда ручной.

Ни `compass.db`, ни старые immutable release не перезаписываются. Collector не вызывает
LLM; Engine открывает `compass.db` только для чтения; rollback меняет только channel pointer.

## 2. Правила перед началом

1. Работать только в `/opt/reddit-compass` и только со стеком этого приложения. Не трогать
   другие VPS-стеки, volumes, образы или cron.
2. Все даты запуска — UTC. Сразу запишите текущую дату и соответствующее московское время:

   ```bash
   date -u '+UTC %F %T'
   TZ=Europe/Moscow date '+MSK %F %T'
   ```

3. Убедитесь, что уже нет batch-контейнера сборщика или Engine. Второй такой запуск может
   одновременно писать один `run_id` или конкурировать за CPU/RAM:

   ```bash
   cd /opt/reddit-compass
   docker compose ps
   docker ps --filter status=running \
     --format 'table {{.Names}}\t{{.Status}}\t{{.Command}}'
   ```

   В частности, `engine trends review` — тоже batch, даже если он выглядит как отдельная
   диагностика. Дождитесь его выхода перед collection/recovery или новым `engine cycle`: он
   пишет в тот же Engine ledger и занимает Qwen slot. Не оставляйте цепочку в фоне без
   лога/владельца; зафиксируйте её команду и PID в журнале ручного запуска.

   Исключение возможно только для **raw-only recovery другой даты**: Engine должен читать уже
   frozen Data Release, новый collector — писать другой `run_id` в `compass.db`, а оператор —
   подтвердить отсутствие конкурирующего collector и достаточные CPU/RAM. При этом нельзя
   запускать новый Engine, shadow или broad до выхода исходного Engine batch.

4. До старта сохраните диагностический срез. Он read-only и не раскрывает секреты:

   ```bash
   docker compose run --rm reddit-compass db stats --source-db /data/compass.db
   docker compose run --rm reddit-compass engine publications --channel broad
   docker compose run --rm reddit-compass engine publications --channel shadow
   ```

5. Не запускать `db rebuild`, не править legacy `stories`/`story_metrics`, не копировать
   локальный `compass.db` на VPS и не выполнять `docker system prune`/`docker compose down`.

## 3. Проверка пропущенных дат и честный backfill

Перед каждым новым циклом нужно проверить не только последний run, но и календарные даты с
предыдущей complete collection. Это не позволяет тихо расширить «7-дневное» окно старыми
данными, когда один или несколько дней выпали. Встроенный read-only механизм сравнивает
состояние raw run, adapter-level health и наличие всех датированных JSONL:

```bash
docker compose run --rm reddit-compass collect --coverage \
  --profile broad --since 2026-08-01 --until 2026-08-05
```

Если дата `D` имеет все свои сохранённые artifacts, но raw finalizer не был запущен или
остался pending/partial, recovery безопасно финализирует её без сети и Qwen:

```bash
docker compose run --rm reddit-compass collect --recover-snapshots \
  --profile broad --since 2026-08-01 --until 2026-08-05
```

Команда выводит coverage **до** recovery и список фактически finalised run IDs. Она берёт
только complete set из `posts.jsonl`, `hackernews.jsonl`, `rss.jsonl`, `ladder.jsonl` и
`producthunt.jsonl`; сетевые adapters и LLM не вызываются. Это нормальный способ исправить
пропущенный ручной finalizer.

Для точечной проверки конкретной даты можно посмотреть её каталог и финализировать вручную:

```bash
RUN_DATE=D
docker compose run --rm --entrypoint sh reddit-compass -c \
  "ls -lh /data/snapshots/${RUN_DATE}/"
docker compose run --rm reddit-compass collect --from-snapshots \
  --profile broad --sources reddit,hn,rss,ladder,ph --date "$RUN_DATE"
```

После этого выполните проверку health из раздела 5. Повторная финализация той же даты
идемпотентна: она обновляет factual raw run из тех же artifacts, а не создаёт новые версии
Engine.

### Если исторических JSONL нет

Сначала используйте date-aware public recovery. Она собирает не «сегодняшнюю ленту под
вчерашним именем», а только material с фактической датой публикации target UTC-day; фактический
`observed_at` остаётся моментом recovery и виден в raw facts/source health.

```bash
RUN_DATE=2026-08-04
docker compose run --rm reddit-compass collect \
  --historical-date "$RUN_DATE" --profile broad \
  --sources reddit,hn,rss,ladder,ph

# Then insist on the normal factual check before any Engine work.
docker compose run --rm reddit-compass collect --coverage \
  --profile broad --since "$RUN_DATE" --until "$RUN_DATE"
```

Recovery намеренно ограничен и прозрачен:

- Reddit использует публичные `new`/weekly `top` listings, фильтрует каждый пост по
  `created_utc`, сохраняет request pacing и не загружает исторические комментарии.
- Hacker News запрашивается в Algolia по точному UTC-интервалу.
- RSS использует дневной Google News query, где он доступен, и дополнительно фильтрует каждую
  запись по дате публикации; Product Hunt так же фильтрует Atom entries.
- Ladder не переиспользует current section listing: для historical recovery он делает public
  date-filtered Google News discovery по каждому исходному домену. Каждая карточка несёт
  `monitoring_type=ladder_historical_google_news` и дополнительно фильтруется по дате
  публикации. Если этот публичный путь реально не вернул material, health останется `empty` —
  current listing не подменяет отсутствующий день.

Historical recovery — это восстановление, наблюдаемое позднее, а не байт-в-байт replay
первоначального ranking/engagement snapshot. Перед редакционным решением проверьте его source
health и quality outcome в shadow. Путь остаётся read-only и не использует аккаунты или private
data.

Если релевантный публичный источник больше не отдаёт нужную дату, recovery явно вернёт `empty`
или `partial`. Только тогда дата действительно недоступна. В таком случае не создавайте
фиктивный run. Следующие действия запрещены:

- повторно собрать current listing и назвать output датой пропуска;
- скопировать `snapshots/<today>` в `snapshots/<missing-date>`;
- создавать partial run и выдавать его за complete.

Если и source-specific recovery, и backup не дали данных, gap остаётся явным
(`pending`/отсутствующая дата); Engine можно исследовать только как preview/shadow на complete
data, но нельзя публиковать этот выпуск в `broad`.

Следствие для каждого ручного цикла: сначала восстановить **все** доступные пропущенные даты,
затем собирать текущую. Для нового источника без честного historical interface нужен отдельный
source-specific archive adapter с тестами; fallback на переименование сегодняшних данных запрещён.

## 4. Сбор нового UTC-дня

Ниже переменная — только удобство shell; дата должна совпадать с текущей UTC-датой.

```bash
RUN_DATE="$(date -u +%F)"
printf 'Collecting UTC date: %s\n' "$RUN_DATE"
```

Запускайте адаптеры последовательно. Reddit rate-limited и read-only; proxy разрешён только
для 429, а не для обхода банов. Если Reddit уже идёт, дождитесь его завершения, не создавайте
второй container.

```bash
# 1. Public Reddit through the configured approved route/proxy.
docker compose run --rm reddit-compass collect \
  --sources reddit --stealth --profile broad

# 2–5. Remaining snapshot artifacts on VPS.
docker compose run --rm reddit-compass rss
docker compose run --rm reddit-compass hn
docker compose run --rm reddit-compass ladder
docker compose run --rm reddit-compass ph
```

До finalizer убедитесь, что каталог относится к `$RUN_DATE` и содержит все пять файлов.
Пустой файл допустим только если adapter честно вернул `empty`, поэтому проверяем наличие,
а решение принимает finalizer/source health:

```bash
docker compose run --rm --entrypoint sh reddit-compass -c \
  "ls -lh /data/snapshots/${RUN_DATE}/"
```

Затем создайте один factual raw run из уже собранных JSONL. Эта команда не ходит в сеть и
не вызывает Qwen:

```bash
docker compose run --rm reddit-compass collect \
  --from-snapshots --profile broad \
  --sources reddit,hn,rss,ladder,ph --date "$RUN_DATE"
```

`collect --sources reddit` до finalizer — лишь этап получения `posts.jsonl`; он не заменяет
полный `broad` raw run. Если один из пяти artifact отсутствует или не читается, finalizer
должен оставить факт как `pending`/`partial`; остановитесь и устраните причину, а не запускайте
production Engine на неполном input.

## 5. Гейт raw collection

Проверьте состояние run и каждый источник. `items` считаются по observations, поэтому
дедупликация items не скрывает фактическое число материалов в run.

```bash
docker compose run --rm --entrypoint python reddit-compass -c "
import sqlite3
date = '${RUN_DATE}'
conn = sqlite3.connect('/data/compass.db')
run = conn.execute(
    'SELECT run_id, snapshot_date, profile, status, started_at, finished_at '
    'FROM runs WHERE snapshot_date = ? AND profile = ?', (date, 'broad')
).fetchone()
print('run:', run)
if run:
    print('observations:', conn.execute(
        'SELECT COUNT(*) FROM observations WHERE run_id = ?', (run[0],)
    ).fetchone()[0])
    for row in conn.execute(
        'SELECT source_id, status, count, COALESCE(error_code, \"\"), message '
        'FROM source_health WHERE run_id = ? ORDER BY source_id', (run[0],)
    ):
        print(' | '.join(str(value) for value in row))
"
```

Продолжать можно, только если raw run имеет `status=complete`, все пять adapter-level source
rows присутствуют и нет `error`, `pending`, `skipped` или `not_configured`. `empty` — не
сбой транспорта, но его нужно сверить с expected minimum профиля: пустой обязательный cluster
делает будущий Data Release `partial`.

Заодно откройте read-only журнал в UI: `/runs` должен показывать collection как первую
фактическую стадию, а не зелёный статус без source-level detail.

## 6. Engine и экономия Qwen

Обычный production-path не ждёт 17:00: pair и bounded trend review выполняются
`qwen3.7-flash` с `think=False`. По текущему international list price это
¥0.225/¥0.974 за 1M input/output против ¥14.988/¥44.965 у `qwen3.8-max`; то есть
примерно 67×/46× дешевле. Engine использует только pay-as-you-go API; Token Plan не
является endpoint'ом сервиса.

`qwen3.8-max` оставлена только для явно согласованной ручной эскалации свободного сложного
synthesis. Её имеет смысл запускать после 17:00 МСК (14:00 UTC), если условия скидки для
вашего тарифного плана подтверждены. Перед такой эскалацией проверьте время и решение
роутера; ключи и их значения в вывод не попадают:

```bash
TZ=Europe/Moscow date '+MSK %F %T'
docker compose run --rm reddit-compass qwen pick --task synth
docker compose run --rm reddit-compass qwen usage
```

Модель входит в cache key review. Поэтому смена с прежних `qwen3.6-flash`/`qwen3.8-max`
создаёт новые контролируемые записи review, не перезаписывая старые. Не заменяйте Flash на
Max массово: это не улучшает deterministic/cross-encoder gate и делает сотни однотипных
вызовов многократно дороже.

Запустите один новый immutable attempt. `--cross-encoder` обязателен для production-path:
без него не достигаются три completeness floors Stories. Команда создаёт только `shadow`
publication после успешного quality gate; она не двигает `broad`.

```bash
docker compose run --rm -e HF_HOME=/data/.cache/hf reddit-compass engine cycle \
  --profile broad --window 7 \
  --cross-encoder --review-limit 20 --review-model qwen3.7-flash \
  --trend-review-limit 12 --trend-review-model qwen3.7-flash \
  --publish-channel shadow
```

Ожидаемое время на VPS — 30–60 минут. Не прерывайте команду из-за долгого Qwen request:
его timeout и продолжение следующей bounded-порции уже встроены. Сохраните JSON output — он
содержит IDs Data/Facet/Story/Trend/Signal releases и diagnostics. Ошибка Qwen не даёт ей
стать merge; error должен остаться visible в выводе и `/engine`.

Не заменяйте эту bounded-стадию командой `engine trends review --limit 200` для обычного
release cycle. Такой ad-hoc review не создаёт quality report сам по себе, конкурирует за тот же
Qwen endpoint и при provider timeouts может занять часы. Он допустим только как явно
зафиксированный исследовательский шаг после release, с отдельной повторной materialization и
quality check; не как путь к `broad`.

## 7. Quality, shadow и ручная публикация

Сначала подтвердите checksum frozen input и сохранённый quality outcome. Если cycle создал
shadow publication, эти команды разрешат нужную тройку releases через pointer:

```bash
docker compose run --rm reddit-compass engine publications --channel shadow
docker compose run --rm reddit-compass engine quality report --channel shadow
docker compose run --rm reddit-compass engine quality check --channel shadow
```

Если shadow pointer не создан, не подменяйте его. `quality report --channel shadow` в этом
случае показывает **предыдущий** shadow release и не является результатом только что
завершившегося cycle. Возьмите IDs из output cycle и выполните диагностику явно:

```bash
docker compose run --rm reddit-compass engine release verify --release DATA_RELEASE_ID
docker compose run --rm reddit-compass engine quality report \
  --data-release DATA_RELEASE_ID \
  --story-release STORY_RELEASE_ID \
  --trend-release TREND_RELEASE_ID \
  --signal-release SIGNAL_RELEASE_ID
docker compose run --rm reddit-compass engine quality check \
  --data-release DATA_RELEASE_ID \
  --story-release STORY_RELEASE_ID \
  --trend-release TREND_RELEASE_ID \
  --signal-release SIGNAL_RELEASE_ID
```

Перед `broad` оператор проверяет в `/runs`, `/engine`, `/radar` и `/today`:

- raw run complete, все expected adapters и source health видимы;
- checksum Data Release verified, `input_status=complete`;
- Story и Trend attempts evaluated, Qwen diagnostics понятны, invalid reviews не стали merges;
- `engine quality check` прошёл все floors и не нашёл regression относительно baseline;
- есть достаточная история для заявлений lifecycle (семь **последовательных UTC-дней**;
  30 дней для meta-trends);
- shadow inspected: evidence links открываются, имена не generic/duplicate, Radar не выдаёт
  pending candidate за confirmed editorial conclusion.
- UI smoke check: `/news` в режиме «По сюжетам» не повторяет несколько привязанных материалов
  одной карточкой (счётчик сообщает число raw materials); «Все материалы» возвращает их для
  аудита. Проверьте `fresh` и `strength` на News, Stories, Trends и Today: даты на карточках
  должны соответствовать выбранному порядку. Stories/Trend detail обязан по-прежнему показывать
  полный evidence — это не UI-дубликат, а проверяемая причина группировки.

Только после явного решения оператора публикация в production меняет pointer:

```bash
docker compose run --rm reddit-compass engine publish \
  --story-release STORY_RELEASE_ID \
  --trend-release TREND_RELEASE_ID \
  --channel broad
```

Не передавайте `--allow-partial` или `--force` для `broad`. Не публикуйте только потому, что
shadow обновился. После publication заново проверьте `/today`, `/radar` и:

```bash
docker compose run --rm reddit-compass engine publications --channel broad
```

## 8. Rollback и завершение журнала

Перед publication запишите текущий `broad` publication ID из `engine publications --channel
broad`. Если UI или post-publication inspection выявила проблему, rollback безопасен и
атомарен: raw data и immutable releases он не удаляет.

```bash
docker compose run --rm reddit-compass engine rollback \
  --channel broad --to PREVIOUS_PUBLICATION_ID
```

В журнале ручного запуска сохраните: UTC date, все raw run IDs и status, artifact health,
release IDs/checksum, параметры cycle, выбранные Qwen model/endpoint reason, quality result,
shadow/broad publication IDs, решение оператора и причину rollback (если был). Не записывайте
в этот журнал секреты, токены или proxy URL.

## 9. Короткое дерево решений

```text
Есть незавершённый collector? ── да → дождаться / диагностировать, не запускать второй
                                 нет
                                  ↓
Есть календарный gap? ── да → есть exact JSONL? ── да → finalize missing date
                                  └─ нет → date-aware public recovery → health/quality check
                                                         └─ unavailable → leave gap visible
                                  ↓
Все пять snapshots текущей даты? ── нет → собрать/починить adapter, Engine не запускать
                                      да
                                       ↓
raw complete + expected health? ── нет → stop; only diagnostics/preview
                                    да
                                     ↓
обычный engine cycle (Flash, любое время) → quality check → shadow inspection
                                                               ↓
                           нужна сложная ручная эскалация после 17:00 МСК? ── да → Max synthesis
                                                               ↓
                                             explicit operator approval? ── да → publish broad
                                                                                нет → leave shadow
```
