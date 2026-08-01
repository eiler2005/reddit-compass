# Документация reddit-compass

Точка входа. Актуальные документы — здесь; исторические планы и отчёты — в
[`archive/`](archive/) (они описывают состояние на момент написания и не поддерживаются).

## С чего начать

| Документ | О чём |
|---|---|
| [`../README.md`](../README.md) | Что делает продукт, quick start, экраны |
| [`../ARCHITECTURE.md`](../ARCHITECTURE.md) | Каноническое описание: границы, контракты, потоки, деплой |
| [`../AGENTS.md`](../AGENTS.md) | Правила работы: границы Reddit, инженерный процесс, секреты, git |

## Контракты и механика

| Документ | О чём |
|---|---|
| [`TREND_ENGINE.md`](TREND_ENGINE.md) | Канонический контракт Engine: релизы, кластеризация, пороги, разметка, publish/rollback |
| [`NEWS_STORIES_TRENDS.md`](NEWS_STORIES_TRENDS.md) | Продуктовый контракт трёх слоёв: News — инбокс, Stories — события, Trends — паттерны |
| [`QUALITY_GATES.md`](QUALITY_GATES.md) | Полы качества, регрессионная упряжка, текущий измеренный статус |
| [`COLLECTION_LIFECYCLE.md`](COLLECTION_LIFECYCLE.md) | Operational contract сбора: статусы, handoff Mac→VPS, rollback |
| [`COLLECTOR_TO_TRENDS_FLOW.md`](COLLECTOR_TO_TRENDS_FLOW.md) | Сквозной путь от адаптера источника до тренда |
| [`DATA_FLOW_DIAGRAMS.md`](DATA_FLOW_DIAGRAMS.md) | Схемы потоков (mermaid): item → story → trend, Pulse, таксономия, обратная связь |
| [`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md) | Таблицы `compass.db` и `trend_engine.db` |
| [`UI_DESIGN_SYSTEM.md`](UI_DESIGN_SYSTEM.md) | Токены, плотный список, полоса источников, навигация, мобильная версия, клавиатура |
| [`HOSTING.md`](HOSTING.md) | Где живёт сервис, как переносить между VPS, гигиена диска |
| [`VERSIONING.md`](VERSIONING.md) | Реестр версий: что развёрнуто и на каких данных работает |
| [`SECRET_SCANNING.md`](SECRET_SCANNING.md) | Обязательный pre-commit gate на секреты |

## Ревью и планы

| Документ | О чём |
|---|---|
| [`ENGINE_REVIEW_V3.md`](ENGINE_REVIEW_V3.md) | Ревью движка: почему слой Stories схлопывался, аналоги, план исправлений |
| [`PLAN_V4.md`](PLAN_V4.md) | План v4 с замерами: пороги под модель, разметка серой зоны, медоидный порог, чистка UI |

## Справочное

| Документ | О чём |
|---|---|
| [`MULTI_SOURCE_PLAN.md`](MULTI_SOURCE_PLAN.md) | Карта источников и кластеров |
| [`STORY_TREND_CLUSTERING_RESEARCH.md`](STORY_TREND_CLUSTERING_RESEARCH.md) | Исследовательская подложка под кластеризацию |
| [`COMPETITIVE_ANALYSIS.md`](COMPETITIVE_ANALYSIS.md) | Аналоги и что из них брать |
| [`RADAR_PROMPTS.md`](RADAR_PROMPTS.md) | Контракты промптов |
