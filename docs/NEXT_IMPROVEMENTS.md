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

## P1 — наблюдаемость пропущенных дней

**Цель.** До запуска Engine видеть gap, а не обнаруживать его при ручной SQL-диагностике.

- Добавить в `/runs` и `/engine` явную calendar coverage strip: completed / pending /
  recoverable-from-artifacts / unavailable.
- `collect --coverage` выводит machine-readable JSON и exit code для будущего cron alert.
- Alert должен предлагать только безопасное действие: exact snapshot recovery, historical
  recovery или явный unresolved gap; никогда не подставлять live fetch за прошлую дату.

**Acceptance:** искусственно удалённый raw run обнаруживается тестом и UI, а recovery
создаёт новый factual run с source health без изменения существующих rows.

## P1 — единый release-readiness отчёт

**Цель.** Убрать ручное склеивание `/runs`, SQL, `quality report`, publications и UI sample.

- Новая read-only команда `engine release readiness --data-release …` собирает checksum,
  coverage, Story/Trend/Signal IDs, quality/check result, Qwen diagnostics, current shadow/
  broad pointer, rollback target и заданную выборку evidence links.
- Печатает JSON для журнала и короткий Markdown для оператора; не имеет publish-флага.

**Acceptance:** команда заменяет диагностический раздел runbook одним reproducible output,
а тест гарантирует, что она не мутирует обе БД и не меняет pointer.

## P2 — бюджет и provenance Qwen

- Введи per-release Qwen cost report: input/output tokens по stage, model, endpoint и
  reason routing. Цена — только list price или явно записанная promotion metadata с датой.
- Добавь configurable hard spend guard для Max synthesis и мягкое предупреждение для Flash.
- Не включай `RC_QWEN_PAYG_FREE_TOKENS`, пока grant не подтверждён в Model Studio console;
  если он включён, записывай дату подтверждения отдельно от секрета.

**Acceptance:** один release output показывает cost estimate и не может назвать неизвестный
грант бесплатным.

## P2 — UX ранжирования и времени

- Оставить текущий default «сила → свежесть», но добавить явный URL/filter control
  `sort=strength|freshness` без изменения published data.
- Унифицировать timestamp/date label и timezone policy: source date, observed-at и
  release-published-at не должны смешиваться в одной подписи.
- Добавить snapshot UI tests, где старый сильный item и свежий слабый item меняются местами
  при переключении sort.

## P3 — возврат cron только после двух ручных циклов

- Выполнить ещё два ручных цикла по runbook с непрерывной coverage и записать timing/
  failure modes.
- Затем включить ровно version-controlled host-cron template; alert owner о неудаче, но не
  выполнять неявный broad publish.
- Проверить, что cron lock исключает второй Collector/Engine и что отложенная job не
  перезаписывает более свежий shadow pointer.
