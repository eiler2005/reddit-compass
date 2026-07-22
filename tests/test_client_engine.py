"""Тесты клиентского слоя: ProxyRotator, RedditHttpClient, конфиг comments_for_top_n."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reddit_compass.client import ProxyRotator, RedditHttpClient, _load_proxies
from reddit_compass.config import MonitorConfig

# ── ProxyRotator ───────────────────────────────────────────────────────────


class TestProxyRotator:
    def test_empty_by_default(self) -> None:
        rotator = ProxyRotator([])
        assert not rotator.enabled
        assert rotator.next() is None

    def test_round_robin(self) -> None:
        rotator = ProxyRotator(["http://p1:8080", "http://p2:8080", "http://p3:8080"])
        assert rotator.enabled
        assert rotator.next() == "http://p1:8080"
        assert rotator.next() == "http://p2:8080"
        assert rotator.next() == "http://p3:8080"
        assert rotator.next() == "http://p1:8080"  # wrap around

    def test_single_proxy(self) -> None:
        rotator = ProxyRotator(["http://only:3128"])
        assert rotator.next() == "http://only:3128"
        assert rotator.next() == "http://only:3128"

    def test_load_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDDIT_COMPASS_PROXIES", "http://a:1, http://b:2 , http://c:3")
        proxies = _load_proxies()
        assert proxies == ["http://a:1", "http://b:2", "http://c:3"]

    def test_load_from_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDDIT_COMPASS_PROXIES", "")
        assert _load_proxies() == []

    def test_load_from_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REDDIT_COMPASS_PROXIES", raising=False)
        assert _load_proxies() == []


# ── RedditHttpClient ───────────────────────────────────────────────────────


class TestRedditHttpClient:
    def test_not_blocked_initially(self) -> None:
        client = RedditHttpClient(ProxyRotator([]))
        assert not client.blocked

    def test_fetch_json_returns_none_when_no_session(self) -> None:
        import asyncio

        client = RedditHttpClient(ProxyRotator([]))
        result = asyncio.run(client.fetch_json("https://www.reddit.com/r/test/hot.json"))
        assert result is None


# ── Config: comments_for_top_n ─────────────────────────────────────────────


class TestCommentsForTopN:
    def test_default_value(self) -> None:
        cfg = MonitorConfig(subreddits={"a": ["test"]})
        assert cfg.settings.comments_for_top_n == 5

    def test_from_file(self, tmp_path: Path) -> None:
        config_data = {
            "subreddits": {"test": ["python"]},
            "settings": {"comments_for_top_n": 3},
        }
        config_file = tmp_path / "test.json"
        config_file.write_text(json.dumps(config_data), encoding="utf-8")
        cfg = MonitorConfig.from_file(config_file)
        assert cfg.settings.comments_for_top_n == 3

    def test_zero_disables_comments(self, tmp_path: Path) -> None:
        config_data = {
            "subreddits": {"test": ["python"]},
            "settings": {"comments_for_top_n": 0},
        }
        config_file = tmp_path / "test.json"
        config_file.write_text(json.dumps(config_data), encoding="utf-8")
        cfg = MonitorConfig.from_file(config_file)
        assert cfg.settings.comments_for_top_n == 0
