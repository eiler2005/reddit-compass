"""Тесты source-agnostic domain models (intelligence/models.py)."""

from __future__ import annotations

import pytest

from reddit_compass.intelligence.models import (
    Briefing,
    BriefingStory,
    ContentItem,
    EvidenceRef,
    ItemSignal,
    Observation,
    ResearchState,
    SourceHealth,
    Story,
    StoryMetric,
)


class TestContentItem:
    def test_minimal_creation(self):
        item = ContentItem(
            item_id="reddit:abc123",
            provider="reddit",
            source_cluster="voices",
            external_id="abc123",
            canonical_url="https://reddit.com/r/test/comments/abc123",
            title="Test post",
        )
        assert item.item_id == "reddit:abc123"
        assert item.provider == "reddit"
        assert item.source_cluster == "voices"
        assert item.content_scope == "headline"
        assert item.language == "en"
        assert item.raw_engagement == {}
        assert item.metadata == {}

    def test_full_creation(self):
        item = ContentItem(
            item_id="hackernews:12345",
            provider="hackernews",
            source_cluster="developers",
            external_id="12345",
            canonical_url="https://news.ycombinator.com/item?id=12345",
            title="Show HN: My project",
            summary_ru="Мой проект",
            excerpt="A cool project",
            author="dev",
            published_at="2026-07-27T10:00:00Z",
            observed_at="2026-07-27T12:00:00Z",
            snapshot_date="2026-07-27",
            language="en",
            content_scope="abstract",
            source_section="hackernews",
            raw_engagement={"points": 100.0, "comments": 50.0},
            metadata={"monitoring_type": "api"},
        )
        assert item.summary_ru == "Мой проект"
        assert item.raw_engagement["points"] == 100.0
        assert item.content_scope == "abstract"

    def test_frozen(self):
        item = ContentItem(
            item_id="reddit:x",
            provider="reddit",
            source_cluster="voices",
            external_id="x",
            canonical_url="https://reddit.com/x",
            title="T",
        )
        with pytest.raises(AttributeError):
            item.title = "new"  # type: ignore[misc]


class TestObservation:
    def test_defaults(self):
        obs = Observation(
            run_id="2026-07-27:ai-native",
            item_id="reddit:abc",
            observed_at="2026-07-27T12:00:00Z",
        )
        assert obs.source_rank is None
        assert obs.engagement_percentile == 0.0
        assert obs.score_delta is None


class TestItemSignal:
    def test_defaults(self):
        sig = ItemSignal(item_id="reddit:abc")
        assert sig.theme_ids == []
        assert sig.buying_intent is False
        assert sig.goal_relevance == {}

    def test_with_values(self):
        sig = ItemSignal(
            item_id="reddit:abc",
            theme_ids=["ai_agents", "labor"],
            pain_points=["Job loss anxiety"],
            buying_intent=True,
            goal_relevance={"book": 80, "rbc": 60},
            summary_ru="Тест",
            evidence_scope="excerpt",
            model="qwen-plus",
            analyzed_at="2026-07-27T12:00:00Z",
        )
        assert len(sig.theme_ids) == 2
        assert sig.goal_relevance["book"] == 80


class TestStory:
    def test_creation(self):
        story = Story(
            story_id="story_abc123def456",
            canonical_key="ai agents jobs",
            title="AI agents replacing jobs",
            item_ids=["reddit:1", "hackernews:2"],
        )
        assert len(story.item_ids) == 2
        assert story.summary_ru == ""


class TestStoryMetric:
    def test_defaults(self):
        m = StoryMetric(run_id="r1", story_id="s1")
        assert m.trend_score == 0.0
        assert m.confidence == "low"
        assert m.direction == "new"


class TestBriefing:
    def test_minimal(self):
        b = Briefing(
            schema_version=1,
            run_id="2026-07-27:ai-native",
            date="2026-07-27",
            profile="ai-native",
            status="partial",
            generated_at="2026-07-27T12:00:00Z",
        )
        assert b.top_changes == []
        assert b.pain_points == []
        assert b.status == "partial"

    def test_with_stories(self):
        story = Story(story_id="s1", canonical_key="k", title="T")
        metric = StoryMetric(run_id="r1", story_id="s1", trend_score=75.0)
        evidence = EvidenceRef(
            item_id="reddit:1",
            provider="reddit",
            source_cluster="voices",
            url="https://reddit.com/1",
            title="Post",
        )
        bs = BriefingStory(
            story=story,
            metric=metric,
            why_it_matters="Important",
            evidence=[evidence],
            score_breakdown={"goal_relevance": 80.0},
        )
        b = Briefing(
            schema_version=1,
            run_id="r1",
            date="2026-07-27",
            profile="ai-native",
            status="complete",
            generated_at="2026-07-27T12:00:00Z",
            top_changes=[bs],
        )
        assert len(b.top_changes) == 1
        assert b.top_changes[0].metric.trend_score == 75.0


class TestSourceHealth:
    def test_statuses(self):
        for status in ("ok", "partial", "error", "not_configured", "skipped"):
            sh = SourceHealth(
                source_id="reddit",
                provider="reddit",
                cluster="voices",
                status=status,  # type: ignore[arg-type]
            )
            assert sh.status == status


class TestResearchState:
    def test_defaults(self):
        rs = ResearchState(story_id="s1")
        assert rs.saved is False
        assert rs.status == "unread"
        assert rs.note == ""
