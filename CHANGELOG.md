# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/);
версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **Manual Release Runbook и gap recovery.** `docs/MANUAL_RELEASE_RUNBOOK.md` фиксирует
  полный ручной путь VPS: пять source adapters и factual finalizer, raw/source-health gate,
  Engine в shadow, Qwen `qwen3.7-flash` для bounded review и `qwen3.8-max` только для
  явного сложного synthesis после проверки условий тарифа, quality check, ручной Broad
  publication и pointer-only rollback. Новые команды
  `collect --coverage --since … --until …` и `collect --recover-snapshots` находят
  календарные gaps по raw run, health и artifacts, а затем без сети финализируют только
  пропущенные даты с полным набором их собственных JSONL. Для отсутствующего artifact
  `collect --historical-date` запрашивает только date-aware public interfaces (Reddit/HN/RSS/PH),
  сохраняет фактическое время наблюдения, а Ladder — у чьего listing нет надёжного
  historical query — идёт через date-filtered Google News discovery с двойной защитой:
  провайдерский `after:/before:` и локальная перепроверка даты публикации. Сегодняшняя
  выборка никогда не выдаётся за пропущенную историческую дату.

- **UI: оригинальные имена трендов, русская подпись ревью, ссылки и кликабельные
  рубрики.** Трендовое ревью больше не переименовывает тренды: `name_ru`/`pattern`
  остаются на языке корпуса, а русское имя из ревью пишется в новую колонку
  `engine_trends.review_name_ru` и показывается подписью рядом на /trends, /today и
  radar. Counterpoints на странице тренда — прямые ссылки на stories; доменные чипы
  (`ai_technology`, `business_markets`, …) на /news, /stories и /trends — ссылки,
  фильтрующие текущий слой по рубрике.

- **Стоимостная маршрутизация Qwen (`qwen_policy`, `docs/QWEN_ROUTING.md`).**
  Массовые прогоны (извлечение схем, нормализация акторов, классификация) уходят на
  pay-as-you-go API на `qwen3.7-flash`; Token Plan — интерактивный продукт и endpoint
  сервиса им больше не является. Локальный леджер `qwen_usage.db` пишет `usage` каждого
  вызова; команда `qwen pick` честно показывает list price, пока владелец явно не
  подтвердит грант через `RC_QWEN_PAYG_FREE_TOKENS`. Для bulk-вызовов выключен reasoning
  (`enable_thinking=false`): замер показал ~150 сгоревших reasoning-токенов на тривиальный
  запрос у qwen3.6-flash. Обычные pair и bounded trend review используют
  `qwen3.7-flash`: international list price ¥0.225/¥0.974 за 1M input/output против
  ¥14.988/¥44.965 у `qwen3.8-max`. Max остаётся только для согласованной ручной
  эскалации свободного synthesis после проверки актуальной акции в Model Studio console.

- **Precision-first same-provider guard для Stories.** MinHash/SimHash fingerprint и
  exact-title merge больше не становятся auto-merge доказательством для двух материалов
  одного provider без общего event URL. Это разрывает шаблонные Financial Times earnings
  announcements, повторяющиеся landing pages и похожие Reddit-вопросы до quality gate,
  сохраняя cross-source syndicated headlines и точные URL-дубли.

- **UI: управляемый порядок и доказательные дубли.** News по умолчанию показывает один
  ранжированный материал на уже связанный Story, а `view=items` сохраняет полный raw-inbox для
  аудита; immutable Data Release не меняется. News, Stories и Trends получили allow-listed
  серверные сортировки (сила/свежесть плюс engagement, объём evidence или охват), а Today
  согласованно ранжирует changes, reading и Reddit shelf по силе либо свежести. Параметры
  сохраняются в UI-пагинации и API, даты остаются на карточках. Pulse сознательно сохраняет
  свой signal-ranking: несколько community posts — отдельные сигналы, а не копии одной статьи.

- **Поколение 5 слоя Trends: `schema_v3`** — схему события `(актор, действие, объект,
  ключ)` извлекает LLM вместо лексикона из тринадцати регулярок (замер: recall ≈ 13 %,
  precision ≈ 63 %). Действие нормализуется в закрытый словарь из 29 ключей; корзины
  `other` / `incident` / `milestone` трендом не становятся намеренно. Кэш извлечения —
  таблица `story_schemas` с ключом по хэшу заголовка и версии промпта, `schemas_digest`
  входит в `params_hash`; прогрев — `engine schemas extract` (батчи по 10, восемь в
  полёте, запись после каждого батча, громкий отказ при падении всех батчей).
  Документация — `docs/ENGINE_GENERATIONS.md`, «Поколение 5».

- **Нормализация акторов: `engine schemas normalize-actors`.** Второй LLM-проход по
  словарю различных акторов из `story_schemas`: модель сопоставляет варианты одного
  имени по смыслу, чего не делает префиксный дедуп `canonical_actors`. Кэш — отдельная
  таблица `actor_aliases` со своей версией промпта: правка нормализации **не** обнуляет
  кэш извлечения (иначе ~40 минут пере-извлечения). `actor_aliases_digest` входит в
  `params_hash`; пустой кэш — no-op.

- **Трендовое ревью стало выполнимым на живых релизах.** `prepare_trend_review_jobs`
  показывает модели топ-20 сюжетов тренда по membership_score: полный состав (у корней
  до 277 членов) давал промпт в 60+ тысяч символов, на котором qwen3.8-max-preview не
  отвечал ни за 75, ни за 300 секунд. Выборка одинакова в подготовке и применении кэша
  (`_review_story_ids`), иначе `input_hash` ревью никогда не попал бы в релиз. Потолок
  вызова — `TREND_REVIEW_TIMEOUT_SECONDS` (240 с), пакет идёт с ограниченной
  параллельностью (6), один сбой не роняет пакет. В `scripts/fetch-and-sync.sh` цикл
  получил `--trend-review-limit 12`: без ревью `/today` (фильтр по `confirmed`) пуст.

- **Ручка гранулярности трендов: `trend_schema_depth`.** Схемный ключ был жёстко
  `(действие, домен)` — уровень *theme* в терминах research, из-за чего
  `product launches in AI` на 37 сюжетов читался рубрикой, а не паттерном. Теперь глубина —
  параметр релиза: `2` = как было, `3` = плюс **тип** актора (`… by companies`,
  `… by countries`), то есть уровень *key event*. Третьим компонентом может быть только тип:
  по самому актору каждая группа одноакторна по построению, и `min_distinct_actors >= 2` —
  само определение тренда — не выполнится никогда.

  Глубина 3 только дробит и никогда не удаляет: строки родителей байт-в-байт равны выводу
  глубины 2, состав родителя — надмножество составов детей. Замер на 4 746 сюжетах:
  31 → 35 трендов, 2 родителя и 4 ребёнка, потеряно 0, дублей имён 0, одноакторных 0,
  полы гейта не сдвинулись. Параметр входит в `params_hash`, откат — публикация прежнего релиза.

  Третий компонент — тот фасет, который реально различает: сначала тип актора, а если он даёт
  меньше двух детей — **объект** действия. Тип актора работает только на регуляторных
  действиях (`ban`, `regulator_fine`), у `launch` и `outage` все акторы компании и делить
  нечем. Объект снимает исходную претензию: `product launches in AI` (38 сюжетов) распадается
  на `: models` (9), `: tools` (9), `: robots` (3).

  Объект берётся детерминированным лексиконом. Zero-shot GLiNER по меткам «AI model /
  software product / hardware device» проверен и **не годится** — метки ложатся почти
  случайно; отрицательный результат записан в `docs/ENGINE_GENERATIONS.md`.

- **Типизация акторов (`intelligence/actor_types.py`, extra `[actors]`).** Zero-shot GLiNER
  `gliner_small-v2.5` по меткам `company / government agency / person / country`. Даёт третий
  компонент ключа и чистит списки акторов: `Got, Just, Laid` → `EA, J2, Linkedin`. В прод-образ
  **не входит** — лимиты контейнера выстраданы под cross-encoder; типизация считается на Mac
  и едет в релиз готовой таблицей (`RC_TREND_DEPTH=3` в `scripts/fetch-and-sync.sh`).
  Нет таблицы — глубина падает до 2 с `ACTOR TYPING FALLBACK` в логе и в метриках релиза.

- **Иерархия трендов в API и UI.** Колонки `engine_trends.parent_trend_id` и
  `distinct_actors`. Список `/trends` показывает рубрики, а конкретные события — строками
  внутри карточки: «bans and restrictions in AI · by companies 4 · by countries 3».
  Акторы наконец сохраняются и видны — раньше `source_count` держал только их число, и
  выигрыш типизации был виден метрике, но не человеку.

- **IPRoyal proxy handoff.** Added a credential-free operational note for the
  IPRoyal Residential sticky-session configuration, Hermes connectivity check,
  and Reddit runtime boundary in `docs/IPROYAL_HANDOFF.md`.

- **Облака тематик на `/pulse`.** Рядом со списком сигналов — все типы с счётчиком, средним
  pulse и тремя живыми примерами заголовков: название вроде «Прочее» или «Боли» само по себе
  не объясняет, что внутри. Клик по тематике открывает `?view=links` — топ-20 **прямыми
  ссылками на посты Reddit**, отсортированными по выбранному фильтру (pulse score, Reddit score,
  комментарии, velocity). Карточная сетка остаётся доступной переключателем.
- **Блок «Новое на Reddit» на `/today`.** Двадцать свежих постов выпуска, которых нет в ленте
  чтения выше, с фильтрами по тематикам. Разнообразие держится квотами: не более трёх постов
  одной тематики, двух из одного сабреддита, и отдельный потолок на политику — у
  `policy_politics` самый высокий средний pulse (44.9 против 31–38 у остальных типов), и без
  собственного ограничения она вытесняет из блока всё.

  Тематическая ось — `signal_type`, а не `domain_ids`: фасеты считаются по тексту материала,
  а у Reddit-поста текста обычно нет, поэтому `domain_ids_json` почти всегда `other`.
  «Новое» определяется по дате публикации, а не по полю `novelty` — оно насыщено (>0.5
  у 99% сигналов) и ничего не разделяет.

### Fixed

- **`qwen3.7-flash` больше не уходит на token-plan и не получает 404.** Guard
  `_PAYG_ONLY_MODELS` проверял `endpoint == "token-plan"`, но эту строку не передаёт
  никто: обе цепочки `qwen_policy` — payg, а `pick_endpoint` возвращает `"payg"` либо
  `""`, которое вызывающие приводят к `None`. Блок был недостижим, поэтому на деплое,
  где задан только `QWEN_TOKEN_PLAN_KEY`, **каждый** review-джоб уходил на token-plan URL
  и получал 404 — а `qwen3.7-flash` теперь дефолт для `engine stories review`,
  `engine trends review`, `--review-model` и `--trend-review-model`. Проверка перенесена
  на саму модель.

- **Полный отказ провайдера больше не выглядит как «сегодня нет сигналов».**
  `analyze_posts` делала `continue` на каждой ошибке и возвращала `[]`: неверный ключ →
  все батчи 401 → «Извлечено 0 сигналов», пустой `signals.jsonl`, отчёт, история тем,
  радар и код возврата 0. Введён `QwenAllBatchesFailedError` — провал **всех** батчей
  поднимается, пропуск отдельного остаётся допустимым. Ошибки конфигурации получили
  собственный тип `QwenConfigError`: раньше отсутствующий ключ ловился веткой
  `except ValueError`, предназначенной для разбора ответа (`int("8/10")`), и
  логировался как «parse error» на каждом батче подряд.

- **Леджер и переменные окружения больше не роняют стадию.** `usage_totals` не был
  защищён, хотя `record_usage` был, а `pick_model`/`pick_endpoint` зовут его первым и
  вызывающие делают это вне своих try-блоков: `database is locked` под конкурентностью
  6–8 убивал bulk-стадию. Недоступный леджер теперь означает «расход неизвестен».
  `RC_QWEN_PAYG_FREE_TOKENS=1M` больше не роняет прогон трейсбеком из голого `int()`,
  отрицательное значение не принимается молча, а неразбираемый
  `RC_QWEN_PAYG_GRANT_START` пишет предупреждение вместо того, чтобы навсегда отключить
  90-дневную проверку; смещение таймзоны в нём больше не отбрасывается.

- **Бесплатный грант Qwen по умолчанию считается общим пулом.** Цепочка bulk при
  исчерпании гранта переходила на `qwen3.6-flash`, который дороже `qwen3.7-flash` в 8.3×
  по input и 11.5× по output, и писала в лог «подтверждённая бесплатная квота». Это
  оправдано, только если грант свой у каждой модели, чего провайдер не документирует.
  Новый `RC_QWEN_PAYG_GRANT_PER_MODEL` включает прежнее поведение явно; по умолчанию
  после исчерпания пула роутер остаётся на самой дешёвой модели по list price.

- **Восстановление снапшотов не трогает текущий день.** У `collect_sources` историческая
  дата обязана быть строго раньше текущей UTC, у `recover_snapshot_gaps` такой защиты не
  было: запуск во время идущего сбора финализировал наполовину дописанные артефакты.

- **RSS и Product Hunt фильтруют по дате до обрезания списка.** При обратном порядке лимит
  съедали самые свежие записи, и до нужного дня очередь не доходила — а у шести прямых
  фидов (BBC, Guardian, TechCrunch, Verge, Ars, Medium) `_historical_feed_url` возвращает
  URL без изменений, поэтому историческое восстановление структурно давало почти ноль.

- **Транспортная ошибка источника больше не отмывается в «пустой день».** Адаптеры
  HN/RSS/Ladder/Product Hunt ловили HTTP- и сетевые ошибки внутри себя и возвращали `[]`;
  дальше `status="ok" if cards else "empty"` давало `empty`, набор `{"ok","empty"}` —
  run `complete`, и `collect --coverage` считал такой день собранным. Ночь, где Algolia
  отдаёт 429, а фиды 503, записывалась полным днём с одним Reddit и навсегда исчезала из
  поля зрения оператора. Новый `sources/errors.py` вводит `SourceTransportError` и счётчик
  попыток: отказ поднимается, только если не удался **ни один** запрос, поэтому частичный
  отказ остаётся `ok`, а честно тихие сутки — `empty`.

- **`collect --historical-date` больше не уничтожает существующие артефакты.** Запись шла
  через `mode="w"` без единой проверки: если за восстанавливаемую дату уже лежал настоящий
  сбор, которому не хватило только финалайзера, ретроспективный запрос затирал его тем
  немногим, что отдаёт провайдер (530 items → 12), а при отказе фетча — усекал до нуля.
  Теперь `write_posts_jsonl` пишет атомарно (`tmp` + `os.replace`), а восстановление
  оставляет непустой артефакт нетронутым со статусом `skipped`, пока не передан явный
  `--overwrite-artifacts`. Порядок шагов рунбука держит код, а не только текст.

- **`snapshot_date` нормализуется на входе.** `strptime` принимает `2026-8-3`, но даты
  хранятся с ведущими нулями, а coverage сравнивает их как текст: `'2026-08-04' >=
  '2026-8-3'` ложно, поэтому `BETWEEN` не матчил ничего и полностью собранная неделя
  показывалась как `missing`, а `--from-snapshots --date 2026-8-3` создавал фантомный
  каталог, невидимый любому каноническому запросу.

- **`schema_v3` применяет то же вето зоны, что и `schema_v2`.** Резолвер импортировал
  `has_out_of_scope_domain`, но не `is_out_of_scope`, поэтому доменное вето ловило
  спортивное регулирование, а заголовочное — ничего: «Chelsea banned from signing players
  after Premier League probe» с доменом `business_markets` становился трендом `bans and
  restrictions in business`, хотя v2 его отвергал. Замер по двум локальным релизам — 66 и
  109 сюжетов (1.9 % и 2.3 %), которые v3 пускал мимо v2.

- **Агрегаты тренда пересчитываются после отсева ревью.** Обновлялся только `story_count`;
  `distinct_actors`, `source_count`, `first_seen` и `last_seen` проносились от прежнего
  состава. Тренд с четырьмя выжившими сюжетами одной компании публиковался `confirmed` со
  списком из полутора десятков акторов, у которых не осталось ни одного сюжета, и порог
  `min_distinct_actors ≥ 2` — само определение тренда — после ревью не перепроверялся.
  Теперь пересчитывается и порог применяется заново. У методов без карты акторов
  (`embedding_v2`, графовый) actor-поля не трогаются: обнулять их было бы хуже.

- **`source_scope` считается по охвату провайдеров.** Стояло множество самих
  `source_count`, и `len(providers) > 1` означало «у сюжетов *разные* счётчики»: тренд из
  сюжетов по два провайдера каждый получал `single_source`, а одно-провайдерный со
  счётчиками 1 и 2 — `cross_source`. Дефект предшествует v3 (`e80b743`).

- **Сортировка News и Today идёт по времени, а не по написанию даты.** `published_at` не
  нормализован по формату: Reddit и HN отдают ISO-8601, RSS — RFC 2822 (в боевом релизе
  3219 против 1414 строк). Пока дата была пятым ключом, сравнение строк ни на что не
  влияло; когда она стала первичным ключом `sort=fresh`, лексикографика начала сортировать
  по названию дня недели — `W > T > S > M > F > "2"`. Новый `api/dates.py` приводит оба
  формата к одному ключу, недатированные материалы уходят в конец в обоих направлениях, а
  зарегистрированный Jinja-фильтр `published_date` показывает день вместо трёх разных
  отметок времени в одной колонке.

### Changed

- **Эксперимент bounded components.** В Story Engine добавлена выключенная по умолчанию,
  release-scoped ветка для проверки недослияния: она допускает только review-пары с явными
  lexical/entity/time-признаками, исключает Show HN и ограничивает связную компоненту четырьмя
  items. На изолированном broad-релизе она проходит структурные полы, но не может быть
  включена или задеплоена до человеческого Golden Set. Экспорт Golden Set теперь резервирует
  50% пар для `auto_merge`, 40% для `review` и 10% для `reject`, чтобы новая ветка была
  действительно проверена на precision и recall. Для групп добавлена явная человеческая метка
  `valid_group`: корректная группа больше не вынужденно помечается как `low_signal`. Смешанный
  аудит (119 пар и 10 групп автора, 1 пара и 20 групп ассистента) отклонил Candidate v1:
  precision 0.70, recall 0.56, overmerge rate 0.40. Это не полностью human Golden Set; ветка
  остаётся выключенной, production не менялся.
- **Guards для bounded-components.** Opt-in ветка теперь не поднимает в auto-merge пару
  разговорных Reddit-вопросов с одинаковой формой (`What` / `How` / `CMV`), а числовой guard
  сравнивает только сопоставимые величины и понимает `$5.5B` = `$5.5bn`. Candidate v2 на том
  же frozen broad проходит structural floors (compression 0.8287; 100.7 multi и 48.6
  cross-source на 1k), но по смешанным 120 pair-labels даёт precision 0.8571 и recall 0.4800.
  Независимая human QA девяти спорных пар исправила четыре метки и подняла recall до 0.5070,
  но оставила precision 0.8571. Все 30 групп проверены (10 human, 20 `assistant_review`):
  12 из 30 — overmerge, rate 0.40. Ветка не проходит gate, не включена по умолчанию и не
  влияет на production.
- **GLiNER zero-shot POC.** Локальный read-only запуск `gliner_small-v2.5` на 177 материалах
  Golden Set подтвердил качественные named-entity spans, но не улучшил decision signal:
  phrase-aware anchor coverage same-story 74.65% против 90.14% у текущих facets, precision
  80.30% против 81.01%. Зависимость не добавлена, Engine и production не изменены.
- **CrossEncoder pair-adjudication POC.** Готовый `ms-marco-MiniLM-L6-v2` заметно улучшил
  ранжирование 120 frozen Golden pairs (AUC 0.9138 против 0.7617), но при пороге, выбранном на
  отдельном dev, дал на неизменённом test precision 0.9167 и recall 0.6286. Это ниже
  production-floor precision 0.95; зависимости, Engine и prod не изменены. Следующий допустимый
  шаг — новый human holdout и только затем изолированный POC вместе с hard-conflict guards.
- **Hybrid CrossEncoder + hard-guard POC.** Консервативный слой не может переопределять
  deterministic rejects/conflicts и дал на test precision 0.9545, но recall только 0.6000
  (цель ≥ 0.75); на всех 120 диагностических pair-labels — P 0.9600, R 0.6761. Новые группы
  человеком не проверены, поэтому StoryRelease, зависимости и production-pointer не изменены.
- **Ручной режим production.** Ночные jobs collection/finalize/Engine на VPS отключены;
  публикация и каждый запуск остаются ручными до согласования нового интервала. Аудит Plan v4
  на изолированном релизе задокументирован в `docs/QUALITY_GATES.md`: текущий retrieval не
  проходит три пола полноты, а его оптимистичный потолок возвращает overmerge, поэтому новый
  Engine не развёртывается и production-pointer не передвигается.
- **Production collection completeness.** A narrowed `collect --from-snapshots
  --sources ...` can no longer mark `broad` or `ai-native` complete while it
  silently omits configured snapshots. Missing required source clusters now make
  the immutable Engine input `partial` before it reaches quality floors.
- **Story and trend quality.** Routine discussion threads and tennis scorelines
  are excluded from story merges; trend names drop publisher tokens and repeated
  unigrams. Dense candidate retrieval can be configured to retain pairs above
  its calibrated auto-merge floor instead of losing them solely to top-K rank.

- **`DEFAULT_TREND_METHOD` выровнен на `embedding_v2`** — тот же метод, что считает
  ночной прогон. Библиотека умалчивала `story_graph_v1`, и расхождение было не
  косметическим: на одном story-релизе (4 957 items) граф-метод давал 6 трендов
  с 5 негодными именами и одним дублем и **ронял полы качества**, которые прод-путь
  проходит (109 трендов, 0 плохих имён). `story_graph_v1` остаётся явным выбором
  и фолбэком, когда model2vec недоступен.

### Changed

- **Модель под сложность задачи, а не наоборот.** Профиль нагрузки снят с боевого
  движка (`trend_engine.db` на VPS, 5 августа): извлечение схем ~1 020 вызовов на
  10 195 заголовков, нормализация акторов ~41, ревью пар сюжетов 629, трендовое ревью
  171. Массовые стадии — это разбор одной строки, а bulk-цепочка открывалась
  `qwen3-235b-a22b-instruct-2507`: 235B на тысяче вызовов «разбери заголовок» выжигал
  бесплатный грант за один прогон. Теперь bulk идёт с `qwen3.7-flash` ($0.03/$0.13 за
  1M против $2/$6 у max — в шестьдесят раз дешевле на входе), 235B из цепочки убрана.

- **`qwen3.8-max` вместо `qwen3.8-max-preview`.** Модель вышла из превью 3 августа
  2026 и появилась на pay-as-you-go — проверено `GET /v1/models` и живым вызовом на
  обоих ключах. Стоит $2/$6 против $2.50/$7.50 у прежнего флагмана `qwen3.7-max`,
  то есть дешевле и сильнее. `_TOKEN_PLAN_ONLY_MODELS` сузился до самого превью-
  идентификатора: держать там стабильную модель значило гнать её мимо бесплатного
  гранта на платную подписку. Смена модели ревью обнуляет 171 накопленное решение в
  `llm_reviews` — это осознанная цена перехода, ревью пересоберётся за один прогон.

- **Ревью выбирает эндпоинт, не меняя модель.** Модель входит в ключ кэша
  `llm_reviews` и маршрутизации не подлежит, а эндпоинт в ключ не входит — новый
  `qwen_policy.pick_endpoint` берёт ту же модель там, где сейчас дешевле.

- **Бесплатный грант идёт впереди скидочной подписки.** Первая версия роутера в окне
  17:00–03:00 МСК ставила подписку первой «потому что скидка». Неверно по двум
  независимым причинам: ноль дешевле любого процента, а грант ещё и перегорает через
  90 дней безвозвратно, тогда как подписка возобновляется каждый месяц — тратить надо
  сначала то, что иначе пропадёт. Окно теперь решает единственный вопрос: что брать,
  когда гранта не осталось. Заодно в `docs/QWEN_ROUTING.md` разведены три разные
  «скидки 14:00–00:00 UTC»: заявленная на pay-as-you-go (источник — твит, официальный
  прайс молчит), заявленная на подписку (вторичный блог) и документированная у Qoder —
  последняя относится к кредитам стороннего продукта и прайсом API не является, хотя
  выглядит как он.

### Fixed

- **Подтверждённый тренд терял всё, что не попало в выборку ревью.** Промпт ревью режется
  до двадцати сильнейших сюжетов, и `story_ids` в ответе физически не может содержать
  больше двадцати — а `apply_cached_trend_reviews` фильтровала по нему **весь** состав.
  Корень на 277 сюжетов после подтверждения становился трендом на 20: падал `story_count`
  (ключ сортировки на /trends, /today и radar), сюжеты вне выборки теряли привязку к
  тренду, а пол `trends_max_story_share ≤ 10 %` переставал быть способен сработать —
  20 из 9 000 это 0.2 % при любом содержании. Вердикт ревью теперь применяется только к
  показанным двадцати: непоказанные остаются, потому что модель о них не высказывалась,
  а молчание не равно отказу.

- **`schema_v3` молча публиковал глубину 2 под именем глубины 3.** У метода стояла своя
  копия ветки деградации, и в отличие от `schema_v2` она не писала `ACTOR TYPING
  FALLBACK`. Хуже того, ночной блок `RC_TREND_METHOD=schema_v3` звал `--trend-depth 3`
  безусловно, а таблицу типов строил только блок под другим флагом (`RC_TREND_DEPTH`,
  по умолчанию выключен) — то есть прогон уходил на глубину 2 каждую ночь. Ветка теперь
  одна на оба метода (`_resolve_actor_typing`), а ночной блок просит глубину 3 только
  когда таблица действительно построена.

- **Акторы и дочерние события не доходили до экрана.** `distinct_actors` и `children`
  собирались в `TrendOut`, но страница тренда не рисовала ни того ни другого, а акторы
  не показывались нигде, кроме строки ребёнка: нормализация акторов отдельным LLM-проходом
  была видна метрике и невидна человеку, а drill-down от рубрики к событию существовал
  только в комментарии к API. Добавлены секции Actors и Key events на странице тренда и
  строка акторов в списке.

- **Счётчики на radar считали тренды, которых на странице нет.** `candidate_count` и
  `confirmed_count` вычислялись до отбрасывания детей, а на полки идут только корни.

- **Расход прошлого бесплатного гранта навсегда закрывал модель.** Грант живёт 90 дней,
  а леджер копится вечно, и `usage_totals` суммировала по всей истории. Появилась
  `RC_QWEN_PAYG_GRANT_START`: расход считается от даты активации, после 90 дней роутер
  уходит на подписку вместо того, чтобы обещать бесплатное. Дата не задана — поведение
  прежнее. Заодно сбой записи в леджер перестал быть полностью беззвучным (недосчитанный
  расход роутер читает как свободную квоту), а соединение ждёт конкурента до 30 с:
  ночной цикл пишет из нескольких контейнеров при параллельности 6–8.

- **Ночной прогон собирал сюжеты без стадии cross-encoder.** `engine cycle` вызывался без
  `--cross-encoder`, серая зона оставалась неразобранной, и полы полноты не брались: замер на
  прогоне 3 августа дал `stories_multi_per_1k` 50.6 при поле 65, `cross_source_per_1k` 20.9
  при 27 и `compression` 0.931 при потолке 0.90. Публикация при этом шла с `--force`, поэтому
  гейт молчал, а `broad` жил с недособранными сюжетами. С включённой стадией на том же
  корпусе: 84.9 / 39.2 / 0.857 — все три пола взяты.

  Заодно `engine stories propose` получил `--cross-encoder`: стадия, от которой зависят эти
  полы, была доступна только внутри полного цикла, и проверить её отдельно было нечем.

- **Спортивное регулирование доезжало до трендов.** Вето `_SPORTS_MARKERS` смотрит только
  заголовок, а «Chelsea fined £10m for breaching agent rules» и «avoid points deduction»
  спортивной лексики не содержат — в схемный слой проходило 26 спортивных сюжетов, из них 10
  с действием `regulator_fine`, и оштрафованный футбольный клуб попал в
  `regulatory fines in business by companies`. Добавлено вето по **домену**: таксономия эти
  материалы уже разметила, и спрашивать её дешевле, чем дописывать названия клубов в
  регулярку. Домен проверяется целиком, а не ведущий: разметка была
  `["business_markets", "sports"]`, а `_story_domain` берёт первый.

- **Издания протекали в акторов тренда.** Отсев `_PUBLISHER_TOKENS` жил внутри регулярочного
  `extract_actor`, а типизированный путь его обходил, поэтому
  `regulatory fines in business by companies` собрал «Financial Times» и «Fox Business».
  Проверка вынесена в `is_publisher` и применена к обоим путям. Заголовки приходят с суффиксом
  источника «— Fox Business», который GLiNER читает как сущность, — токен добавлен в список.
  Поймано ревизией по содержанию: все структурные метрики при этом были зелёными.

- **Три из восьми ключей `_DOMAIN_LABELS` не были доменами.** `world` и `science_climate` —
  идентификаторы **рубрик** (`taxonomy._RUBRICS`), `surveillance_privacy` — опечатка вместо
  `security_privacy`. Пять доменов из одиннадцати уходили в фолбэк и назывались
  «layoffs in climate energy infrastructure».

- **`run_engine_cycle` не передавал `review_model` в трендовый релиз.** Кэш ревью искался под
  дефолтом `qwen3.8-max-preview`, а писался под `trend_review_model`; совпадало только потому,
  что дефолты одинаковы — с `--trend-review-model X` кэш переставал попадать.

- **Порог ребра до медоида обесточивал весь слой ревью.** `_valid_group_against_medoid`
  требовал `score >= 0.72` жёстко зашитым числом — выше всей серой зоны (0.45–0.65).
  Пара, поднятая Qwen-ревью или merge-моделью до `auto_merge`, всё равно отбрасывалась
  при сборке групп, поэтому ни разметка, ни обучение модели не влияли на результат.
  Теперь это параметр релиза `medoid_min_score` (входит в `params_hash`) с откалиброванным
  дефолтом 0.55. Замер на 7-дневном broad: кросс-источниковые истории выросли с 12 до 29
  на 1000 items при нулевом переслиянии; порог 0.72 не давал ничего взамен.

### Removed

- **Legacy-слой UI снят целиком.** `api/dashboard.py` (863 строки HTML конкатенацией строк),
  маршруты `/legacy/*`, шаблоны `today.html`, `radar.html`, `story.html`, `explore.html` и
  партиал `story_card.html`. Эти страницы читали `compass.db` напрямую, в обход
  опубликованного релиза, и были достижимы только при отсутствии публикации — то есть
  показывали картину, не прошедшую гейты качества. Теперь на их месте честная заглушка
  «Публикации нет, запустите `engine cycle`». `/explore` редиректит на `/news` c сохранением
  строки запроса, `/dashboard` — на `/today`.
- Вместе с ними удалены осиротевшие билдеры: `briefing_to_view`, `story_to_detail_view`,
  `RadarPageView` (`view_models.py`) и `build_theme_clouds`, `build_freshness_line`,
  `build_trend_strength`, `build_raw_popular_items` (`query_service.py`). Карточка истории
  строится теперь в одном месте вместо трёх — заодно исчезла причина пустого блока «Метрики».
- **Мёртвый CSS**: 74 правила, обслуживавших удалённые страницы. Проверено, что ни один
  используемый шаблонами класс не потерян; интерполируемые модификаторы
  (`status-{{ }}`, `scope-{{ }}`, `signal-type-{{ }}`, `pipeline-stage-{{ }}`) сохранены.

### Changed

- **Ленты `/today` рендерятся на сервере.** `/ui/today-changes` и `/ui/today-reading` отдают
  HTML-фрагменты вместо JSON, карточки вынесены в `components/today_change_card.html` и
  `components/today_reading_item.html`. Разметка перестала существовать вторым экземпляром
  в императивном JS: `today_reading.js` ужался с 236 строк до 114 и теперь только вставляет
  полученный HTML. Нумерация догруженных материалов продолжает серверную страницу.

### Fixed

- **Возвращено покрытие `signals.render_trend_radar`**: тесты удалили вместе с тестами
  снятого `api/dashboard.py`, хотя сама функция осталась живой и вызывается командами
  `radar` и `report`. Проверки восстановлены в `tests/test_trend_radar_render.py`.

### Added

- **Radar candidate preview and rubric filtering**: evaluated releases now expose every trend
  candidate in the analytical Radar with explicit pending/Qwen/confirmed labels. Rubric tabs derive
  membership from evidence story domains, so an old broad trend label no longer appears unchanged in
  every category. A publication containing only pending rows is also labelled as candidates rather
  than as confirmed production trends. Production publication remains manual and unchanged.

- **Операционный completion contract**: `collect --from-snapshots` превращает уже собранные
  JSONL в один factual raw run без сети и LLM. Добавлены `docs/COLLECTION_LIFECYCLE.md`,
  version-controlled host-cron и безопасный Reddit-only Mac→VPS handoff: локальный
  `compass.db` больше не может перезаписать VPS corpus.
- **Run journal**: `/runs` раскрывает для каждого запуска source health, Frozen Data Release,
  Stories, Trends/Qwen, quality gate и current publication. Счётчик материалов берётся из
  observations, а не из изменяемой даты item.
- **Reliable Today feeds**: тяжёлый reading selection вынесен из async event loop, кэшируется по
  immutable publication и грузится постранично; Engine SQLite использует WAL + busy timeout.
  Первые десять ссылок теперь server-rendered, а статический JS получает cache-busting version и
  лишь догружает остаток: Today не остаётся на «Подбираю…» при stale asset или сбое вторичного
  запроса. В «Что изменилось» допускаются только подтверждённые trends с пригодным именем;
  сырой кластер остаётся диагностикой Engine.
- **Embedding cache correctness**: повторное использование уже сохранённых vector hashes теперь
  только создаёт release-specific refs и не вызывает `model2vec.encode([])`. Это устраняет
  production fallback `need at least one array to concatenate` при повторном Engine cycle.
- **Bounded candidate retrieval**: sparse token/entity buckets и общий candidate budget больше не
  разворачивают полный `N×N` на семидневном корпусе. Узкие URL/near-duplicate anchors и top-K
  dense neighbours сохраняют важные пары, а частотные `AI`/`OpenAI`/`US` не могут съесть память
  или весь ночной цикл.
- **Same-cycle reviewed Engine**: валидные bounded Qwen pair reviews создают второй immutable
  StoryRelease в том же cycle; bounded trend review materializes финальный TrendRelease. Qwen
  pair labels используют тот же canonical pair key, что обучающий merge scorer.
- **Bounded Qwen availability**: у pair/trend Engine-review теперь отдельный 75-second timeout.
  Недоступность одного ответа фиксируется как диагностика конкретного attempt и не удерживает
  frozen cycle, quality gate или предыдущую опубликованную версию интерфейса.
- **README How it works + secret hygiene**: README теперь описывает полный путь
  `sources → compass.db → immutable DataRelease → facets/stories/trends → RadarPublication → UI`
  со ссылками на алгоритмы, БД и quality gates. Добавлен repo-local `scripts/secret-scan` по
  образцу operational scanner из соседних проектов; tracked production IP/sslip/vendor route
  details заменены на placeholders и gitignored env-переменные.

- **Today: ежедневная лента и рабочие рубрики**: `/today` теперь показывает до 20 материалов
  для ежедневного чтения с прямыми безопасными ссылками. Отбор учитывает свежесть, профиль
  РБК/книги, качество источника и ограниченные within-channel engagement-сигналы, а квоты
  сдерживают повторы одной story, одного провайдера, одной рубрики и Reddit. Тематические срезы
  стали явными переходами в фильтрованные Stories, а не декоративными чипами.
  Лента и карточки «Что изменилось» загружаются отдельными безопасными JSON-запросами, поэтому
  основной HTML Today остаётся компактным при ограничениях reverse proxy.
- **Продукт оживлён на проде (2026-07-30)**: `engine cycle` (embedding_v2 + model2vec)
  прогнан на свежих данных 24–30, **все полы качества зелёные** (overmerge 0, тренды без
  голых имён/дублей, таксономия сбалансирована, Pulse other 13.9%), канал `broad`
  опубликован — `/today` больше не preview. Reddit собран с локального approved route и смерджен в corpus
  точечно по provider (без перезаписи VPS-БД; `scripts/ingest_snapshot_day.py` /
  `scripts/merge_reddit_corpus.py`). Ночной cron `16:00 UTC` запускает цикл с Qwen-дообучением серой
  зоны (`--review-limit 80`). Эталон качества: `config/quality_baselines.json`.
- **Quality gates (допустимый уровень качества + защита от регрессий)**: `intelligence/quality.py`
  считает метрики по immutable-релизу (overmerge, баланс рубрик новой таксономии, качество
  имён трендов, доля `other` в Pulse) и проверяет их против абсолютных полов `QUALITY_FLOORS`
  и baseline-снимка. CLI `engine quality report|check|snapshot` (`check` выходит с ненулевым
  кодом при провале пола или регрессии → основа для CI/отката). `tests/test_quality.py`
  кодирует те же полы как синтетические CI-инварианты. Эталон: `config/quality_baselines.json`.
  Документация: `docs/QUALITY_GATES.md`.
- **Trends v2 на проде через model2vec (torch-free)**: `embeddings.py` получил бэкенд
  `model2vec` (модели `minishlab/*`, без torch/sentence-transformers); новый optional-extra
  `embed`. `engine cycle` по умолчанию кэширует эмбеддинги и строит тренды методом
  `embedding_v2` (c-TF-IDF имена, дедуп, производная); при отсутствии пакета/сети —
  graceful fallback на `story_graph_v1`. Collector-образ ставит `.[embed]`.
- **Learned story merge scoring (Фаза 3)**: dependency-light логистическая регрессия
  (`intelligence/story_scoring.py`, numpy) поверх `features_json`. Детерминированная
  авто-разметка `engine label auto` (без человека), обучение `engine label train`
  с калибровкой порога под precision ≥ 0.95; веса и хэш модели сохраняются в
  `metrics_json.merge_model` (воспроизводимо). Человеческие метки имеют приоритет.
  Модель решает только серую зону — жёсткие правила (URL-match, hard conflicts)
  остались детерминированными. Единый источник дефолтов `DEFAULT_STORY_PARAMS`.
- **Trends v2 `embedding_v2` (Фаза 5)**: новый слой трендов (`intelligence/trend_discovery.py`) —
  кластеризация векторов историй, имена через c-TF-IDF (вместо «Паттерн: fall»),
  дедупликация по пересечению множеств историй (Jaccard ≥ 0.5), обязательная производная
  по дням, confidence с компонентами (volume / cross_source / day_spread), обязательный
  `source_scope`. Выбор метода: `engine trends propose --method embedding_v2|story_graph_v1`.
- **Perspective gap (Фаза 4)**: `perspective_gap` теперь реально вычисляется из pulse_score
  и mainstream-покрытия связанной истории; guard `perspective_gap_available` не считает разрыв
  на несбалансированных релизах (флаг в metrics signal_release). Расширена классификация
  сигналов (`complaint`, `product_request`) для снижения доли `other`.
- **Таксономия и квоты (Фаза 6)**: из `ai_technology` убраны generic-слова
  (model/product/startup/code/software/developer/agent) и source_hints technology/tech/hackernews —
  источник больше не назначает рубрику сам по себе. Двухуровневый рубрикатор `RUBRICS`
  (8 верхних рубрик), `apply_reddit_quota` (Reddit ≤ 30% в блоке «Мир»), детектор рутины
  `is_routine_beat` — рутина остаётся в /news, но исключается из story/trend-слоёв.
- **Обратная связь (Фаза 7)**: one-click endpoint `POST /ui/engine/feedback`
  (полезно/мусор) пишет в `engine_labels`, пополняя golden set без ручной разметки.
- **GUI**: навигационная метка `Pulse` переименована в `Reddit Pulse`; на карточках
  Stories/Trends добавлен бейдж `source_scope` (🔗 Reddit + СМИ / 🔴 только Reddit / 📰 только СМИ).
- **Документация**: `docs/DATA_FLOW_DIAGRAMS.md` — Mermaid-схемы потока данных
  Reddit → stories → trends → Reddit Pulse → публикации.
- **Versioned Story/Trend Engine**: новая `trend_engine.db` с frozen Data Releases,
  независимыми Facet/Story/Trend attempts, checksum verification и SQLite immutability triggers.
- **Hybrid Story Engine**: bounded top-K retrieval по URL/title/entities/optional E5,
  event conflicts, stable-landing URL guard, RFC/ISO date normalization, constrained
  agglomeration, stable story IDs и merge/split provenance.
- **Trend Engine**: pattern graph только поверх разных stories, минимум три события/два дня,
  specific-pattern guard (pain/theme alone cannot form a trend), source scope, mandatory Qwen
  confirmation gate, history status и lifecycle без искусственной динамики.
- **Golden Set и release gates**: stratified export/import, precision/recall/overmerge,
  cross-source recall, evidence coverage и Qwen-budget перед production publish.
- **Engine control plane**: `engine release/facets/embeddings/stories/trends/golden/publish/rollback`,
  `/engine`, `/api/v2/engine/*`, publication-backed Radar и короткий Today.
- **Published analysis layers**: separate News inbox (`/news`, `/api/v2/news`), Stories workspace
  (`/stories`, `/api/v2/engine/stories`), Trends workspace (`/trends`, `/api/v2/engine/trends`)
  and Project Lens (`/projects/{project_id}`, `/api/v2/projects/{project_id}/lens`) over the same
  immutable RadarPublication.
- **GUI drill-down**: published Story detail (`/stories/{story_id}`,
  `/api/v2/engine/stories/{story_id}`), Trend detail (`/trends/{trend_id}`,
  `/api/v2/engine/trends/{trend_id}`) and Radar cockpit links across News/Stories/Trends/Projects.
- **Shadow publication UI**: `/radar`, News, Stories, Trends and Project Lens preserve
  `channel`/`publication_id`, so experimental Engine publications can be reviewed without
  promoting them to the default `broad` channel.
- **Strict Qwen adjudication**: pair/trend Pydantic schemas, evidence validation, prompt/model/input
  cache; невалидный ответ не влияет на clustering.
- **Engine lab retrieval v2.1**: dependency-light `lexical-hash-v1` embeddings for local top-K
  candidate retrieval, CLI tuning flags for story/trend graph thresholds, bounded trend feature
  pair generation, ordered trend topic phrases, recurring-thread guards and HuggingFace model
  release URL merge rule.
- **Story Engine A/B experiments**: `engine experiments compare` runs baseline, MinHash/SimHash
  near-duplicate, guarded semantic-dedup and combined variants on the same frozen FacetRelease,
  returning release IDs, metric deltas, merge reasons and cross-source samples without publishing.
- **Reddit Pulse hardening**: `signal_releases` store method, params hash, metrics and git SHA;
  `reddit-pulse propose` can link Reddit signals to an existing StoryRelease and compute mainstream
  coverage from frozen rows without running a new network collection.
- **Engine preview fallback**: News, Stories, Trends, Radar and Project Lens show the latest
  evaluated Engine release when a channel has no `RadarPublication`; UI/API mark it as `preview`
  so it cannot be confused with production.
- **Engine diagnostics workflow**: `engine diagnose` reports release coverage, compression,
  candidate decisions, undermerge examples and next commands; `engine stories candidates` exports
  scored pair candidates on 50/100/300-item frozen slices without creating a StoryRelease.
- **DataRelease source-health gate**: sources with `expected_min_items` no longer pass as `ok`
  when empty/degraded; `broad`/`ai-native` releases with an empty expected voices cluster become
  `partial` and `engine diagnose` surfaces the issue explicitly.
- **Story Engine v2.3 conservative merge gate**: dense/E5 similarity alone can no longer auto-merge
  stories; auto-merge now requires event provenance anchors, and large same-provider groups without
  shared event URLs are blocked.
- **Active-learning labels**: `engine label active --story-release STORY_ID --target N` prioritizes
  review/near-threshold story pairs and stores version-scoped manual labels for the Golden Set.
- **Offline corpus repair**: `db repair --source-db data/compass.db --output-dir data/snapshots`
  migrates old SQLite projections to the current item schema, backfills Reddit
  `discussion_url`/`target_url`/domains from local JSONL snapshots and rebuilds `source_health`
  from existing observations without network collection or full legacy rebuild.
- **Engine lab performance guard**: limited `stories candidates/propose` runs now use token/URL
  indexes and selective embedding loading, so 50/100/300-item lab slices do not deserialize vectors
  or fuzzy-match against the full frozen release.
- **Collector-to-Trends documentation**: added text diagrams and developer checklist covering
  source adapters, `compass.db`, immutable Data Releases, facets, stories, trends, publications and
  GUI lineage.
- **Story Engine v2.2 cross-source guard**: conservative source-independent event-title/entity
  auto-merge reduces local full-release review pairs from 552 to 120 and improves cross-source
  stories from 11 to 14 without merging generic topic posts in tests.
- **Cluster Lab sandbox**: отдельный `cluster_lab.db` для immutable data releases,
  experiments, story proposals и trend proposals без mutation production `stories`.
  CLI: `lab release create/list`, `lab experiment create`, `lab propose`, `lab compare/eval`.
- **Cluster Lab trend fallback**: trend proposals строятся не только из `item_signals`,
  но и из entity/title-topic buckets, чтобы sandbox работал на неполных/legacy DB.
- **Story/trend clustering research notes**: documented story identification vs topic modeling,
  entity-aware sparse+dense representation stack, eval metrics и roadmap.
- **Честные метрики clustering** (RADAR_CLUSTERING_IMPROVEMENT_TASK):
  `candidate_story_count`, `single_item_story_count`, `multi_item_story_count`,
  `cross_source_story_count`, `radar_ready_story_count`, `analyzed_coverage_ratio`,
  `compression_ratio` в RunSummary. Radar KPI показывает все 6 метрик.
  Warning при compression > 65% и analysis coverage < 95%.
- **normalize_title v2**: `Opinion | Real title - NYT` → использует правую часть;
  trailing publisher suffix (`- The New York Times`) удаляется; 16 provider aliases.
- **Generic/low-signal guards**: `is_generic_title()`, `is_low_signal_title()`.
  Generic titles (opinion, tech life, newsletter) не склеиваются по title-only.
  URL-based story_id для generic/low-signal материалов.
- **Deterministic canonical key**: `extract_ordered_tokens()` вместо `set→list`.
  `cluster_items()` детерминирован по story_ids.
- **Conservative clustering**: короткие заголовки (<4 tokens) без entity overlap
  требуют similarity ≥ 0.85 (было 0.72).
- **Radar-ready filtering**: `is_radar_ready()` — single-source материалы
  не доминируют в top аналитике, доступны в Explore.
- **Дизайн-система v2** (icreon.com palette через VPS + Wayback Machine):
  deep blue → purple → magenta gradient, Outfit (geometric sans-serif),
  ambient radial gradients, kinetic motion (reveal-up stagger, pulse-glow,
  spring easing), gradient text clip на KPI/scores, backdrop-filter nav.
  Обе темы: dark (deep navy #06080f) + light (#f6f8fa).
- **Broad Radar / trendwatching core**: стабильная taxonomy из 12 `domain_id`
  (`ai_technology`, `labor_career`, `business_markets`, `society_politics`,
  `world_geopolitics`, `culture_media`, `sports`, `science_health_education`,
  `finance_consumer`, `climate_energy_infrastructure`, `security_privacy`, `other`).
- **Default `broad` profile** (`config/profiles/broad.json`): широкие Reddit packs,
  broad keywords и goal profiles для книги, РБК и business signal.
- **SQLite schema v3**: новые поля `domain_ids`, `trend_id`, `lifecycle`,
  `project_scores`, `discussion_url`, `target_url`, `dedupe_group_id`, `evidence_refs`.
- **Radar workspace**: category tabs, category × source-cluster matrix, trend shelves,
  Broad/AI-native mode switcher, source-section coverage и domain labels.
- **Radar drill-down**: pain points, stable themes и emerging theme chips открывают
  `/explore` с сохранёнными `date/profile`; `/api/v2/stories` и `/api/v2/trends`
  поддерживают фильтры `pain` и `candidate_theme`.
- **API v2 additions**: `/api/v2/domains`, `/api/v2/radar/{date}`,
  `/api/v2/trends`, `/api/v2/trends/{trend_id}`,
  `/api/v2/projects/{project_id}/radar`.
- **Дизайн-система** (kinetic motion-first, dark tech aesthetic по описанию Awwwards
  icreon-digital-velocity): обе темы (dark default + light toggle с persistence),
  типографика Space Grotesk / Inter / JetBrains Mono, CSS design tokens, scroll-reveal,
  hover lift + glow, count-up KPI, ambient background с radial gradients.
- **Разделение Radar и Today**: `/today` — компактный briefing (3-5 изменений, что
  прочитать, кнопка в Radar); `/runs/{date}/radar` — полный аналитический workspace
  (KPI, LLM-анализ, relevance Книга/РБК, облака, сила трендов, мега-сюжеты, raw
  popularity, охват источников); `/radar` — redirect на последний Radar.
- **Legacy routes**: `/legacy/dashboard`, `/legacy/runs/{date}/radar` — старые
  renderers на один переходный релиз.
- **Intelligence layer** (`src/reddit_compass/intelligence/`): source-agnostic domain models
  (ContentItem, Story, Briefing), SQLite v2 migrations, story clustering (rapidfuzz),
  ranking (goal relevance, cross-source coverage, momentum, novelty, evidence quality),
  deterministic briefing.
- **Unified run** (`reddit-compass run`): единая команда для сбора из указанных источников.
  Флаги: `--sources reddit,hn,rss`, `--profile`, `--analyze`, `--allow-partial`.
- **Source registry** (`sources/registry.py`): 22 источника с метаданными (provider, cluster,
  access, required env). NYT API и WSJ: `enabled_by_default=False`.
- **NYT adapter** (`sources/nytimes.py`): Top Stories API + Article Search API.
  Требует `NYT_API_KEY`. Без ключа: status `not_configured`.
- **Web UI** (Jinja2): `/today` (briefing), `/stories/{id}` (detail + timeline),
  `/explore` (search/filter/pagination), `/runs` (history). Research actions с CSRF.
  Security headers: CSP, X-Content-Type-Options, Referrer-Policy.
- **API v2** (`/api/v2/`): briefings, stories, runs, source-health, PATCH research-state.
  Pydantic schemas. v1 остаётся совместимым.
- **LLM validation** (`llm_schemas.py`): Pydantic schemas для валидации ответов Qwen.
  Stratified selection (70% clusters, 20% global, 10% exploration). Retry policy.
- **Profile schema v2**: goals, themes в `config/profiles/ai-native.json`.
  Совместимость с v1.
- **CLI: `db rebuild`**: перестройка SQLite v2 из snapshots. Идемпотентен,
  research_state переживает rebuild.

### Fixed

- **Secret-scan cannot be bypassed by an `https` prefix**: placeholder recognition no longer
  mistakes the `test` substring in `https` for a fake value. It now detects public-IP/`sslip`
  endpoints, Basic/Bearer credentials, URL credentials and actual proxy assignments, while a
  whole-tree audit avoids reading untracked local `.env*` files.
- **Pre-commit runs on the supported Git 2.19 toolchain**: pin to the compatible 4.5 line,
  restoring the required scanner, format, private-key and detect-secrets hooks instead of failing
  before any hook runs on unsupported `git ls-files --deduplicate`.
- **`/today` показывал пустой legacy briefing при наличии Engine-релиза**: если для `broad`
  ещё нет `RadarPublication`, Today теперь использует тот же latest evaluated preview fallback,
  что `/trends` и Radar API. Production publish также признаёт новые `Engine quality floors`,
  а не только legacy `metrics.publication_gate`, поэтому зелёный ночной `engine quality check`
  может быть опубликован в `broad`.
- **Today стал кликабельным и объяснимым**: карточки trend-кандидатов открывают
  `/trends/{trend_id}`, технические `partial`/`insufficient_history` объясняются в статусной
  панели, а верх экрана показывает KPI выпуска, тематический срез и быстрые переходы в
  News/Stories/Trends/Pulse/Radar.
- **Trend detail больше не раздувается на больших кластерах**: `/trends/{trend_id}` показывает
  первые 8 stories и ссылку в Stories workspace, вместо HTML на мегабайты для широких
  machine-generated trend-кандидатов.
- **Perspective gap никогда не считался через CLI**: `reddit-pulse propose` грузил только
  reddit-items, поэтому guard баланса всегда видел 0 mainstream. Баланс теперь измеряется по
  всему релизу (`perspective_gap_available_counts`); на 7-дневном broad разрыв доступен
  (1243/1257 сигналов с ненулевым gap).
- **Trends v2 схлопывал корпус в один «тренд»** при отсутствии кэша плотных эмбеддингов:
  добавлен max-cluster guard (большая доля корпуса **и** большой абсолютный размер).
- **`engine quality snapshot` не подтягивал Pulse**: дефолтный пустой `--signal-release`
  блокировал авто-резолв signal_release (проверка `is None` вместо `not signal_release`).

### Validated on real frozen data (`2026-07-23_2026-07-29-broad-r1`, 4957 items)

- Таксономия (Фаза 6): `ai_technology` 97.5% → 29.4%; макс. рубрика 25.5% (≤50%), пустых нет;
  побочный эффект — `other` 25.5% (следующий рычаг: keywords/LLM).
- Обучаемый скоринг (Фаза 3): обучается на 69 744 авто-метках; `pair_precision` 0.60 вскрывает
  ≈40% overmerge среди размечаемых auto-merge. Полезная модель требует разметки серой зоны
  (Qwen/человек) — детерминизмом серая зона не размечается.
- Trends v2 (Фаза 5): структурно работает; для реальных трендов нужен кэш E5-эмбеддингов.
- Perspective gap (Фаза 4): после фикса — ненулевое распределение на broad (max 0.81).

### Changed

- `collect` теперь является collection-only runtime и не импортирует clustering, ranking,
  briefing или LLM; `run` временно остаётся compatibility alias.
- Radar и Today читают только текущий immutable publication pointer. Если новая версия не
  опубликована, UI сохраняет предыдущую проверенную публикацию и показывает предупреждение.
- Reddit Pulse novelty is neutral when no prior finalized DataRelease exists; same-provider URL
  duplicates are labelled `same_provider_duplicate`, not `cross_source_url`.
- `cluster_lab.db` и `lab` CLI deprecated на один переходный релиз; безопасный импорт разрешён
  только при точном совпадении checksum исходного корпуса.
- `reddit-compass run --analyze` теперь создаёт `item_signals` для каждого item
  через deterministic facets layer; Radar больше не показывает фальшивый блок анализа,
  если разметок `0`.
- Item count в UI считается через `observations`, а не через `items.snapshot_date`.
- Hacker News adapter собирает front page/new/weekly-top перед keyword search, поэтому
  больше не является только AI-keyword источником.
- RSS adapter расширен до section-level coverage: BBC, Guardian, Reuters, NYT/WaPo
  via Google News RSS, FT/Fox Business/USA Today и tech/culture sources.
- Reddit link-post теперь хранит два URL: `discussion_url` и внешний `target_url`;
  canonical URL для link-post — внешний target, чтобы RSS/HN могли склеиться с Reddit.
- Story clustering использует canonical/target URL, нормализованный title/entity overlap
  и recent history; current-run ranking больше не тащит historical item_ids в метрики run.
- **`REDDIT_COMPASS_ENGINE`** (`auto|playwright`): выбор движка Reddit-запросов.
  `playwright` пропускает aiohttp-попытку и сразу стартует Chromium — основной режим
  для ротационных residential proxy, где Reddit отдаёт 403 голому HTTP с pool-IP,
но обслуживает браузерный трафик (проверено на residential provider 2026-07-27).
- **`fetch-and-sync.sh`**: страховка local route — чередование маршрута Reddit:
direct approved route / configured residential proxy (движок playwright); скрипт
  сам source'ит `deploy/hostkey/.env.secrets`. Override — `RC_PROXY_MODE=on|off`.
- **ROADMAP Phase 7**: план переноса Reddit-fetch на VPS (whitelist/sticky residential proxy,
  разнесение тегов образов api/collector, сборка collector в deploy.sh).
- **Trend Radar v2** (`/runs/{date}/radar`): полноценный серверный рендер с LLM-аналитикой —
  карточки топ-тем с пояснениями, идеи для колонок, сдвиги нарратива, pain points (теги),
  топ-10 по релевантности для книги, облако тем. Тёмный editorial-стиль, единый с дашбордом.
- **Сила трендов** (`trend_strength.py`): composite score (кросс-источник × объём × новизна ×
  динамика) + метка 🆕/🔄. История тем в `data/theme-history.jsonl`. Таблица «📈 Сила трендов»
  на radar-странице: новые сильные тренды первыми, повторяющиеся — с указанием недель.
- **Signals без Reddit:** `reddit-compass signals` теперь загружает ВСЕ доступные JSONL
  (posts, hackernews, rss, ladder, producthunt). Раньше падал без `posts.jsonl`.
- **Radar: Ladder + ProductHunt:** `render_trend_radar` включает секции paywall-СМИ
  и ProductHunt в мега-тренды и отдельные блоки.

### Changed

- **Деплой:** теги образов разнесены — `reddit-compass-api:latest` (slim) и
  `reddit-compass-collector:latest` (Playwright/Chromium); `deploy.sh` собирает оба образа.
  Раньше сервисы делили `reddit-compass:latest`, и `up -d api` перезаписывал тег
  slim-образом — collector с Chromium на VPS не собирался никогда.
- **RedditBrowser** (client.py): retry на транзитивные сетевые ошибки (`Failed to fetch` —
  ротация exit IP у residential proxy) и retry `goto` на новой странице (новое соединение —
  другой exit IP у ротационного proxy).
- **`/runs/{date}/radar`**: переключён с regex-рендера markdown на `render_radar_page()`
  (структурный HTML из signals.jsonl + signals-report.md + JSONL-данных).
- **`_cmd_signals`** (cli.py): загрузка из 5 JSONL-файлов вместо только `posts.jsonl`.

### Fixed

- Radar Pulse block no longer uses a closed read-only engine DB connection and now filters
  `signal_releases` by the published `data_release_id` and date.
- Pulse API sanitizes external URLs and fixes the `release_items` join parameter order.
- Signals на VPS падал с "Snapshot не найден: posts.jsonl" при отсутствии Reddit-данных
  (RSS/HN/Ladder/PH собираются автоматически, Reddit — вручную с Mac).

## [0.2.0] — 2026-07-23

### Added

- **Мульти-источники (Phase 6):** 5 адаптеров, 1282 единицы за прогон:
  - `sources/rss.py`: BBC, Guardian, Reuters, TechCrunch, Verge, Ars Technica (135 статей)
  - `sources/hackernews.py`: Algolia API, 14 запросов, фильтр 7 дней (197 stories)
  - `sources/ladder.py`: 12 paywall СМИ через Ladder proxy — парсинг статей из listing
    (NYT 28, WaPo 40, Wired 36, Time 28, AmBanker 20, VanityFair 20, NewYorker 10 = 183)
  - `sources/producthunt.py`: Atom feed (30 продуктов)
  - Reddit: Playwright JSON API (737 постов, 18 сабреддитов)
- **Ladder proxy** на VPS: `ghcr.io/everywall/ladder`, Docker network `reddit-compass_net`,
  `LADDER_URL=http://ladder:8080`. Ruleset: 33 домена (ladder-rules).
- **Run-манифест** (`manifest.py`): прозрачный лог каждого запуска — источник, статус,
  count, ошибки, длительность. Файл: `snapshots/YYYY-MM-DD/run-manifest.json`.
- **Dashboard v2** (интерактивный): кластеры (AI, Surveillance, Труд, Бизнес, Общество, HN, СМИ),
  все посты кликабельные, панель «📋 Статус запуска», навигация по якорям.
- **Trend radar** (`reddit-compass radar`): автогенерация отчёта по кластерам с прямыми
  ссылками на каждый пост. Секция «🔥 Мега-тренды» (топ-15 через все источники).
- **HTTPS-доступ** через хостовой Caddy: public URL хранится только в gitignored deploy env.
  (Basic Auth, Let's Encrypt TLS, SNI).
- **HN фильтр по дате:** `numericFilters=created_at_i>N` — только последние 7 дней
  (было: GPT-4 за 2023, стало: свежие stories).
- **SQLite-хранилище** (`db.py`): `data/compass.db`, таблицы posts/comments/signals/threads.
  Аддитивно к JSONL. CLI: `reddit-compass db init / stats`.
- **REST API** (FastAPI + OAuth2 client credentials): `/api/v1/snapshots|posts|signals|stats`,
  `/oauth/token` (JWT 1h), `/health`. CORS для Vercel-практикума. CLI: `reddit-compass serve`.
- **Уведомления-заготовки** (`notify.py`): `prepare_telegram_digest()`, `prepare_email_digest()` —
  формируют данные БЕЗ отправки, пишут в `data/notifications/`.
- **aiohttp JSON-клиент** (primary): лёгкий HTTP-движок без браузера. Playwright — fallback,
  RSS — last resort. `RedditEngine` переключается автоматически при блоке (HTML/403).
- **Proxy-ротация:** `REDDIT_COMPASS_PROXIES` — round-robin. Только для снижения 429.
- **`comments_for_top_n`**: комментарии только для top-N (default 5). Запросов: 526 → ~130.
- **Stealth-режим:** `--stealth` / `nightly` — jitter 3–6с + exponential backoff.
- **VPS деплой:** Docker compose (batch + api + caddy), host-cron (RSS 03:30, HN 03:45,
  Ladder 04:00), UFW, disk cleanup.
- `docs/COMPETITIVE_ANALYSIS.md`, `docs/IMPROVEMENTS.md`, `docs/MULTI_SOURCE_PLAN.md`.
- ROADMAP: фазы 2.5, 3.5, 6; дополнения в Phase 2/3.
- LICENSE (MIT), CONTRIBUTING.md, SECURITY.md.
- GitHub: description, topics, website, Docker CI/CD (GHCR).
- Тесты: 69+, coverage 65–84%.

### Changed

- AGENTS.md: proxy разрешены (только 429); движок — aiohttp primary; деплой разрешён.
- ARCHITECTURE.md: 5 источников, 3 движка, Ladder, манифест, dashboard.
- Убран r/deepfakes из профиля (404). Удалён проект `reddit_trends` (заменён).
- Зависимости: +fastapi, +uvicorn, +python-jose, +httpx (dev).
- Playwright proxy: раздельный формат (server/username/password), timeout 60с.
- Dashboard: источник определяется по subreddit + source (фикс "rss" вместо "HN").

## [0.1.0] — 2026-07-22

### Added

- Выделение `reddit-compass` в отдельный репозиторий из монорепо-сервиса `reddit-monitor`.
- Config-driven профили сбора (`config/profiles/ai-native.json`, `starter.json`).
- Движок Playwright JSON API + RSS fallback (без Reddit API credentials).
- CLI: `fetch / search / track / virality / report / all / nightly`.
- Детекция виральности: crosspost / score_surge / multi_subreddit.
- Ночной разбор трендов, сгруппированный по кластерам активного профиля.
- Docker-образ и compose для локального запуска; скелет VPS-деплоя (`deploy/hostkey/`).
- Обвязка: uv, ruff, mypy (strict), pytest (+cov), pre-commit, GitHub Actions CI.
- Тесты на парсеры, детектор виральности, рендер отчёта и конфигурацию.

### Changed

- Отвязка от монорепо: пути данных — внутри проекта, через `DATA_DIR` / `HARVESTS_DIR` /
  `REDDIT_COMPASS_CONFIG`; удалена привязка к `research/case-sources/...`.
- `trends_analysis.py` обобщён: разбор идёт по кластерам профиля, убраны книжные/брендовые секции.
- `PostCard.from_dict()` восстанавливает `top_comments` в `CommentCard` при чтении JSONL —
  устранён латентный баг рендера отчёта из файла (обращение к атрибутам dict-объектов).
