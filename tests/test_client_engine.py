"""Тесты клиентского слоя: ProxyRotator, RedditHttpClient, конфиг comments_for_top_n."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reddit_compass.client import (
    ProxyRotator,
    RedditBrowser,
    RedditHttpClient,
    _engine_mode,
    _load_proxies,
)
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


# ── REDDIT_COMPASS_ENGINE ──────────────────────────────────────────────────


class TestEngineMode:
    def test_default_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REDDIT_COMPASS_ENGINE", raising=False)
        assert _engine_mode() == "auto"

    def test_playwright(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDDIT_COMPASS_ENGINE", "playwright")
        assert _engine_mode() == "playwright"

    def test_unknown_falls_back_to_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDDIT_COMPASS_ENGINE", "turbo")
        assert _engine_mode() == "auto"


class _FakeBrowser:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.started = False
        self.payload: dict[str, object] = {"data": {"children": []}}

    async def start(self) -> None:
        self.started = True

    async def fetch_json(self, url: str) -> dict[str, object] | None:
        return self.payload

    async def close(self) -> None:
        self.started = False


class _FakeHttpClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.started = False
        self.blocked = False

    async def start(self) -> None:
        self.started = True

    async def fetch_json(self, url: str) -> None:
        return None

    async def close(self) -> None:
        self.started = False


class TestEngineSelection:
    def test_playwright_mode_skips_aiohttp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        import reddit_compass.client as client

        monkeypatch.setenv("REDDIT_COMPASS_ENGINE", "playwright")
        monkeypatch.setattr(client, "_check_playwright", lambda: True)
        monkeypatch.setattr(client, "RedditBrowser", _FakeBrowser)
        monkeypatch.setattr(client, "RedditHttpClient", _FakeHttpClient)

        engine = client.RedditEngine()
        asyncio.run(engine.start())
        assert engine._http is None
        assert engine._use_browser
        assert isinstance(engine._browser, _FakeBrowser)
        assert engine._browser.started
        result = asyncio.run(engine.fetch_json("https://www.reddit.com/r/test/hot.json"))
        assert result == {"data": {"children": []}}
        asyncio.run(engine.close())

    def test_playwright_mode_without_playwright_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        import reddit_compass.client as client

        monkeypatch.setenv("REDDIT_COMPASS_ENGINE", "playwright")
        monkeypatch.setattr(client, "_check_playwright", lambda: False)
        monkeypatch.setattr(client, "RedditHttpClient", _FakeHttpClient)

        engine = client.RedditEngine()
        asyncio.run(engine.start())
        assert isinstance(engine._http, _FakeHttpClient)
        assert engine._http.started
        assert engine._browser is None
        asyncio.run(engine.close())


# ── Browser retries (flaky residential proxy) ──────────────────────────────


class TestBrowserFlakeRetry:
    def test_retries_on_fetch_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        import reddit_compass.client as client

        class FakePage:
            def __init__(self) -> None:
                self.calls = 0

            async def evaluate(self, js: str, url: str) -> dict[str, object]:
                self.calls += 1
                if self.calls == 1:
                    return {"__error": "TypeError: Failed to fetch"}
                return {"data": {"children": [{"kind": "t3"}]}}

        async def fast_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(client.asyncio, "sleep", fast_sleep)
        browser = RedditBrowser(ProxyRotator([]))
        page = FakePage()
        browser._page = page
        result = asyncio.run(browser.fetch_json("https://www.reddit.com/r/test/hot.json"))
        assert result == {"data": {"children": [{"kind": "t3"}]}}
        assert page.calls == 2

    def test_gives_up_after_max_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        import reddit_compass.client as client

        class FakePage:
            def __init__(self) -> None:
                self.calls = 0

            async def evaluate(self, js: str, url: str) -> dict[str, object]:
                self.calls += 1
                return {"__error": "TypeError: Failed to fetch"}

        async def fast_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(client.asyncio, "sleep", fast_sleep)
        browser = RedditBrowser(ProxyRotator([]))
        page = FakePage()
        browser._page = page
        result = asyncio.run(browser.fetch_json("https://www.reddit.com/r/test/hot.json"))
        assert result is None
        assert page.calls == client.MAX_RETRIES + 1


class TestBrowserGotoRetry:
    def test_retries_goto_with_new_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        import playwright.async_api as pw_api

        import reddit_compass.client as client

        class FakePage:
            def __init__(self) -> None:
                self.goto_calls = 0
                self.closed = False

            async def goto(self, url: str, **kwargs: object) -> None:
                self.goto_calls += 1
                if self.goto_calls == 1:
                    raise RuntimeError("Timeout 60000ms exceeded")

            async def close(self) -> None:
                self.closed = True

        class FakePwBrowser:
            def __init__(self) -> None:
                self.pages: list[FakePage] = []

            async def new_page(self, **kwargs: object) -> FakePage:
                page = FakePage()
                self.pages.append(page)
                return page

            async def close(self) -> None:
                return None

        class FakeChromium:
            def __init__(self, pw_browser: FakePwBrowser) -> None:
                self._pw_browser = pw_browser

            async def launch(self, **kwargs: object) -> FakePwBrowser:
                return self._pw_browser

        class FakePlaywright:
            def __init__(self, pw_browser: FakePwBrowser) -> None:
                self.chromium = FakeChromium(pw_browser)

            async def stop(self) -> None:
                return None

        class FakePlaywrightContext:
            def __init__(self, pw_browser: FakePwBrowser) -> None:
                self._pw = FakePlaywright(pw_browser)

            async def start(self) -> FakePlaywright:
                return self._pw

        pw_browser = FakePwBrowser()
        monkeypatch.setattr(pw_api, "async_playwright", lambda: FakePlaywrightContext(pw_browser))

        async def fast_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(client.asyncio, "sleep", fast_sleep)

        browser = RedditBrowser(ProxyRotator([]))
        asyncio.run(browser.start())
        assert len(pw_browser.pages) == 2
        assert pw_browser.pages[0].closed
        assert browser._page is pw_browser.pages[1]
        asyncio.run(browser.close())
