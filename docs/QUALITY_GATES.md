# Quality Gates — допустимый уровень качества и защита от регрессий

> Зачем: мы постоянно меняем настройки слияния историй, таксономии, трендов и Reddit Pulse.
> Нужен способ **количественно** видеть, стало ли хуже, и при необходимости откатить правку.
> Этот документ описывает harness, который это делает. Код:
> [`src/reddit_compass/intelligence/quality.py`](../src/reddit_compass/intelligence/quality.py),
> CLI: `reddit-compass engine quality {report|check|snapshot}`.

После `report`, `check` или полного `engine cycle` результат quality gate сохраняется в
`trend_engine.db.engine_quality_reports` для точной тройки `DataRelease + StoryRelease +
TrendRelease`. Это делает outcome воспроизводимой частью Engine audit trail и позволяет `/runs`
показывать этап без повторного тяжёлого пересчёта исторических релизов. Отсутствующая запись
означает только «этот legacy/старый attempt ещё не был проверен», а не успешный gate.

## Две идеи

1. **Полы качества (`QUALITY_FLOORS`)** — абсолютный «допустимый уровень». Релиз либо
   проходит каждый пол, либо нет. Если пол не пройден — «у нас проблема» в этом слое.
2. **Baseline-снимок (`config/quality_baselines.json`)** — метрики эталонного релиза.
   `check` дополнительно ловит **регрессии**: метрика ухудшилась сильнее допуска по
   сравнению со снимком. Это и есть «вернуть тесты назад»: сделал правку →
   `engine quality check` красный → `git revert`.

Полы и регрессии — разные вещи. Пол = «ниже этого нельзя никогда». Регрессия =
«стало хуже, чем было в эталоне».

## Полы (текущие пороги)

| Метрика | Пол | Смысл |
|---|---|---|
| `stories_overmerge_ge5` | ≤ 0 | одно-провайдерных историй с ≥5 материалов (overmerge) |
| `stories_overmerge_ge8` | ≤ 0 | то же с ≥8 материалов |
| `taxonomy_ai_tech_share` | ≤ 50% | доля `ai_technology` (раньше было 97.5%) |
| `taxonomy_other_share` | ≤ 40% | доля нерасклассифицированного `other` |
| `taxonomy_max_rubric_share` | ≤ 50% | крупнейшая рубрика верхнего уровня |
| `taxonomy_empty_rubrics` | = 0 | рубрик верхнего уровня без материалов |
| `trends_bad_name_count` | = 0 | трендов с именем в один токен / голый глагол / generic |
| `trends_duplicate_name_count` | = 0 | дубли имён трендов |
| `pulse_other_share` | ≤ 35% | сигналов Pulse с типом `other` |

Пороги меняются в `QUALITY_FLOORS` (код) — это и есть «установка допустимого уровня».

## Регрессионные метрики и допуски

`REGRESSION_METRICS` задаёт, по каким метрикам сравниваем со снимком и какой допуск
(в единицах метрики). Для `stories_cross_source` регрессия = **падение** сверх допуска;
для остальных — **рост** сверх допуска (меньше = лучше).

## Команды

```bash
# Снимок метрик текущего релиза канала (по умолчанию shadow):
reddit-compass engine quality report --channel shadow

# Записать эталон (обычно после того, как релиз признан хорошим):
reddit-compass engine quality snapshot --channel shadow --out config/quality_baselines.json

# Проверка: полы + регрессии относительно config/quality_baselines.json.
# Выход != 0, если есть проваленный пол или регрессия → можно использовать в CI/хуке.
reddit-compass engine quality check --channel shadow
```

Без `--channel`/релизов можно передать релизы явно:
`--data-release … --story-release … --trend-release … [--signal-release …]`.

## CI / хук отката

`engine quality check` возвращает ненулевой код при проблеме — это встраивается в CI
или pre-push хук: прогнал изменение на frozen-релизе → check красный → не пушим /
откатываем. Синтетические инварианты тех же полов живут в
[`tests/test_quality.py`](../tests/test_quality.py) и падают в `pytest` без внешних
данных (overmerge-корпус → пол падает; чистый корпус → проходит; детекция регрессии).

## Текущий статус снимка (2026-07-30, broad, 7-дневный 24–30, все полы ✅)

Снимок `config/quality_baselines.json` снят с живой публикации после запуска
`embedding_v2` + model2vec и промоута в `broad` (`/today` больше не preview).

- **Stories:** overmerge_ge5 = **0**, overmerge_ge8 = **0** ✅; cross_source = 118.
- **Таксономия:** `ai_technology` 24.2% (≤50 ✅), `other` 23.6% (≤40 ✅),
  макс. рубрика 18.9% (≤50 ✅), пустых рубрик 0 ✅.
- **Trends:** `trends_bad_name_count` = **0**, `trends_duplicate_name_count` = **0** ✅ —
  `embedding_v2` (c-TF-IDF имена + дедуп по пересечению историй) убрал «Паттерн: rise» и дубли.
- **Pulse:** `other` 13.9% (≤35 ✅), `gap_available=true`, разрыв считается.

То есть harness теперь полностью зелёный и защищает от регресса: любая правка, которая
вернёт overmerge / голые имена трендов / перекос таксономии, уронит `engine quality check`
(и CI/хук), что сигнализирует «откатить изменение». Qwen-дообучение серой зоны идёт в
ночном `engine cycle` (`--review-limit 80`) и улучшает merge-модель к следующему релизу.
