"""Тесты query service (api/query_service.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from reddit_compass.api.query_service import (
    build_freshness_line,
    build_run_summary,
    build_source_coverage,
    build_theme_clouds,
    resolve_latest_run,
)
from reddit_compass.api.view_models import RunSummary
from reddit_compass.db import get_db
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import (
    ContentItem,
    ItemSignal,
    Observation,
    SourceHealth,
    Story,
    StoryMetric,
)
from reddit_compass.intelligence.repository import (
    query_stories,
    replace_run_signals,
    replace_run_stories,
    save_source_health,
    upsert_items,
    upsert_observations,
    upsert_run,
)


@pytest.fixture
def db_with_data(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = get_db(db_path)
    migrate(conn)

    # Создаём run
    upsert_run(
        conn,
        run_id="2026-07-27:ai-native",
        snapshot_date="2026-07-27",
        profile="ai-native",
        status="complete",
        started_at="2026-07-27T10:00:00Z",
        finished_at="2026-07-27T11:00:00Z",
    )

    # Создаём stories
    story = Story(
        story_id="story_test",
        canonical_key="test",
        title="Test Story",
        first_seen="2026-07-27",
        item_ids=["reddit:1"],
    )
    metric = StoryMetric(
        run_id="2026-07-27:ai-native",
        story_id="story_test",
        trend_score=75.0,
        direction="new",
        item_count=1,
        source_count=1,
    )
    replace_run_stories(conn, "2026-07-27:ai-native", [story], [metric])
    conn.commit()

    yield conn
    conn.close()


class TestResolveLatestRun:
    def test_returns_latest_date(self, db_with_data):
        result = resolve_latest_run(db_with_data)
        assert result == "2026-07-27"

    def test_returns_none_for_empty_db(self, tmp_path: Path):
        conn = get_db(tmp_path / "empty.db")
        migrate(conn)
        result = resolve_latest_run(conn)
        assert result is None
        conn.close()


class TestBuildRunSummary:
    def test_builds_summary(self, db_with_data):
        summary = build_run_summary(db_with_data, "2026-07-27", "ai-native")
        assert summary is not None
        assert summary.run_id == "2026-07-27:ai-native"
        assert summary.date == "2026-07-27"
        assert summary.status == "complete"
        assert summary.story_count == 1

    def test_returns_none_for_missing_run(self, db_with_data):
        summary = build_run_summary(db_with_data, "2026-01-01", "ai-native")
        assert summary is None


class TestBuildFreshnessLine:
    def test_builds_line(self):
        summary = RunSummary(
            run_id="2026-07-27:ai-native",
            date="2026-07-27",
            profile="ai-native",
            status="complete",
            finished_at="2026-07-27T11:00:00Z",
            unique_item_count=100,
            successful_provider_count=5,
            expected_provider_count=6,
        )
        line = build_freshness_line(summary)
        assert "Полный" in line
        assert "5/6 источников" in line
        assert "100 материалов" in line


class TestBuildSourceCoverage:
    def test_builds_coverage(self, db_with_data):
        coverage = build_source_coverage(db_with_data, "2026-07-27:ai-native", "2026-07-27")
        assert len(coverage) > 0
        # Все источники из registry
        source_ids = {c.source_id for c in coverage}
        assert "reddit" in source_ids
        assert "hackernews" in source_ids

    def test_builds_provider_section_coverage(self, db_with_data):
        item = ContentItem(
            item_id="bbc:1",
            provider="bbc",
            source_cluster="mainstream",
            external_id="1",
            canonical_url="https://bbc.com/news/world-1",
            title="World news story",
            source_section="world",
            domain_ids=["world_geopolitics"],
        )
        upsert_items(db_with_data, [item])
        upsert_observations(
            db_with_data,
            [
                Observation(
                    run_id="2026-07-27:ai-native",
                    item_id=item.item_id,
                    observed_at="2026-07-27T11:00:00Z",
                )
            ],
        )
        save_source_health(
            db_with_data,
            "2026-07-27:ai-native",
            [
                SourceHealth(
                    source_id="bbc:world",
                    provider="bbc",
                    cluster="mainstream",
                    status="ok",
                    count=1,
                    message="world",
                )
            ],
        )
        db_with_data.commit()

        coverage = build_source_coverage(db_with_data, "2026-07-27:ai-native", "2026-07-27")
        by_id = {row.source_id: row for row in coverage}

        assert by_id["bbc:world"].item_count == 1
        assert by_id["bbc:world"].label == "BBC / World"


class TestBuildThemeClouds:
    def test_returns_empty_for_no_signals(self, db_with_data):
        stable, emerging, pain = build_theme_clouds(db_with_data, "2026-07-27:ai-native")
        assert stable == []
        assert emerging == []
        assert pain == []

    def test_cloud_nodes_link_to_explore_with_run_context(self, db_with_data):
        replace_run_signals(
            db_with_data,
            "2026-07-27:ai-native",
            [
                ItemSignal(
                    item_id="reddit:1",
                    theme_ids=["ai_agents"],
                    candidate_themes=["agent security"],
                    pain_points=["security breach"],
                ),
                ItemSignal(
                    item_id="reddit:2",
                    theme_ids=["ai_agents"],
                    candidate_themes=["agent security"],
                    pain_points=["security breach"],
                ),
            ],
        )
        db_with_data.commit()

        stable, emerging, pain = build_theme_clouds(
            db_with_data,
            "2026-07-27:ai-native",
            [{"id": "ai_agents", "label": "AI-агенты"}],
        )

        assert stable[0].url == "/explore?date=2026-07-27&profile=ai-native&theme=ai_agents"
        assert (
            emerging[0].url
            == "/explore?date=2026-07-27&profile=ai-native&candidate_theme=agent+security"
        )
        assert pain[0].url == "/explore?date=2026-07-27&profile=ai-native&pain=security+breach"

    def test_query_stories_filters_by_item_signal_pain(self, db_with_data):
        other_story = Story(
            story_id="story_other",
            canonical_key="other",
            title="Other Story",
            first_seen="2026-07-27",
            item_ids=["reddit:2"],
        )
        other_metric = StoryMetric(
            run_id="2026-07-27:ai-native",
            story_id="story_other",
            trend_score=20.0,
            direction="new",
            item_count=1,
            source_count=1,
        )
        existing_story = Story(
            story_id="story_test",
            canonical_key="test",
            title="Test Story",
            first_seen="2026-07-27",
            item_ids=["reddit:1"],
        )
        existing_metric = StoryMetric(
            run_id="2026-07-27:ai-native",
            story_id="story_test",
            trend_score=75.0,
            direction="new",
            item_count=1,
            source_count=1,
        )
        replace_run_stories(
            db_with_data,
            "2026-07-27:ai-native",
            [existing_story, other_story],
            [existing_metric, other_metric],
        )
        replace_run_signals(
            db_with_data,
            "2026-07-27:ai-native",
            [
                ItemSignal(
                    item_id="reddit:1",
                    theme_ids=["ai_agents"],
                    candidate_themes=["agent security"],
                    pain_points=["security breach"],
                ),
                ItemSignal(
                    item_id="reddit:2",
                    theme_ids=["pricing_models"],
                    candidate_themes=["pricing pressure"],
                    pain_points=["pricing friction"],
                ),
            ],
        )
        db_with_data.commit()

        stories, total = query_stories(
            db_with_data,
            date="2026-07-27",
            profile="ai-native",
            pain="security breach",
        )

        assert total == 1
        assert stories[0]["story_id"] == "story_test"

        theme_stories, theme_total = query_stories(
            db_with_data,
            date="2026-07-27",
            profile="ai-native",
            theme="ai_agents",
        )
        assert theme_total == 1
        assert theme_stories[0]["story_id"] == "story_test"

        candidate_stories, candidate_total = query_stories(
            db_with_data,
            date="2026-07-27",
            profile="ai-native",
            candidate_theme="agent security",
        )
        assert candidate_total == 1
        assert candidate_stories[0]["story_id"] == "story_test"


class TestViewLabels:
    def test_cluster_label(self):
        from reddit_compass.api.view_models import cluster_label

        assert cluster_label("voices") == "🗣 Голоса"
        assert cluster_label("tech_culture") == "🔬 Tech/Культура"
        assert cluster_label("unknown") == "unknown"

    def test_provider_label(self):
        from reddit_compass.api.view_models import provider_label

        assert provider_label("techcrunch") == "TechCrunch"
        assert provider_label("hackernews") == "HN"
        assert provider_label("bbc") == "BBC"
        assert provider_label("unknown_source") == "unknown_source"


class TestAnalysisCoverage:
    def test_deterministic_signals_cover_all_items(self):
        """build_deterministic_item_signals покрывает 100% items."""
        from reddit_compass.intelligence.llm_pipeline import (
            build_deterministic_item_signals,
        )
        from reddit_compass.intelligence.models import ContentItem

        items = [
            ContentItem(
                item_id=f"test:{i}",
                provider="reddit",
                source_cluster="voices",
                external_id=str(i),
                canonical_url=f"https://r.com/{i}",
                title=f"Test story number {i}",
            )
            for i in range(10)
        ]
        signals = build_deterministic_item_signals(items)
        assert len(signals) == len(items)
        signal_ids = {s.item_id for s in signals}
        item_ids = {i.item_id for i in items}
        assert signal_ids == item_ids
