# Следующие улучшения после релиза 2026-08-05

> Это приоритизированный backlog, а не разрешение запускать новые production rebuilds.
> Любая работа с Engine сначала идёт на локальной scratch-копии Data Release, затем через
> Golden Set/tests, потом — в shadow.

## P0 — качество имён Trends

**Наблюдение.** Hard floors прошли, но на shadow были лексические названия наподобие
`agent built actually tool` и `job see point pay feel`. Это означает, что существующий
`trends_bad_name_count` не ловит token-bag, хотя формально имя не single-token и не bare verb.

**Решение.**

1. Добавить deterministic name-quality classifier: stopword-heavy, слабая head noun,
   слишком много function words, отсутствие actor/action/object либо повторяющийся generic
   template — отдельные причины rejected name.
2. Расширить `engine quality report`: count + примеры дефектных names. Broad floor должен
   блокироваться, если такой count > 0.
3. Сделать bounded manual synthesis для *только дефектных* accepted trend candidates:
   `qwen3.8-max` после проверки цены в console генерирует concise evidence-grounded name;
   response валидируется по schema и materializes новый TrendRelease. Не использовать
   ad-hoc `engine trends review --limit 200` как shortcut к публикации.
4. Внести 20–30 examples в trend-name Golden Set: хороший name, generic name, token bag,
   непроверяемое editorial claim.

**Acceptance:** новый shadow release имеет `trends_bad_name_count=0`, ручная выборка
top-20 names читаема без evidence context, all tests и quality gates проходят.

## ✅ Закрыто 2026-08-06 — наблюдаемость пропущенных дней

**Цель.** До запуска Engine видеть gap, а не обнаруживать его при ручной SQL-диагностике.

- Добавить в `/runs` и `/engine` явную calendar coverage strip: completed / pending /
  recoverable-from-artifacts / unavailable.
- `collect --coverage` выводит machine-readable JSON и exit code для будущего cron alert.
- Alert должен предлагать только безопасное действие: exact snapshot recovery, historical
  recovery или явный unresolved gap; никогда не подставлять live fetch за прошлую дату.

**Как закрыто.** `coverage_summary` классифицирует каждый день в одно из четырёх состояний
и предлагает только безопасное действие: финализацию сохранённых артефактов либо date-aware
историческое восстановление. Живой сбор за прошлую дату не предлагается никогда. Текущий
UTC-день пропуском не считается — иначе алерт срабатывал бы каждую ночь. `collect --coverage`
печатает `{"summary": …, "days": […]}`, а `--fail-on-gap` даёт код возврата 1 для cron.
Полоса покрытия за 14 дней — на `/runs`, теми же словами, что и CLI.

## ✅ Закрыто 2026-08-06 — единый release-readiness отчёт

**Цель.** Убрать ручное склеивание `/runs`, SQL, `quality report`, publications и UI sample.

- Новая read-only команда `engine release readiness --data-release …` собирает checksum,
  coverage, Story/Trend/Signal IDs, quality/check result, Qwen diagnostics, current shadow/
  broad pointer, rollback target и заданную выборку evidence links.
- Печатает JSON для журнала и короткий Markdown для оператора; не имеет publish-флага.

**Как закрыто.** `engine release readiness --release … [--format json|markdown|both]`.
Собирает checksum, input_status, полы качества с *названиями* проваленных метрик, указатели
каналов и цель отката. Флага публикации нет по построению: команда, которая одновременно
оценивает готовность и умеет публиковать, рано или поздно опубликует по ошибке. Тест
проверяет, что число публикаций до и после вызова совпадает.

## ✅ Закрыто 2026-08-06 — дефекты Qwen-роутинга

Все шесть исправлены и покрыты тестами в той же сессии; раздел оставлен как запись о том,
что именно было сломано и почему. Незакрытым остаётся только пункт 3 (учёт токенов при
timeout) — он требует решения о том, что записывать, не выдумывая чисел.

1. **Мёртвый guard `_PAYG_ONLY_MODELS`** (`signals.py:105`). Условие срабатывает только
   при явном `endpoint == "token-plan"`, а `qwen_policy` эту строку никогда не возвращает
   (обе цепочки — `payg`, `pick_endpoint` отдаёт `payg` или `""`). На деплое, где задан
   только `QWEN_TOKEN_PLAN_KEY`, `qwen3.7-flash` — теперь дефолт для `engine stories
   review`, `engine trends review`, `--review-model` и `--trend-review-model` — уходит на
   token-plan URL и получает 404 на **каждом** review-джобе.
2. **`usage_totals` не защищён** (`qwen_policy.py:148`), хотя `record_usage` — защищён.
   `pick_model`/`pick_endpoint` зовут его первым, и вызывающие делают это *вне* своих
   try-блоков. `database is locked` под 6–8-поточной конкуренцией убивает bulk-стадию.
3. **Токены при timeout списаны, но не записаны** (`signals.py:226` идёт только после
   200). Отмена по `TREND_REVIEW_TIMEOUT_SECONDS = 240` не пишет ничего — роутер
   переоценивает остаток бесплатной квоты. Самые дорогие вызовы рискуют больше всех.
4. **Полный отказ провайдера даёт exit 0 и пустой релиз.** `analyze_posts` continue’ит на
   каждой ошибке; неверный ключ → все батчи 401 → «Извлечено 0 сигналов», пустой
   `signals.jsonl`, отчёт, радар и код возврата 0.
5. **Падение на нечисловом env.** `payg_free_quota()`/`token_plan_quota()` делают голый
   `int(raw)`: `RC_QWEN_PAYG_FREE_TOKENS=1M` роняет стадию трейсбеком. Отрицательное
   значение молча означает «нет квоты». `payg_grant_start` при этом ValueError глотает —
   и битая дата навсегда отключает 90-дневную проверку.
6. **Цепочки не cheapest-first.** `BULK_CHAIN` падает с `qwen3.7-flash` на `qwen3.6-flash`,
   который по собственной таблице `QWEN_ROUTING.md:39` дороже в 8.3×/11.5×. У `SYNTH_CHAIN`
   та же инверсия. Цепочки дешевле только в предположении, что у каждой модели свой
   нетронутый грант.

**Как закрыты.** (1) проверка перенесена с несуществующей строки `endpoint` на саму модель
и поднимает `QwenConfigError`; (2) `usage_totals` ловит `OSError`/`sqlite3.Error` и
возвращает нулевой расход — недоступный леджер означает «расход неизвестен», а не отказ
стадии; (4) введён `QwenAllBatchesFailedError`, а конфигурационные ошибки получили
собственный тип и больше не логируются как «parse error»; (5) общий `_int_env` с
предупреждением, `payg_grant_start` больше не отбрасывает смещение и не молчит о битой
дате; (6) введён `RC_QWEN_PAYG_GRANT_PER_MODEL`, по умолчанию грант считается общим
пулом, поэтому после его исчерпания роутер остаётся на самой дешёвой модели.

**Остаётся (3).** Токены, списанные провайдером до отмены по timeout, по-прежнему не
попадают в леджер. Записывать оценку значило бы выдумать число; правильный шаг —
отдельный счётчик неучтённых вызовов, чтобы роутер знал, что его оценка остатка неполна.

## ✅ Закрыто 2026-08-06 — бюджет и provenance Qwen

- Введи per-release Qwen cost report: input/output tokens по stage, model, endpoint и
  reason routing. Цена — только list price или явно записанная promotion metadata с датой.
- Добавь configurable hard spend guard для Max synthesis и мягкое предупреждение для Flash.
- Не включай `RC_QWEN_PAYG_FREE_TOKENS`, пока grant не подтверждён в Model Studio console;
  если он включён, записывай дату подтверждения отдельно от секрета.

**Как закрыто.** В леджер добавлена колонка `stage`, вызовы размечены (`schema_extract`,
`pair_review`, `trend_review`, `actor_normalize`, `classify`). `reddit-compass qwen cost`
даёт расход по (стадия, модель, эндпоинт) с оценкой в CNY по list price и датой проверки
цен. Модели без цены считаются отдельным `unpriced_calls`, а не нулём: неизвестная цена и
бесплатный вызов — разные вещи. `RC_QWEN_MAX_SPEND_CNY` — жёсткий потолок, проверяется
**до** вызова; не задан — неявного лимита не появляется.

## ✅ Закрыто 2026-08-06 — UX ранжирования и времени

- Оставить текущий default «сила → свежесть», но добавить явный URL/filter control
  `sort=strength|fresh|oldest` (плюс `engagement` у News, `volume` у Stories,
  `coverage` у Trends) без изменения published data. Токен — `fresh`, не `freshness`:
  `_safe_sort` молча приводит неизвестное значение к дефолту, поэтому `?sort=freshness`
  тихо отдавал бы strength-порядок.
- Унифицировать timestamp/date label и timezone policy: source date, observed-at и
  release-published-at не должны смешиваться в одной подписи.
- Добавить snapshot UI tests, где старый сильный item и свежий слабый item меняются местами
  при переключении sort.

**Как закрыто.** Токены сортировки валидируются (422 на API, приведение к дефолту в UI),
даты приводит к одному виду `api/dates.py`, а тесты проверяют *порядок*, а не код ответа —
именно отсутствие таких тестов и позволило дефекту дожить до прода.

## P3 — возврат cron: защиты готовы, само расписание ждёт двух ручных циклов

- Выполнить ещё два ручных цикла по runbook с непрерывной coverage и записать timing/
  failure modes.
- Затем включить ровно version-controlled host-cron template; alert owner о неудаче, но не
  выполнять неявный broad publish.
- Проверить, что cron lock исключает второй Collector/Engine и что отложенная job не
  перезаписывает более свежий shadow pointer.

**Что уже готово (2026-08-06).** Обе защиты сделаны и покрыты тестами, поэтому включение
расписания больше не упирается в них — только в два ручных цикла:

- каждая строка шаблона `deploy/hostkey/reddit-compass.cron` обёрнута в `flock -n` (общий
  замок у стадий сбора, отдельный у Engine). `-n` означает «пропустить», а не «встать в
  очередь»: пропущенная ночь видна в `collect --coverage`, испорченная — нет;
- `publish_radar` отказывается двигать указатель канала назад: задача, догнавшая
  выполнение после следующего цикла, больше не может молча вернуть канал на устаревший
  Data Release. Осознанный откат остаётся доступен через `engine rollback` или `--force`.
