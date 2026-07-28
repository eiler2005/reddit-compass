"""Tests for broad Radar taxonomy."""

from __future__ import annotations

from reddit_compass.intelligence.taxonomy import (
    DOMAIN_ORDER,
    classify_domains,
    compute_project_scores,
    normalize_domain_ids,
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


def test_project_scores_are_domain_sensitive() -> None:
    book = compute_project_scores(["ai_technology", "labor_career"], "AI layoffs")
    rbc = compute_project_scores(["business_markets"], "Earnings and market pricing")
    assert book["book"] > rbc["book"]
    assert rbc["rbc"] >= book["rbc"]
