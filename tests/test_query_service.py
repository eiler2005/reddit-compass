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
from reddit_compass.intelligence.models import Story, StoryMetric
from reddit_compass.intelligence.repository import replace_run_stories, upsert_run


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


class TestBuildThemeClouds:
    def test_returns_empty_for_no_signals(self, db_with_data):
        stable, emerging, pain = build_theme_clouds(db_with_data, "2026-07-27:ai-native")
        assert stable == []
        assert emerging == []
        assert pain == []


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
