# Handoff для внешней LLM: story matching / event coreference

Дата: 2026-08-02. Этот документ передаёт воспроизводимые факты и ограничения по
задаче сопоставления новостей в `reddit-compass`. Нужен независимый технический
взгляд на следующий POC, а не подтверждение уже принятого решения.

## Коротко

У сервиса уже есть deterministic retrieval + decision layer с hard-conflict guards.
Ни текущий Candidate v2, ни проверенные готовые компоненты пока не проходят production
quality gate. **Новый production release, deploy и сдвиг publication pointer запрещены**,
пока новый подход не проходит все пары и группы на корректном Golden Set.

Наиболее интересный сигнал: готовый CrossEncoder заметно лучше ранжирует пары, но не
достигает одновременно нужных precision и recall. Он является кандидатом для
консервативного второго слоя, но не заменой current decision layer.

## Инварианты и границы

- Данные: только публичные Reddit-посты/комментарии и публичные источники; Reddit-слой
  read-only.
- Нельзя использовать собранный контент для обучения моделей. Допустимы готовые модели
  в zero-shot/read-only inference; нельзя fine-tune на этих данных.
- Эксперименты — только локально, в scratch и на копии БД. `compass.db` production Engine
  читает read-only; Data Releases immutable.
- `broad`/`ai-native` публикуются вручную только после всех gates. Ночных production jobs
  сейчас нет; production pointer не должен меняться в рамках POC.
- Нельзя называть assistant-разметку human ground truth.

Полные инженерные контракты: [`TREND_ENGINE.md`](TREND_ENGINE.md),
[`QUALITY_GATES.md`](QUALITY_GATES.md), `AGENTS.md` в корне.

## Точный frozen input

| сущность | идентификатор |
| --- | --- |
| DataRelease | `2026-07-23_2026-07-29-broad-r1` |
| FacetRelease | `facets_3f101ad5bd24e30803db` |
| StoryRelease / Candidate v2 | `stories_d7e2ffe73c6dd7cdd991` |
| scratch DB | `scratchpad/eval.db` |

Абсолютный scratch root в локальной сессии:

```text
/private/tmp/claude-501/-Users-DenisErmilov-aiprojects-reddit-compass/
673decf1-2c2f-4c0e-b0cc-9d5378d3827d/scratchpad
```

При переносе на другую машину использовать собственный scratch root и копию БД, а не этот путь.

## Production gates

Для `StoryRelease` обязательны одновременно:

| метрика | порог |
| --- | ---: |
| pair precision | ≥ 0.95 |
| pair recall | ≥ 0.75 |
| cross-source recall | ≥ 0.75 |
| group overmerge rate | ≤ 0.03 |

Ни AUC, ни compression, ни хороший единичный пример не заменяют эти четыре условия.

## Golden Set и provenance разметки

### Пары

- 120 пар: 71 `same_story`, 49 `different_story`.
- 119 размечены автором вручную; 1 пара (`010`) имеет `assistant_review` provenance.
- Ранее автор вручную перепроверил 9 спорных пар: 4 исправления к `different_story`, 5
  подтверждений `same_story`.
- Одно и то же старое разбиение уже использовалось для выбора guards и POC; его нельзя
  считать полностью нетронутым final holdout для следующей модели.

### Группы

- 30 групп, каждая — cluster из четырёх материалов.
- 10 групп размечены автором вручную, 20 — `assistant_review` по явному разрешению автора.
- Результат: 18 `valid_group`, 12 `overmerge`, то есть overmerge rate **0.40**.
- После изменения merge-решений группы меняются, поэтому эти labels нельзя механически
  переиспользовать как оценку нового графа: нужен новый group review package.

## Текущая реализация и baseline

Код: `src/reddit_compass/intelligence/engine.py`, основная функция
`_score_story_pair`; таблица кандидатов `story_candidate_pairs` хранит `score`, `decision`,
`reason` и `features_json`.

Текущая лестница решений:

1. URL/provenance и сильные event-признаки могут дать `auto_merge`.
2. Жёсткие number/location/person conflicts дают `reject`.
3. Остальное — `review`; ограниченное bounded-components-ответвление экспериментально
   переводит часть review-пар в `auto_merge`.
4. При сборке групп дополнительно действует medoid/membership constraint.

Candidate v2 (`stories_d7e2ffe73c6dd7cdd991`) на текущей mixed-provenance Golden Set:

| метрика | результат |
| --- | ---: |
| pair precision | 0.8571 |
| pair recall | 0.5070 |
| cross-source recall | 0.6667 |
| group overmerge | 0.40 |
| compression ratio | 0.8301 |
| publication gate | `false` |

Структурные метрики bounded-components сами по себе выглядели приемлемо, но это не
компенсирует pair/group failures. Важная деталь для hybrid POC: все 42
`auto_merge` из Golden-пар этого Candidate оказались `bounded_component_candidate`; в
данном наборе нет отдельного блока safe provenance auto-merges, который можно было бы
просто сохранить.

## Уже проверенные готовые решения

### GLiNER — отклонён

POC: `gliner-community/gliner_small-v2.5`; 177 уникальных материалов из 120 пар;
read-only inference, без обучения и без новой проектной зависимости.

| proxy-метрика | current facets | GLiNER phrase-aware |
| --- | ---: | ---: |
| same-story с общим anchor | 90.14% | 74.65% |
| different-story anchor collision | 30.61% | 26.53% |
| precision правила «есть общий anchor» | 81.01% | 80.30% |
| false merges с общим anchor | 6/6 | 5/6 |

Вывод: GLiNER хорошо извлекает named entity spans, но не отличает связанные, но разные
развития новости. Не добавлять в Engine/lockfile как decision signal.

Артефакт: `scratchpad/golden/gliner-event-frame-poc.md`;
скрипт: `scratchpad/gliner_event_frame_poc.py`.

### Plain CrossEncoder — promising, но не production-ready

Модель: `cross-encoder/ms-marco-MiniLM-L6-v2`. На вход подавались headline + до
1 600 символов frozen excerpt; пары оценивались A→B и B→A, sigmoid score усреднялся.
Это MS MARCO passage-ranker, а не event-coreference модель.

| metric | current numeric candidate score | CrossEncoder |
| --- | ---: | ---: |
| ROC-AUC (все 120) | 0.7617 | 0.9138 |
| average precision (все 120) | 0.8665 | 0.9396 |

Порог выбрали на детерминированной стратифицированной половине (`dev`) как максимум recall
при precision ≥ 0.95 и без изменения применили на второй половине (`test`):

| policy | test precision | test recall | TP / FP / FN |
| --- | ---: | ---: | --- |
| current Engine policy | 0.8947 | 0.4857 | 17 / 2 / 18 |
| CrossEncoder | 0.9167 | 0.6286 | 22 / 2 / 13 |

CrossEncoder ранжирует лучше, но опасно путает похожие темы с одним событием. Примеры FP:
разные тарифные решения Трампа; визит Си и отдельный визит Нетаньяху. Не интегрирован.

Артефакт: `scratchpad/golden/cross-encoder-story-poc.md`;
скрипт: `scratchpad/cross_encoder_story_poc.py`.

### Hybrid CrossEncoder + hard guards — precision pass, recall fail

Политика была заранее зафиксирована так:

1. `reject`, отсутствие кандидата, number/location/person conflict не могут стать merge.
2. Не-bounded deterministic auto-merges должны сохраняться.
3. Только `review` и bounded `auto_merge` должны проходить CrossEncoder-порог.

На этом Candidate пункт 2 не сработал (см. выше: 42 auto-пары все bounded). Model score
применили к 113 из 120 Golden-пар. Тот же заранее выбранный порог `0.8739` дал:

| split | precision | recall | TP / FP / FN |
| --- | ---: | ---: | --- |
| dev | 0.9643 | 0.7500 | 27 / 1 / 9 |
| test | 0.9545 | 0.6000 | 21 / 1 / 14 |
| все 120, только diagnostic | 0.9600 | 0.6761 | 48 / 2 / 23 |

Это сняло один опасный FP относительно plain CrossEncoder, но не достигает recall gate
0.75. Также не построен новый StoryRelease и не размечены его новые группы. Следовательно
hybrid — **no-go для release**.

Артефакт: `scratchpad/golden/hybrid-cross-encoder-guard-poc.md`;
скрипт: `scratchpad/hybrid_cross_encoder_guard_poc.py`.

## Что не делать

1. Не публиковать Candidate v2 и не сдвигать production pointer из-за хорошего AUC или
   95.45% pair precision одного POC.
2. Не включать CrossEncoder как default merge-правило и не добавлять его в `pyproject.toml` /
   `uv.lock` до успешного POC.
3. Не заменять event identity простым semantic dedup, title similarity или NER anchor overlap.
4. Не обучать/дообучать модель на собранных Reddit/новостных материалах.
5. Не объявлять 20 `assistant_review` groups или пару `010` человеческой разметкой.
6. Не оценивать новый graph старыми group labels без повторного показа изменившихся групп.

## На чём нужна независимая рекомендация

Пожалуйста, предложи **один наиболее перспективный следующий локальный POC** либо
обоснуй, почему нужно остановиться. Ответ должен быть конкретным:

1. Какую готовую open-source модель/библиотеку/внешний event graph выбрать и почему она
   решает именно *cross-document event identity*, а не лишь semantic similarity, NER или dedup.
2. Как встроить её только после candidate retrieval и вместе с hard conflicts, чтобы она не
   переопределяла deterministic `reject`.
3. Как заранее зафиксировать decision policy и исключить tuning по holdout.
4. Какой минимальный новый **human** holdout нужен (пары + новые группы) и как отобрать
   трудные случаи: одна тема/разные события, разные актёры, разные числа, последующие
   обновления одного события, Reddit prompts.
5. Какие metrics должны стать release decision, включая pair P/R, cross-source recall и
   group overmerge.
6. Какой будет stop criterion: при каком результате мы окончательно не интегрируем решение.

Не предлагай обучение на проектном контенте и не предлагай обходить перечисленные release gates.

## Внешние ссылки, уже просмотренные в исследовании

- [ACL CD-ECR paper](https://aclanthology.org/2023.emnlp-main.294/)
- [Sentence Transformers CrossEncoder documentation](https://www.sbert.net/docs/cross_encoder/usage/usage.html)
- [CrossEncoder model card](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2)
- [GLiNER](https://github.com/urchade/GLiNER)
- [SemHash](https://github.com/MinishLab/semhash)
- [GDELT API documentation](https://docs.gdeltcloud.com/api-reference/v2)

## Текущий репозиторный статус

До добавления этого handoff последние значимые коммиты:

- `c251368` — bounded merge guards;
- `87b382b` — GLiNER POC documentation;
- `6b0f134` — plain CrossEncoder POC documentation;
- `e7b0c27` — hybrid POC documentation.

Все проверки последнего состояния прошли: `ruff check`, `ruff format --check`, `mypy src`,
`pytest` (567 passed) и `scripts/secret-scan --all`.
