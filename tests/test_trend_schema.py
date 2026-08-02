"""Тренд как повторяющаяся схема события, а не как кластер похожих слов."""

from __future__ import annotations

from typing import Any

from reddit_compass.intelligence.trend_schema import (
    discover_schema_trends,
    extract_action,
    extract_actor,
    story_schema,
)


def _story(story_id: str, title: str, date: str, domain: str = "ai_technology") -> dict[str, Any]:
    return {
        "story_id": story_id,
        "title": title,
        "domain_ids": [domain],
        "first_seen": date,
        "last_seen": date,
    }


def test_action_lexicon_normalizes_wordings_to_one_key() -> None:
    """Разные формулировки одного действия обязаны давать один ключ."""
    wordings = [
        "Amazon lays off 14,000 managers",
        "Salesforce cuts 4000 jobs in support",
        "Intel announces fresh layoffs",
        "Meta slashes its workforce again",
    ]

    keys = {extract_action(title)[0] for title in wordings}  # type: ignore[index]

    assert keys == {"layoffs"}


def test_publisher_is_not_an_actor() -> None:
    """Издание — подпись источника, а не участник события."""
    assert extract_actor("The New York Times sues OpenAI over training data") == "OpenAI"
    assert extract_actor("Reuters: Amazon lays off staff") == "Amazon"


def test_schema_key_combines_action_and_domain() -> None:
    """Одно действие без домена собирает рубрику, а не паттерн."""
    ai_launch = story_schema(_story("s1", "OpenAI launches a new model", "2026-08-01"))
    biotech_launch = story_schema(
        _story("s2", "Moderna launches a new product", "2026-08-01", domain="science_climate")
    )

    assert ai_launch is not None and biotech_launch is not None
    assert ai_launch[0] != biotech_launch[0]
    assert "AI" in ai_launch[1]


def test_recurring_pattern_across_actors_becomes_a_trend() -> None:
    stories = [
        _story("s1", "Amazon lays off 14000 managers", "2026-07-28", "labor_career"),
        _story("s2", "Salesforce cuts 4000 jobs", "2026-07-29", "labor_career"),
        _story("s3", "Intel announces fresh layoffs", "2026-07-30", "labor_career"),
    ]

    trends = discover_schema_trends(stories)

    assert len(trends) == 1
    assert trends[0]["story_count"] == 3
    assert len(trends[0]["distinct_actors"]) >= 2
    assert "сокращения штата" in trends[0]["name_ru"]


def test_one_actor_is_a_storyline_not_a_trend() -> None:
    """Главное отличие от прежнего слоя: повтор у одного актора трендом не является."""
    stories = [
        _story("s1", "OpenAI launches a new model", "2026-07-28"),
        _story("s2", "OpenAI releases another model", "2026-07-29"),
        _story("s3", "OpenAI unveils a new tool", "2026-07-30"),
    ]

    assert discover_schema_trends(stories) == []


def test_single_day_burst_is_not_a_trend() -> None:
    stories = [
        _story("s1", "Amazon lays off staff", "2026-07-28", "labor_career"),
        _story("s2", "Intel cuts jobs", "2026-07-28", "labor_career"),
        _story("s3", "Meta slashes its workforce", "2026-07-28", "labor_career"),
    ]

    assert discover_schema_trends(stories) == []


def test_stories_without_a_recognised_action_are_skipped() -> None:
    stories = [
        _story("s1", "Thoughts on the weather today", "2026-07-28"),
        _story("s2", "A quiet afternoon in the park", "2026-07-29"),
        _story("s3", "Some musings about nothing", "2026-07-30"),
    ]

    assert discover_schema_trends(stories) == []
