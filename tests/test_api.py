"""Тесты REST API (FastAPI TestClient)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reddit_compass.api.app import create_app
from reddit_compass.db import get_db, save_snapshot
from reddit_compass.models import PostCard


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Настраивает env для тестов API."""
    db_path = tmp_path / "test_api.db"
    monkeypatch.setenv("RC_DB_PATH", str(db_path))
    monkeypatch.setenv("RC_API_SECRET", "test-secret-key")
    monkeypatch.setenv("RC_API_CLIENTS", "testclient:testsecret")
    monkeypatch.setenv("RC_API_CORS_ORIGINS", "https://cheap-intelligence.vercel.app")

    # Создаём тестовые данные
    conn = get_db(db_path)
    card = PostCard(
        subreddit="test",
        post_id="api_test_1",
        title="API Test Post",
        author="tester",
        created_utc="2026-07-22T12:00:00Z",
        score=500,
        upvote_ratio=0.9,
        num_comments=10,
        url="https://example.com",
        selftext="",
        link_flair_text=None,
        is_self=True,
        permalink="/r/test/comments/api_test_1",
        monitoring_type="hot",
        snapshot_date="2026-07-22",
    )
    save_snapshot(conn, "2026-07-22", [card])
    conn.close()
    yield


@pytest.fixture()
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture()
def auth_headers(client: TestClient):
    resp = client.post(
        "/oauth/token",
        json={"client_id": "testclient", "client_secret": "testsecret"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestHealth:
    def test_health_no_auth(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestOAuth:
    def test_valid_credentials(self, client: TestClient):
        resp = client.post(
            "/oauth/token",
            json={"client_id": "testclient", "client_secret": "testsecret"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 3600

    def test_invalid_credentials(self, client: TestClient):
        resp = client.post(
            "/oauth/token",
            json={"client_id": "testclient", "client_secret": "wrong"},
        )
        assert resp.status_code == 401

    def test_wrong_grant_type(self, client: TestClient):
        resp = client.post(
            "/oauth/token",
            json={"client_id": "x", "client_secret": "y", "grant_type": "password"},
        )
        assert resp.status_code == 400


class TestProtectedEndpoints:
    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.get("/api/v1/snapshots")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client: TestClient):
        resp = client.get("/api/v1/snapshots", headers={"Authorization": "Bearer invalid-token"})
        assert resp.status_code == 401

    def test_snapshots_with_auth(self, client: TestClient, auth_headers: dict):
        resp = client.get("/api/v1/snapshots", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["date"] == "2026-07-22"

    def test_posts_with_auth(self, client: TestClient, auth_headers: dict):
        resp = client.get("/api/v1/posts", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["post_id"] == "api_test_1"

    def test_posts_filter_by_subreddit(self, client: TestClient, auth_headers: dict):
        resp = client.get("/api/v1/posts?subreddit=nonexistent", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_stats_with_auth(self, client: TestClient, auth_headers: dict):
        resp = client.get("/api/v1/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_snapshots"] == 1
        assert data["total_posts"] == 1

    def test_signals_with_auth(self, client: TestClient, auth_headers: dict):
        resp = client.get("/api/v1/signals", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
