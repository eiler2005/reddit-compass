"""Тесты deterministic briefing (intelligence/briefing.py)."""

from __future__ import annotations

from reddit_compass.intelligence.briefing import (
    build_briefing_story,
    build_deterministic_briefing,
    build_evidence_refs,
)
from reddit_compass.intelligence.models import (
    ContentItem,
    SourceHealth,
    Story,
    StoryMetric,
)


def _make_item(
    item_id: str,
    provider: str = "reddit",
    cluster: str = "voices",
    scope: str = "headline",
    url: str = "",
) -> ContentItem:
    return ContentItem(
        item_id=item_id,
        provider=provider,
        source_cluster=cluster,  # type: ignore[arg-type]
        external_id=item_id,
        canonical_url=url or f"https://{provider}.com/{item_id}",
        title=f"Title {item_id}",
        excerpt=f"Excerpt for {item_id}",
        content_scope=scope,  # type: ignore[arg-type]
    )


def _make_story(story_id: str = "story_test") -> Story:
    return Story(
        story_id=story_id,
        canonical_key="test",
        title="Test story",
        first_seen="2026-07-27",
        item_ids=["r1", "n1"],
    )


def _make_metric(story_id: str = "story_test", direction: str = "new") -> StoryMetric:
    return StoryMetric(
        run_id="2026-07-27:ai-native",
        story_id=story_id,
        trend_score=75.0,
        direction=direction,  # type: ignore[arg-type]
        item_count=2,
        source_count=2,
    )


class TestBuildEvidenceRefs:
    def test_limits_to_three(self):
        items = [
            _make_item("r1", "reddit"),
            _make_item("n1", "nytimes"),
            _make_item("h1", "hackernews"),
            _make_item("f1", "ft"),
            _make_item("w1", "wired"),
        ]
        refs = build_evidence_refs(items, limit=3)
        assert len(refs) == 3

    def test_prefers_higher_scope(self):
        items = [
            _make_item("r1", scope="headline"),
            _make_item("r2", scope="full"),
            _make_item("r3", scope="abstract"),
        ]
        refs = build_evidence_refs(items, limit=3)
        assert refs[0].content_scope == "full"

    def test_unique_providers(self):
        items = [
            _make_item("r1", "reddit"),
            _make_item("r2", "reddit"),
            _make_item("n1", "nytimes"),
        ]
        refs = build_evidence_refs(items, limit=3)
        providers = {r.provider for r in refs}
        assert len(providers) == 2


class TestBuildBriefingStory:
    def test_creates_why_it_matters(self):
        story = _make_story()
        metric = _make_metric()
        items = [_make_item("r1"), _make_item("n1", "nytimes")]

        bs = build_briefing_story(story, metric, items)

        assert "2 источник" in bs.why_it_matters
        assert bs.metric.trend_score == 75.0
        assert len(bs.evidence) <= 3
        assert "goal_relevance" in bs.score_breakdown


class TestBuildDeterministicBriefing:
    def test_basic_briefing(self):
        stories = [_make_story()]
        metrics = [_make_metric()]
        items_by_story = {"story_test": [_make_item("r1"), _make_item("n1", "nytimes")]}

        briefing = build_deterministic_briefing(
            run_id="2026-07-27:ai-native",
            date="2026-07-27",
            profile="ai-native",
            stories=stories,
            metrics=metrics,
            items_by_story=items_by_story,
            source_health=[],
        )

        assert briefing.schema_version == 1
        assert briefing.status == "complete"
        assert len(briefing.top_changes) == 1
        assert briefing.pain_points == []
        assert briefing.column_ideas == []
        assert briefing.narrative_shifts == []

    def test_partial_status_with_error(self):
        stories = [_make_story()]
        metrics = [_make_metric()]
        items_by_story = {"story_test": [_make_item("r1")]}
        source_health = [
            SourceHealth(
                source_id="reddit",
                provider="reddit",
                cluster="voices",
                status="error",
                message="Failed",
            )
        ]

        briefing = build_deterministic_briefing(
            run_id="2026-07-27:ai-native",
            date="2026-07-27",
            profile="ai-native",
            stories=stories,
            metrics=metrics,
            items_by_story=items_by_story,
            source_health=source_health,
        )

        assert briefing.status == "partial"

    def test_partial_status_missing_expected(self):
        stories = [_make_story()]
        metrics = [_make_metric()]
        items_by_story = {"story_test": [_make_item("r1")]}
        source_health = [
            SourceHealth(
                source_id="reddit",
                provider="reddit",
                cluster="voices",
                status="ok",
                count=100,
            )
        ]

        briefing = build_deterministic_briefing(
            run_id="2026-07-27:ai-native",
            date="2026-07-27",
            profile="ai-native",
            stories=stories,
            metrics=metrics,
            items_by_story=items_by_story,
            source_health=source_health,
            expected_sources={"reddit", "hackernews"},
        )

        assert briefing.status == "partial"

    def test_top_changes_limit(self):
        stories = [_make_story(f"story_{i}") for i in range(10)]
        metrics = [_make_metric(f"story_{i}", direction="new") for i in range(10)]
        for i, m in enumerate(metrics):
            object.__setattr__(m, "trend_score", 100.0 - i)

        items_by_story = {f"story_{i}": [_make_item(f"r{i}")] for i in range(10)}

        briefing = build_deterministic_briefing(
            run_id="2026-07-27:ai-native",
            date="2026-07-27",
            profile="ai-native",
            stories=stories,
            metrics=metrics,
            items_by_story=items_by_story,
            source_health=[],
        )

        assert len(briefing.top_changes) == 5

    def test_watchlist_for_stable(self):
        stories = [_make_story("story_stable")]
        metrics = [_make_metric("story_stable", direction="stable")]
        items_by_story = {"story_stable": [_make_item("r1")]}

        briefing = build_deterministic_briefing(
            run_id="2026-07-27:ai-native",
            date="2026-07-27",
            profile="ai-native",
            stories=stories,
            metrics=metrics,
            items_by_story=items_by_story,
            source_health=[],
        )

        assert len(briefing.top_changes) == 0
        assert len(briefing.watchlist) == 1
