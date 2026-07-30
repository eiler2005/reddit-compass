"""Tests for broad Radar taxonomy."""

from __future__ import annotations

from reddit_compass.intelligence.taxonomy import (
    DOMAIN_ORDER,
    apply_reddit_quota,
    classify_domains,
    compute_project_scores,
    is_routine_beat,
    normalize_domain_ids,
    rubric_for_domains,
)


def test_broad_domains_include_required_top_level_categories() -> None:
    assert "ai_technology" in DOMAIN_ORDER
    assert "labor_career" in DOMAIN_ORDER
    assert "business_markets" in DOMAIN_ORDER
    assert "society_politics" in DOMAIN_ORDER
    assert "world_geopolitics" in DOMAIN_ORDER
    assert "culture_media" in DOMAIN_ORDER
    assert "sports" in DOMAIN_ORDER
    assert "other" in DOMAIN_ORDER


def test_classifies_sports_culture_business_world() -> None:
    assert (
        classify_domains("NBA media rights deal changes streaming sports", provider="reddit")[0]
        == "sports"
    )
    assert (
        classify_domains("Hollywood creators fight platform algorithm", provider="guardian")[0]
        == "culture_media"
    )
    business_domains = classify_domains(
        "Startup funding and earnings pressure hit markets",
        provider="reuters",
    )
    assert business_domains[0] == "business_markets"
    assert (
        classify_domains("Election and sanctions reshape geopolitics", provider="bbc")[0]
        == "world_geopolitics"
    )


def test_unknown_domain_falls_back_to_other() -> None:
    assert normalize_domain_ids(["unknown"]) == ["other"]
    assert classify_domains("A quiet miscellaneous local note") == ["other"]


def test_keyword_substrings_do_not_create_false_sports_domain() -> None:
    domains = classify_domains(
        "Oil prices fall as government support changes",
        excerpt="Officials support a temporary pause.",
        provider="guardian",
        source_section="business",
    )

    assert "sports" not in domains


def test_project_scores_are_domain_sensitive() -> None:
    book = compute_project_scores(["ai_technology", "labor_career"], "AI layoffs")
    rbc = compute_project_scores(["business_markets"], "Earnings and market pricing")
    assert book["book"] > rbc["book"]
    assert rbc["rbc"] >= book["rbc"]


def test_generic_words_do_not_assign_ai_technology() -> None:
    # «model», «product», «software», «developer» больше не дают домен AI.
    domains = classify_domains("New product launch updates software model")
    assert "ai_technology" not in domains


def test_specific_ai_terms_still_assign_ai_technology() -> None:
    assert classify_domains("OpenAI releases new GPT LLM agent")[0] == "ai_technology"


def test_source_alone_does_not_assign_domain() -> None:
    # HN / r/technology сами по себе больше не назначают ai_technology.
    domains = classify_domains("A quiet miscellaneous local note", source_section="hackernews")
    assert "ai_technology" not in domains


def test_rubric_for_domains_maps_to_top_level() -> None:
    assert rubric_for_domains(["security_privacy"]) == "surveillance"
    assert rubric_for_domains(["finance_consumer"]) == "business"
    assert rubric_for_domains(["sports"]) == "culture"
    assert rubric_for_domains(["other"]) == "other"


def test_reddit_quota_caps_share_and_preserves_order() -> None:
    items = [
        {"id": 1, "provider": "reddit"},
        {"id": 2, "provider": "reuters"},
        {"id": 3, "provider": "reddit"},
        {"id": 4, "provider": "bbc"},
        {"id": 5, "provider": "reddit"},
        {"id": 6, "provider": "nyt"},
        {"id": 7, "provider": "reddit"},
    ]
    result = apply_reddit_quota(
        items, is_reddit=lambda it: it["provider"] == "reddit", max_share=0.3
    )
    reddit_count = sum(1 for it in result if it["provider"] == "reddit")
    assert reddit_count / len(result) <= 0.3
    # Все не-Reddit сохранены, порядок соблюден.
    assert [it["id"] for it in result if it["provider"] != "reddit"] == [2, 4, 6]


def test_reddit_quota_not_applied_without_non_reddit() -> None:
    items = [{"provider": "reddit"}, {"provider": "reddit"}]
    result = apply_reddit_quota(items, is_reddit=lambda it: it["provider"] == "reddit")
    assert len(result) == 2


def test_routine_beat_detection() -> None:
    assert is_routine_beat("49ers injury report and depth chart update") is True
    assert is_routine_beat("Final score: Lakers win", source_section="scoreboard") is True
    assert is_routine_beat("OpenAI releases new GPT model") is False
