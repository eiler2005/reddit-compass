"""LLM-извлечение схемы: инварианты вокруг модели, а не сама модель.

Модель здесь не вызывается ни разу. Проверяется то, что ломается тихо: разбор ответа,
кэш, отпечаток для ``params_hash`` и поведение при мусоре на входе.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from reddit_compass.intelligence.engine import engine_db
from reddit_compass.intelligence.trend_schema_llm import (
    ACTION_KEYS,
    action_label,
    extract_schemas,
    extraction_prompt,
    load_schemas,
    parse_batch,
    schemas_digest,
    store_schemas,
    title_key,
)
from reddit_compass.signals import QwenApiError

_TITLES = ["Bank of England holds interest rates at 3.75%", "How do I get into hacking?"]
_GOOD = """{"results":[
  {"i":1,"event":true,"actor":"Bank of England","action":"held rates","key":"regulation",
   "object":"interest rates"},
  {"i":2,"event":false}]}"""


def test_parse_batch_reads_events_and_non_events() -> None:
    records = parse_batch(_GOOD, _TITLES)

    assert [r["is_event"] for r in records] == [1, 0]
    assert records[0]["actor"] == "Bank of England"
    assert records[0]["key"] == "regulation"


def test_unknown_action_key_falls_back_to_other() -> None:
    """Ключ вне словаря обязан стать `other`, а не попасть в ключ схемы как есть."""
    raw = '{"results":[{"i":1,"event":true,"actor":"X","action":"did","key":"teleported"}]}'

    assert parse_batch(raw, ["X did something"])[0]["key"] == "other"


def test_non_trend_keys_never_become_trends() -> None:
    """`other` и `incident` нужны извлечению, но слою — нет.

    `incident` остаётся в промпте, чтобы модель не растаскивала происшествия по
    осмысленным ключам, но тренда из него быть не должно: на прогоне 3 августа он дал
    пять трендов на 220 сюжетов, где в одной «схеме» лежали жара в Европе,
    столкнувшиеся вертолёты и помолвка.
    """
    from reddit_compass.intelligence.trend_schema_llm import NON_TREND_KEYS

    assert sorted(NON_TREND_KEYS) == ["incident", "milestone", "other"]
    for key in NON_TREND_KEYS:
        assert action_label(key) == ""
    assert all(action_label(key) for key in ACTION_KEYS if key not in NON_TREND_KEYS)


def test_incident_is_still_offered_to_the_model() -> None:
    """Без явной корзины происшествия загрязняют `shutdown`, `regulation` и прочие."""
    assert "incident" in extraction_prompt(["headline"])


def test_broken_json_yields_no_records_instead_of_guesses() -> None:
    """Релиз обязан отличать «не событие» от «модель не ответила»."""
    assert parse_batch("not json at all", _TITLES) == []
    assert parse_batch('{"results": "nonsense"}', _TITLES) == []


def test_missing_index_leaves_that_title_unresolved() -> None:
    raw = '{"results":[{"i":1,"event":true,"actor":"A","action":"did","key":"launch"}]}'

    records = parse_batch(raw, _TITLES)

    assert len(records) == 1
    assert records[0]["title"] == _TITLES[0]


def test_cache_key_is_title_plus_prompt_version() -> None:
    """Заголовок, а не story_id: story_id выводится из медоида и меняется между релизами."""
    assert title_key("  Bank Of England   Holds Rates ") == title_key("bank of england holds rates")
    assert title_key("a") != title_key("b")


def test_prompt_lists_every_action_key() -> None:
    prompt = extraction_prompt(["headline"])

    for key in ACTION_KEYS:
        assert key in prompt
    assert "1. headline" in prompt


def test_cache_round_trip(tmp_path: Path) -> None:
    conn = engine_db(tmp_path / "engine.db")
    try:
        records = parse_batch(_GOOD, _TITLES)
        assert store_schemas(conn, records, model="test-model") == 2

        loaded = load_schemas(conn, _TITLES)

        assert loaded[title_key(_TITLES[0])]["actor"] == "Bank of England"
        assert loaded[title_key(_TITLES[1])]["is_event"] is False
    finally:
        conn.close()


def test_digest_tracks_content_not_order() -> None:
    """Отпечаток идёт в params_hash: одинаковый кэш — воспроизводимый релиз."""
    first = {"a": {"is_event": True, "key": "launch"}, "b": {"is_event": False}}
    second = {"b": {"is_event": False}, "a": {"is_event": True, "key": "launch"}}

    assert schemas_digest(first) == schemas_digest(second)
    assert schemas_digest(first) != schemas_digest({"a": {"is_event": True, "key": "ban"}})


def test_extract_dedupes_titles_and_survives_a_failing_batch() -> None:
    """Один сорванный батч не имеет права уронить ночной прогон."""
    calls: list[str] = []

    async def runner(prompt: str, model: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            raise QwenApiError(429, "rate limited")
        return _GOOD

    records = asyncio.run(
        extract_schemas([*_TITLES, *_TITLES], runner, batch_size=1, model="test-model")
    )

    # Два уникальных заголовка → два батча, первый сорван и записей не дал.
    assert len(calls) == 2
    assert len(records) == 1


def test_records_are_persisted_after_every_batch_not_at_the_end() -> None:
    """Первая версия писала всё в конце: обрыв на середине терял часы работы."""
    seen: list[int] = []

    async def runner(prompt: str, model: str) -> str:
        return _GOOD

    asyncio.run(
        extract_schemas(
            ["a headline", "b headline", "c headline", "d headline"],
            runner,
            batch_size=1,
            concurrency=1,
            on_records=lambda batch: seen.append(len(batch)),
        )
    )

    assert len(seen) == 4, "кэш обязан пополняться после каждого батча"


def test_batches_run_concurrently() -> None:
    """Последовательно 9 317 заголовков — около шести часов; это неприемлемо и в проде."""
    in_flight = 0
    peak = 0

    async def runner(prompt: str, model: str) -> str:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _GOOD

    titles = [f"headline number {index}" for index in range(12)]
    asyncio.run(extract_schemas(titles, runner, batch_size=1, concurrency=4))

    assert peak > 1, "батчи идут последовательно"
    assert peak <= 4, "предел одновременных вызовов не соблюдается"


def test_extract_never_calls_the_model_for_an_empty_input() -> None:
    async def runner(prompt: str, model: str) -> str:  # pragma: no cover - не вызывается
        raise AssertionError("модель не должна вызываться")

    assert asyncio.run(extract_schemas([], runner)) == []


def test_schema_v3_requires_a_warm_cache(tmp_path: Path) -> None:
    """Пустой кэш обязан падать, а не отдавать пустой релиз как «паттернов нет»."""
    from reddit_compass.intelligence.engine import _discover_trends_schema_v3

    stories = [{"story_id": "s1", "title": "Anything", "domain_ids": ["ai_technology"]}]

    assert _discover_trends_schema_v3(stories, params={}, schemas={}) == []


def test_llm_resolver_builds_the_same_key_shape_as_the_lexicon() -> None:
    """Сравнение поколений обязано мерить извлечение, а не изменившиеся правила."""
    from reddit_compass.intelligence.engine import _llm_schema_resolver

    story: dict[str, Any] = {
        "story_id": "s1",
        "title": "Bank of England holds interest rates at 3.75%",
        "domain_ids": ["business_markets"],
    }
    schemas = {
        title_key(str(story["title"])): {
            "is_event": True,
            "actor": "Bank of England",
            "action": "held rates",
            "key": "regulation",
        }
    }

    resolved = _llm_schema_resolver(schemas)(story)

    assert resolved == (
        "regulation|business_markets",
        "new regulation in business",
        "Bank of England",
    )


def test_llm_resolver_skips_non_events_and_out_of_scope_domains() -> None:
    from reddit_compass.intelligence.engine import _llm_schema_resolver

    title = "Chelsea fined for agent rules"
    schemas = {
        title_key(title): {"is_event": True, "actor": "Chelsea", "key": "regulator_fine"},
    }
    resolve = _llm_schema_resolver(schemas)

    sports = {"story_id": "s1", "title": title, "domain_ids": ["business_markets", "sports"]}
    unknown = {"story_id": "s2", "title": "never seen", "domain_ids": ["ai_technology"]}

    assert resolve(sports) is None
    assert resolve(unknown) is None


@pytest.mark.parametrize("key", ["", "other", "unknown"])
def test_actions_outside_the_vocabulary_produce_no_trend(key: str) -> None:
    from reddit_compass.intelligence.engine import _llm_schema_resolver

    title = "Someone did something"
    schemas = {title_key(title): {"is_event": True, "actor": "Someone", "key": key}}
    story = {"story_id": "s1", "title": title, "domain_ids": ["ai_technology"]}

    assert _llm_schema_resolver(schemas)(story) is None


def test_total_failure_is_loud_not_an_empty_result() -> None:
    """Неверное имя модели однажды дало «извлечено 0» на 467 батчах и выглядело как
    отсутствие паттернов в корпусе. Отказ стадии обязан быть отказом."""

    async def runner(prompt: str, model: str) -> str:
        raise QwenApiError(400, "model not found")

    with pytest.raises(RuntimeError, match="провалилось на всех"):
        asyncio.run(extract_schemas(["a", "b", "c"], runner, batch_size=1))


def test_partial_failure_still_returns_what_worked() -> None:
    """Один сорванный вызов не обязан ронять прогон — падает только тотальный отказ."""
    calls = 0

    async def runner(prompt: str, model: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise QwenApiError(503, "upstream busy")
        return _GOOD

    records = asyncio.run(extract_schemas(["a", "b"], runner, batch_size=1, concurrency=1))

    assert records


def test_a_code_bug_in_the_runner_is_not_mistaken_for_a_provider_failure() -> None:
    """Ради этого и сужался except.

    Три тихие поломки подряд появились потому, что `except Exception` вокруг сетевого
    вызова глотал и `KeyError` из сборки промпта, и неверную конфигурацию. Дефект кода
    обязан подниматься наверх, а не превращаться в пустой батч.
    """

    async def runner(prompt: str, model: str) -> str:
        raise TypeError("это дефект кода, а не сбой сети")

    with pytest.raises(TypeError):
        asyncio.run(extract_schemas(["a"], runner, batch_size=1))


def test_missing_api_key_fails_immediately() -> None:
    """Отсутствие ключа — ошибка конфигурации, а не «провайдер не ответил»."""

    async def runner(prompt: str, model: str) -> str:
        raise ValueError("Ключ Qwen не установлен")

    with pytest.raises(ValueError, match="Ключ Qwen"):
        asyncio.run(extract_schemas(["a", "b"], runner, batch_size=1))
