"""Тесты слоя Trends v2 (Фаза 5): эмбеддинг-кластеризация + c-TF-IDF."""

from __future__ import annotations

import json

from reddit_compass.intelligence.trend_discovery import (
    _ctfidf_name,
    _is_specific_name,
    discover_trends,
)


def _story(story_id: str, title: str, date: str, domains: list[str] | None = None) -> dict:
    return {
        "story_id": story_id,
        "title": title,
        "domain_ids": domains or ["ai_technology"],
        "first_seen": date,
        "last_seen": date,
        "source_count": 1,
        "project_scores": {},
    }


def _providers(story_providers: dict[str, list[str]]) -> tuple[dict, dict[str, str]]:
    item_ids_by_story: dict[str, list[str]] = {}
    provider_by_item: dict[str, str] = {}
    for story_id, providers in story_providers.items():
        ids = []
        for index, provider in enumerate(providers):
            item_id = f"{story_id}_item{index}"
            ids.append(item_id)
            provider_by_item[item_id] = provider
        item_ids_by_story[story_id] = ids
    return item_ids_by_story, provider_by_item


def test_discovers_specific_cross_source_trend() -> None:
    stories = [
        _story("s1", "OpenAI launches quantum AI agent platform", "2026-07-25"),
        _story("s2", "OpenAI quantum AI agent rollout expands", "2026-07-26"),
        _story("s3", "Anthropic responds to OpenAI quantum agent", "2026-07-28"),
        _story("s4", "Google weighs OpenAI quantum agent deal", "2026-07-29"),
    ]
    item_ids_by_story, provider_by_item = _providers(
        {
            "s1": ["reuters", "reddit"],
            "s2": ["nytimes"],
            "s3": ["reddit"],
            "s4": ["bbc"],
        }
    )
    trends = discover_trends(stories, item_ids_by_story, provider_by_item)
    assert len(trends) == 1
    trend = trends[0]
    assert trend["story_count"] == 4
    assert len(trend["name_ru"].split()) >= 2
    assert "fall" not in trend["name_ru"].split()
    assert trend["source_scope"] == "cross_source"
    assert trend["confidence_components"]["cross_source"] > 0
    assert trend["lifecycle"] in {"emerging", "growing", "peaked", "steady"}
    assert len(trend["evidence_story_ids"]) == 4


def test_requires_multiple_dates() -> None:
    stories = [
        _story("s1", "OpenAI quantum agent launch", "2026-07-29"),
        _story("s2", "OpenAI quantum agent rollout", "2026-07-29"),
        _story("s3", "OpenAI quantum agent deal", "2026-07-29"),
    ]
    item_ids_by_story, provider_by_item = _providers(
        {"s1": ["reuters"], "s2": ["nytimes"], "s3": ["bbc"]}
    )
    assert discover_trends(stories, item_ids_by_story, provider_by_item) == []


def test_community_only_scope_for_reddit_cluster() -> None:
    stories = [
        _story("s1", "Local AI agent meetup recap", "2026-07-25"),
        _story("s2", "Local AI agent meetup photos", "2026-07-27"),
        _story("s3", "Local AI agent meetup notes", "2026-07-29"),
    ]
    item_ids_by_story, provider_by_item = _providers(
        {"s1": ["reddit"], "s2": ["reddit"], "s3": ["reddit"]}
    )
    trends = discover_trends(stories, item_ids_by_story, provider_by_item)
    assert len(trends) == 1
    assert trends[0]["source_scope"] == "community_only"


def test_is_specific_name_rejects_bare_verbs_and_generic() -> None:
    assert _is_specific_name("fall") is False
    assert _is_specific_name("ai agent") is False
    assert _is_specific_name("") is False
    assert _is_specific_name("openai quantum agent") is True


def test_blob_cluster_is_rejected() -> None:
    # 20 историй с одним заголовком сливаются в один кластер — это тема, не тренд.
    dates = ["2026-07-25"] * 6 + ["2026-07-27"] * 6 + ["2026-07-29"] * 8
    stories = [
        _story(f"s{i}", "OpenAI quantum agent launch expands platform", dates[i]) for i in range(20)
    ]
    item_ids_by_story, provider_by_item = _providers(
        {f"s{i}": ["reuters", "reddit"] for i in range(20)}
    )
    # abs-порог понижен, чтобы 20-историйный blob попал под оба условия guard.
    assert discover_trends(stories, item_ids_by_story, provider_by_item, max_cluster_abs=10) == []
    # С отключённым лимитом кластер проходит (доказательство, что режет именно guard).
    trends = discover_trends(
        stories,
        item_ids_by_story,
        provider_by_item,
        max_cluster_ratio=1.0,
        max_cluster_abs=10,
    )
    assert len(trends) == 1


def test_ctfidf_name_prefers_distinctive_terms() -> None:
    cluster = [
        "OpenAI launches quantum AI agent",
        "OpenAI quantum agent rollout",
        "OpenAI quantum agent deal",
    ]
    corpus = [
        ["unrelated", "sports", "final", "score"],
        ["weather", "forecast", "storm", "warning"],
    ] + [title.split() for title in cluster]
    name = _ctfidf_name(cluster, corpus)
    assert "quantum" in name
    assert len(name.split()) >= 1


def test_ctfidf_name_never_repeats_a_token() -> None:
    """Униграммы и биграммы соревнуются в одном топе и раньше склеивались.

    На релизе 2026-08-01 это дало два тренда с именем «york time york time athletic
    york» и, как следствие, ``trends_duplicate_name_count = 1``.
    """
    cluster = [
        "OpenAI hit by copyright lawsuit - The New York Times",
        "OpenAI sued over training data - The Athletic",
        "New York Times sues OpenAI again over training data",
    ]
    corpus = [["apple", "ships", "chip"], ["tesla", "recalls", "cars"]]

    tokens = _ctfidf_name(cluster, corpus).split()

    assert tokens, "имя не должно быть пустым"
    assert len(tokens) == len(set(tokens)), f"повторяющийся токен в имени: {tokens}"


def test_ctfidf_name_drops_publisher_names() -> None:
    """Название издания — подпись источника, а не паттерн тренда."""
    cluster = [
        "Regulator opens probe - The New York Times",
        "Regulator probe widens - The Athletic",
        "New York Times reports regulator probe expands",
    ]
    corpus = [["apple", "ships", "chip"], ["tesla", "recalls", "cars"]]

    name = _ctfidf_name(cluster, corpus)

    assert "york" not in name
    assert "athletic" not in name
    assert "probe" in name


def test_domains_survive_raw_sqlite_rows() -> None:
    """Истории приходят сырыми строками БД, где ``domain_ids`` — JSON-текст.

    Итерация по строке давала по «рубрике» на символ: на проде у тренда
    ``my ai job me`` в чипах оказались ``"``, ``,``, ``[``, ``_``, ``a``, ``b``…
    Здесь тот же вход, что даёт ``_load_engine_stories``.
    """
    stories = [
        _story("s1", "OpenAI launches quantum AI agent platform", "2026-07-25"),
        _story("s2", "OpenAI quantum AI agent rollout expands", "2026-07-26"),
        _story("s3", "Anthropic responds to OpenAI quantum agent", "2026-07-28"),
        _story("s4", "Google weighs OpenAI quantum agent deal", "2026-07-29"),
    ]
    for story in stories:
        story["domain_ids"] = json.dumps(["ai_technology", "business"])
        story["project_scores"] = json.dumps({"book": 7})

    item_ids_by_story, provider_by_item = _providers(
        {"s1": ["reuters"], "s2": ["nytimes"], "s3": ["reddit"], "s4": ["bbc"]}
    )
    trends = discover_trends(stories, item_ids_by_story, provider_by_item)

    assert len(trends) == 1
    assert trends[0]["domain_ids"] == ["ai_technology", "business"]
    # Оценки проектов терялись молча по той же причине.
    assert trends[0]["project_scores"] == {"book": 7}


def test_broken_domains_do_not_become_characters() -> None:
    """Нечитаемый JSON лучше пустых рубрик, чем списка символов."""
    stories = [
        _story("s1", "OpenAI launches quantum AI agent platform", "2026-07-25"),
        _story("s2", "OpenAI quantum AI agent rollout expands", "2026-07-26"),
        _story("s3", "Anthropic responds to OpenAI quantum agent", "2026-07-28"),
    ]
    for story in stories:
        story["domain_ids"] = "не json"

    item_ids_by_story, provider_by_item = _providers(
        {"s1": ["reuters"], "s2": ["nytimes"], "s3": ["bbc"]}
    )
    trends = discover_trends(stories, item_ids_by_story, provider_by_item)

    assert len(trends) == 1
    assert trends[0]["domain_ids"] == ["other"]
