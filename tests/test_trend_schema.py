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
    assert "layoffs" in trends[0]["name_ru"]


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


def test_sports_and_military_senses_are_vetoed() -> None:
    """Глаголы лексикона многозначны; вне своего домена они дают ложную схему.

    Все примеры — из первого замера 58 трендов, где они попали в тренды ошибочно.
    """
    out_of_scope = [
        "Why is Hamilton getting so many penalties? - F1 Q&A",
        "Como becomes the first club to ban headers in their youth academy",
        "Chelsea's Mudryk free to play as agreement cuts doping ban",
        "Israel raises defense alert over possible US strikes on Iran",
        "the Islamists have Launched an attack in Ceuta",
        "Pirlo out of Italy coach race after scrutiny over betting",
    ]

    assert [extract_action(title) for title in out_of_scope] == [None] * len(out_of_scope)


def test_in_scope_actions_survive_the_veto() -> None:
    in_scope = {
        "US bans Chinese open source AI models": "ban",
        "Pixar lays off over 100 Bay Area workers": "layoffs",
        "EU fines Google 890mn over ad tech": "regulator_fine",
        "Amazon workers strike over pay": "protest",
        "Anthropic raises $3.5B in a new funding round": "funding",
    }

    for title, expected in in_scope.items():
        action = extract_action(title)
        assert action is not None and action[0] == expected, title


def test_trend_names_use_the_source_language() -> None:
    """Имена — на английском, как в оригинальных материалах, без перевода."""
    stories = [
        _story("s1", "Amazon lays off 14000 managers", "2026-07-28", "labor_career"),
        _story("s2", "Salesforce cuts 4000 jobs", "2026-07-29", "labor_career"),
        _story("s3", "Intel announces fresh layoffs", "2026-07-30", "labor_career"),
    ]

    name = discover_schema_trends(stories)[0]["name_ru"]

    assert name == "layoffs in the labour market"
    assert not any("а" <= char <= "я" for char in name.lower())


def test_schema_v2_adapter_satisfies_the_release_contract() -> None:
    """Адаптер обязан отдавать все поля, которые пишет ``create_trend_release``."""
    from reddit_compass.intelligence.engine import _discover_trends_schema_v2

    stories = [
        {
            **_story("s1", "Amazon lays off 14000 managers", "2026-07-28", "labor_career"),
            "source_count": 2,
        },
        {
            **_story("s2", "Salesforce cuts 4000 jobs", "2026-07-29", "labor_career"),
            "source_count": 1,
        },
        {
            **_story("s3", "Intel announces fresh layoffs", "2026-07-30", "labor_career"),
            "source_count": 3,
        },
    ]

    adapted = _discover_trends_schema_v2(stories, params={})

    assert len(adapted) == 1
    trend, memberships = adapted[0]
    required = {
        "trend_id",
        "name_ru",
        "pattern",
        "domain_ids",
        "confidence",
        "lifecycle",
        "source_scope",
        "first_seen",
        "last_seen",
        "story_count",
        "source_count",
        "project_scores",
        "evidence_story_ids",
        "counterpoints",
        "review_status",
        "review_id",
    }
    assert required <= set(trend)
    assert trend["story_count"] == 3
    assert trend["source_count"] == len(trend["distinct_actors"])
    assert len(memberships) == 3
    assert all(len(entry) == 3 for entry in memberships)


def test_schema_v2_trend_id_is_stable_across_runs() -> None:
    """Одинаковый вход обязан давать тот же trend_id — релизы воспроизводимы."""
    from reddit_compass.intelligence.engine import _discover_trends_schema_v2

    stories = [
        _story("s1", "Amazon lays off 14000 managers", "2026-07-28", "labor_career"),
        _story("s2", "Salesforce cuts 4000 jobs", "2026-07-29", "labor_career"),
        _story("s3", "Intel announces fresh layoffs", "2026-07-30", "labor_career"),
    ]

    first = _discover_trends_schema_v2(stories, params={})[0][0]["trend_id"]
    second = _discover_trends_schema_v2(list(reversed(stories)), params={})[0][0]["trend_id"]

    assert first == second


def test_min_distinct_actors_is_configurable() -> None:
    """Порог различных акторов — параметр релиза, а не константа."""
    from reddit_compass.intelligence.engine import _discover_trends_schema_v2

    stories = [
        _story("s1", "OpenAI launches a new model", "2026-07-28"),
        _story("s2", "OpenAI releases another model", "2026-07-29"),
        _story("s3", "OpenAI unveils a new tool", "2026-07-30"),
    ]

    assert _discover_trends_schema_v2(stories, params={}) == []
    assert len(_discover_trends_schema_v2(stories, params={"trend_min_distinct_actors": 1})) == 1


def test_low_signal_stories_never_reach_the_trend_layer() -> None:
    """Регулярные треды отсеивались только при слиянии пар, но не на слое Trends.

    В опубликованном shadow-релизе из-за этого висели «тренды»
    «discussion advice thread july general» и «moronic monday question thread june july».
    """
    from reddit_compass.intelligence.clustering import is_low_signal_title
    from reddit_compass.intelligence.taxonomy import is_routine_beat

    published_as_trends = [
        "Daily General Discussion and Advice Thread - July 30, 2026",
        "Moronic Monday - June 15, 2026 - Your Weekly Questions Thread",
    ]

    for title in published_as_trends:
        assert is_low_signal_title(title) or is_routine_beat(title), title
