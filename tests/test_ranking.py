"""Тесты ranking (intelligence/ranking.py)."""

from __future__ import annotations

import pytest

from reddit_compass.intelligence.models import ContentItem, Story
from reddit_compass.intelligence.ranking import (
    compute_confidence,
    compute_direction,
    compute_engagement_value,
    compute_percentiles,
    compute_trend_score,
    cross_source_coverage_score,
    evidence_quality_score,
    goal_relevance_score,
    momentum_score,
    novelty_score,
    rank_story,
)


def _make_item(
    item_id: str,
    provider: str = "reddit",
    cluster: str = "voices",
    scope: str = "headline",
    engagement: dict | None = None,
) -> ContentItem:
    return ContentItem(
        item_id=item_id,
        provider=provider,
        source_cluster=cluster,  # type: ignore[arg-type]
        external_id=item_id,
        canonical_url=f"https://{provider}.com/{item_id}",
        title="Test",
        content_scope=scope,  # type: ignore[arg-type]
        raw_engagement=engagement or {},
    )


class TestEngagementValue:
    def test_reddit(self):
        item = _make_item("r1", "reddit", engagement={"score": 100, "comments": 50})
        value = compute_engagement_value(item)
        assert value > 0

    def test_hackernews(self):
        item = _make_item("h1", "hackernews", engagement={"points": 100, "comments": 50})
        value = compute_engagement_value(item)
        assert value > 0

    def test_media_zero(self):
        item = _make_item("n1", "nytimes", engagement={})
        value = compute_engagement_value(item)
        assert value == 0.0


class TestPercentiles:
    def test_within_provider(self):
        items = [
            _make_item("r1", "reddit", engagement={"score": 100, "comments": 50}),
            _make_item("r2", "reddit", engagement={"score": 10, "comments": 5}),
            _make_item("h1", "hackernews", engagement={"points": 50, "comments": 20}),
        ]
        percentiles = compute_percentiles(items)
        assert percentiles["r1"] > percentiles["r2"]
        assert "h1" in percentiles


class TestGoalRelevance:
    def test_with_signals(self):
        items = [_make_item("r1")]
        signals = {"r1": {"book": 80, "rbc": 60}}
        score = goal_relevance_score(items, item_signals=signals)
        assert score == 70.0

    def test_without_signals_fallback(self):
        items = [_make_item("r1"), _make_item("r2")]
        score = goal_relevance_score(items)
        assert score <= 60.0


class TestCrossSourceCoverage:
    @pytest.mark.parametrize(
        "clusters,expected",
        [
            (set(), 0.0),
            ({"voices"}, 25.0),
            ({"voices", "developers"}, 55.0),
            ({"voices", "developers", "mainstream"}, 75.0),
            ({"voices", "developers", "mainstream", "business"}, 90.0),
            ({"a", "b", "c", "d", "e"}, 100.0),
        ],
    )
    def test_coverage(self, clusters: set[str], expected: float):
        assert cross_source_coverage_score(clusters) == expected


class TestMomentum:
    def test_no_previous_run(self):
        score = momentum_score(50.0, None, None)
        assert score == 50.0

    def test_high_percentile(self):
        score = momentum_score(90.0, None, None)
        assert score > 50.0


class TestNovelty:
    @pytest.mark.parametrize(
        "first_seen,current,expected",
        [
            ("2026-07-27", "2026-07-27", 100.0),  # today
            ("2026-07-26", "2026-07-27", 80.0),  # 1 day
            ("2026-07-25", "2026-07-27", 80.0),  # 2 days
            ("2026-07-21", "2026-07-27", 55.0),  # 6 days
            ("2026-07-14", "2026-07-27", 30.0),  # 13 days
            ("2026-07-01", "2026-07-27", 10.0),  # older
        ],
    )
    def test_novelty(self, first_seen: str, current: str, expected: float):
        assert novelty_score(first_seen, current) == expected

    def test_resurfacing(self):
        assert novelty_score("2026-07-01", "2026-07-27", is_resurfacing=True) == 75.0


class TestEvidenceQuality:
    def test_single_provider_limited(self):
        items = [_make_item("r1", "reddit", scope="full")]
        score = evidence_quality_score(items)
        assert score <= 60.0

    def test_two_providers(self):
        items = [
            _make_item("r1", "reddit", scope="excerpt"),
            _make_item("n1", "nytimes", scope="abstract"),
        ]
        score = evidence_quality_score(items)
        assert score == 62.5  # (75 + 50) / 2

    def test_best_two_providers(self):
        items = [
            _make_item("r1", "reddit", scope="full"),
            _make_item("n1", "nytimes", scope="excerpt"),
            _make_item("h1", "hackernews", scope="headline"),
        ]
        score = evidence_quality_score(items)
        assert score == 87.5  # (100 + 75) / 2


class TestConfidence:
    @pytest.mark.parametrize(
        "providers,evidence,expected",
        [
            ({"reddit", "nytimes"}, 70.0, "high"),
            ({"reddit", "nytimes"}, 50.0, "medium"),
            ({"reddit"}, 80.0, "medium"),
            ({"reddit"}, 50.0, "low"),
        ],
    )
    def test_confidence(self, providers: set[str], evidence: float, expected: str):
        assert compute_confidence(providers, evidence) == expected


class TestDirection:
    def test_new(self):
        assert compute_direction("2026-07-27", "2026-07-27", None, None, 1, 1) == "new"

    def test_resurfacing(self):
        result = compute_direction("2026-07-01", "2026-07-27", 1, 1, 2, 2, gap_days=20)
        assert result == "resurfacing"

    def test_growing(self):
        assert compute_direction("2026-07-20", "2026-07-27", 2, 1, 5, 2) == "growing"

    def test_fading(self):
        assert compute_direction("2026-07-20", "2026-07-27", 10, 3, 5, 1) == "fading"

    def test_stable(self):
        assert compute_direction("2026-07-20", "2026-07-27", 5, 2, 5, 2) == "stable"


class TestTrendScore:
    def test_formula(self):
        score = compute_trend_score(
            goal_relevance=80.0,
            cross_source_coverage=55.0,
            momentum=60.0,
            novelty=100.0,
            evidence_quality=75.0,
        )
        expected = 0.30 * 80 + 0.25 * 55 + 0.20 * 60 + 0.15 * 100 + 0.10 * 75
        assert abs(score - expected) < 0.01


class TestRankStory:
    def test_basic_ranking(self):
        story = Story(
            story_id="story_test",
            canonical_key="test",
            title="Test story",
            first_seen="2026-07-27",
        )
        items = [
            _make_item("r1", "reddit", "voices", "excerpt", {"score": 100, "comments": 50}),
            _make_item("n1", "nytimes", "mainstream", "abstract"),
        ]
        percentiles = {"r1": 80.0, "n1": 50.0}

        metric = rank_story(
            story=story,
            items=items,
            current_date="2026-07-27",
            percentiles=percentiles,
            run_id="2026-07-27:ai-native",
        )

        assert metric.story_id == "story_test"
        assert metric.item_count == 2
        assert metric.source_count == 2
        assert metric.direction == "new"
        assert 0 <= metric.trend_score <= 100

    def test_media_not_excluded_due_to_zero_score(self):
        """Media item со score 0 может войти в top story."""
        story = Story(
            story_id="story_media",
            canonical_key="media",
            title="Media story",
            first_seen="2026-07-27",
        )
        items = [
            _make_item("n1", "nytimes", "mainstream", "abstract", {}),
            _make_item("ft1", "ft", "business", "headline", {}),
        ]
        percentiles = {"n1": 50.0, "ft1": 50.0}

        metric = rank_story(
            story=story,
            items=items,
            current_date="2026-07-27",
            percentiles=percentiles,
            run_id="2026-07-27:ai-native",
        )

        assert metric.source_count == 2
        assert metric.cross_source_coverage == 55.0
