"""Тесты API v2 (api/v2.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reddit_compass.api.app import create_app
from reddit_compass.db import get_db
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import Story, StoryMetric
from reddit_compass.intelligence.repository import replace_run_stories, upsert_run


@pytest.fixture
def client(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = get_db(db_path)
    migrate(conn)

    # Создаём тестовые данные
    upsert_run(
        conn,
        run_id="2026-07-27:ai-native",
        snapshot_date="2026-07-27",
        profile="ai-native",
        status="complete",
        started_at="2026-07-27T10:00:00Z",
        finished_at="2026-07-27T11:00:00Z",
    )

    story = Story(
        story_id="story_test123",
        canonical_key="test story",
        title="Test Story Title",
        summary_ru="Тестовый сюжет",
        first_seen="2026-07-27",
        last_seen="2026-07-27",
        item_ids=["reddit:1", "hackernews:2"],
    )
    metric = StoryMetric(
        run_id="2026-07-27:ai-native",
        story_id="story_test123",
        trend_score=75.0,
        confidence="high",
        direction="new",
        item_count=2,
        source_count=2,
    )
    replace_run_stories(conn, "2026-07-27:ai-native", [story], [metric])
    conn.commit()
    conn.close()

    import os

    os.environ["RC_DB_PATH"] = str(db_path)

    app = create_app()
    with TestClient(app) as c:
        yield c

    del os.environ["RC_DB_PATH"]


class TestStoriesEndpoint:
    def test_list_stories(self, client: TestClient):
        response = client.get("/api/v2/stories")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_list_stories_pagination(self, client: TestClient):
        response = client.get("/api/v2/stories?page=1&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["page_size"] == 10

    def test_list_stories_filter_direction(self, client: TestClient):
        response = client.get("/api/v2/stories?direction=new")
        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["direction"] == "new"

    def test_get_story(self, client: TestClient):
        response = client.get("/api/v2/stories/story_test123")
        assert response.status_code == 200
        data = response.json()
        assert data["story_id"] == "story_test123"
        assert data["title"] == "Test Story Title"

    def test_get_story_not_found(self, client: TestClient):
        response = client.get("/api/v2/stories/nonexistent")
        assert response.status_code == 404


class TestRunsEndpoint:
    def test_list_runs(self, client: TestClient):
        response = client.get("/api/v2/runs")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["run_id"] == "2026-07-27:ai-native"


class TestResearchState:
    def test_patch_research_state(self, client: TestClient):
        response = client.patch(
            "/api/v2/stories/story_test123/research-state",
            json={"saved": True, "status": "read", "note": "Important"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["saved"] is True
        assert data["status"] == "read"
        assert data["note"] == "Important"

    def test_get_research_state(self, client: TestClient):
        # Сначала создаём
        client.patch(
            "/api/v2/stories/story_test123/research-state",
            json={"saved": True},
        )
        # Потом получаем
        response = client.get("/api/v2/stories/story_test123/research-state")
        assert response.status_code == 200
        data = response.json()
        assert data["saved"] is True

    def test_get_research_state_not_found(self, client: TestClient):
        response = client.get("/api/v2/stories/nonexistent/research-state")
        assert response.status_code == 200
        assert response.json() is None


class TestBriefingsEndpoint:
    def test_get_briefing_not_found(self, client: TestClient):
        response = client.get("/api/v2/briefings/2026-01-01")
        assert response.status_code == 404


class TestSourceHealthEndpoint:
    def test_list_source_health(self, client: TestClient):
        response = client.get("/api/v2/source-health")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
