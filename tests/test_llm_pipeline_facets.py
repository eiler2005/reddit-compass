"""Tests for deterministic item facets used by run --analyze."""

from __future__ import annotations

from reddit_compass.intelligence.llm_pipeline import build_deterministic_item_signals
from reddit_compass.intelligence.models import ContentItem


def test_deterministic_item_signals_cover_every_item() -> None:
    items = [
        ContentItem(
            item_id="reddit:1",
            provider="reddit",
            source_cluster="voices",
            external_id="1",
            canonical_url="https://reddit.com/r/jobs/comments/1",
            title="AI layoffs are hitting white collar jobs",
            domain_ids=["ai_technology", "labor_career"],
        ),
        ContentItem(
            item_id="bbc:1",
            provider="bbc",
            source_cluster="mainstream",
            external_id="1",
            canonical_url="https://bbc.com/sport/story",
            title="Football streaming rights reshape sports media",
            domain_ids=["sports", "culture_media"],
        ),
        ContentItem(
            item_id="legacy:1",
            provider="reuters",
            source_cluster="business",
            external_id="1",
            canonical_url="https://reuters.example/markets/1",
            title="Investors reassess the stock market after earnings",
            source_section="markets",
            domain_ids=["other"],
        ),
    ]

    signals = build_deterministic_item_signals(
        items,
        theme_catalog={"labor": ["layoff", "jobs"], "sports": ["football"]},
        analyzed_at="2026-07-28T10:00:00Z",
    )

    assert [signal.item_id for signal in signals] == ["reddit:1", "bbc:1", "legacy:1"]
    assert signals[0].domain_ids == ["ai_technology", "labor_career"]
    assert "labor" in signals[0].theme_ids
    assert signals[0].goal_relevance["book"] > signals[1].goal_relevance["book"]
    assert signals[2].domain_ids != ["other"]
    assert "business_markets" in signals[2].domain_ids
    assert signals[0].model == "deterministic-facets-v1"
