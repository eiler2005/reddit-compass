"""Date-bound source queries used by manual collection recovery."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from reddit_compass.config import MonitorConfig
from reddit_compass.fetch_subreddits import _post_created_on_date
from reddit_compass.sources import hackernews, ladder, producthunt, rss


def test_reddit_historical_filter_uses_source_timestamp_in_utc() -> None:
    target = int(datetime(2025, 8, 4, 23, 59, 59, tzinfo=UTC).timestamp())
    next_day = int(datetime(2025, 8, 5, 0, 0, 0, tzinfo=UTC).timestamp())

    assert _post_created_on_date({"created_utc": target}, "2025-08-04") is True
    assert _post_created_on_date({"created_utc": next_day}, "2025-08-04") is False
    assert _post_created_on_date({}, "2025-08-04") is False


def test_rss_and_producthunt_require_the_target_publication_date() -> None:
    assert rss._published_on_date("Mon, 04 Aug 2025 23:55:00 +0000", "2025-08-04") is True
    assert rss._published_on_date("Tue, 05 Aug 2025 00:05:00 +0000", "2025-08-04") is False
    assert producthunt._published_on_date("2025-08-04T23:55:00Z", "2025-08-04") is True
    assert producthunt._published_on_date("2025-08-05T00:05:00Z", "2025-08-04") is False


def test_google_news_historical_query_replaces_relative_window() -> None:
    url = rss._historical_feed_url(
        "https://news.google.com/rss/search?q=site%3Areuters.com%2Bwhen%3A1d&hl=en-US",
        "2025-08-04",
    )
    query = parse_qs(urlsplit(url).query)["q"][0]

    assert "when:1d" not in query
    assert "after:2025-08-04" in query
    assert "before:2025-08-05" in query


def test_historical_ladder_google_news_keeps_only_target_date() -> None:
    source = ladder.LadderSource(
        name="example",
        cluster="mainstream",
        base_url="https://www.example.com",
        search_paths=[],
    )
    xml = """
    <rss><channel>
      <item><title>Target</title><link>https://news.example/target</link>
        <description>Target desc</description><pubDate>Mon, 04 Aug 2025 12:00:00 GMT</pubDate>
      </item>
      <item><title>Other</title><link>https://news.example/other</link>
        <description>Other desc</description><pubDate>Tue, 05 Aug 2025 00:00:00 GMT</pubDate>
      </item>
    </channel></rss>
    """

    cards = ladder.historical_google_news_cards(
        source,
        xml,
        snapshot_date="2025-08-04",
        historical_date="2025-08-04",
    )

    assert [card.title for card in cards] == ["Target"]
    assert cards[0].monitoring_type == "ladder_historical_google_news"
    query = parse_qs(urlsplit(ladder.historical_google_news_url(source, "2025-08-04")).query)["q"][
        0
    ]
    assert query == "site:example.com after:2025-08-04 before:2025-08-05"


def test_hackernews_historical_query_has_one_exact_utc_interval(monkeypatch) -> None:
    captured: list[tuple[str, str]] = []

    async def fake_fetch(
        session: object,
        url: str,
        query_label: str,
        snapshot_date: str,
        seen_ids: set[str],
        cards: list[object],
        tally: object = None,
    ) -> None:
        del session, snapshot_date, seen_ids, cards, tally
        captured.append((query_label, url))

    monkeypatch.setattr(hackernews, "_fetch_algolia_url", fake_fetch)

    asyncio.run(
        hackernews.fetch_hn_stories(
            queries=["AI agents"],
            snapshot_date="2025-08-04",
            historical_date="2025-08-04",
        )
    )

    assert [label for label, _ in captured] == ["historical_date", "historical_top", "AI agents"]
    filters = [parse_qs(urlsplit(url).query)["numericFilters"][0] for _, url in captured]
    assert all("created_at_i>=1754265600,created_at_i<1754352000" in value for value in filters)


def test_historical_recovery_never_discards_an_existing_artifact(tmp_path) -> None:
    """Восстановление за прошлую дату не имеет права затирать уже собранный артефакт.

    За восстанавливаемую дату артефакт обычно уже лежит — не отработал финалайзер.
    Запись шла в `mode="w"`, поэтому ретроспективный запрос, отдающий горстку items,
    уничтожал настоящий дневной сбор. Порядок шагов рунбука (сначала --recover-snapshots)
    держит теперь код: без явного --overwrite-artifacts артефакт остаётся нетронутым.
    """
    from reddit_compass.collector import run_source_adapter

    snap_dir = tmp_path / "2026-08-04"
    snap_dir.mkdir()
    artifact = snap_dir / "rss.jsonl"
    original = '{"post_id": "real-item"}\n' * 530
    artifact.write_text(original, encoding="utf-8")

    result = asyncio.run(
        run_source_adapter(
            "rss",
            MonitorConfig(),
            snap_dir,
            "2026-08-04",
            historical_date="2026-08-04",
        )
    )

    assert result.status == "skipped"
    assert "--recover-snapshots" in result.message
    assert artifact.read_text(encoding="utf-8") == original


def test_jsonl_write_leaves_the_previous_file_intact_when_it_fails(tmp_path) -> None:
    """Падение на середине записи не имеет права оставить обрезанный артефакт.

    `mode="w"` усекает файл в момент открытия: отказ адаптера уже после открытия оставлял
    пустой артефакт, неотличимый от честно пустого дня, и день уходил в релиз собранным.
    """
    from reddit_compass.export import write_posts_jsonl

    path = tmp_path / "rss.jsonl"
    original = '{"post_id": "real-item"}\n'
    path.write_text(original, encoding="utf-8")

    class _Exploding:
        def to_json(self) -> str:
            raise RuntimeError("adapter died mid-serialization")

    with contextlib.suppress(RuntimeError):
        write_posts_jsonl([_Exploding()], path)  # type: ignore[list-item]

    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp"))


class _FakeResponse:
    def __init__(self, status: int, body: str = "{}") -> None:
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def json(self):
        import json

        return json.loads(self._body)

    async def text(self) -> str:
        return self._body


class _FakeSession:
    """aiohttp-сессия, отвечающая по сценарию: статус на каждый последовательный запрос."""

    def __init__(self, statuses: list[int]) -> None:
        self._statuses = list(statuses)
        self.calls = 0

    def get(self, url: str, **kwargs):
        del url, kwargs
        status = self._statuses[min(self.calls, len(self._statuses) - 1)]
        self.calls += 1
        return _FakeResponse(status, '{"hits": []}')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


def test_hackernews_raises_when_every_algolia_request_fails(monkeypatch) -> None:
    """Отвалились все запросы — адаптер обязан сказать это, а не вернуть пустой список.

    Возврат `[]` делал отказ неотличимым от тихого дня: run получал `complete`, и
    провалившаяся ночь навсегда исчезала из `collect --coverage`.
    """
    import aiohttp

    from reddit_compass.sources.errors import SourceTransportError

    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: _FakeSession([429]))

    with pytest.raises(SourceTransportError) as excinfo:
        asyncio.run(hackernews.fetch_hn_stories(queries=["AI agents"], snapshot_date="2026-08-04"))

    assert excinfo.value.source_id == "hackernews"
    assert excinfo.value.attempted == 4


def test_hackernews_partial_failure_still_returns_the_day(monkeypatch) -> None:
    """Часть запросов ответила — день собран, пусть и неполно. Это не отказ."""
    import aiohttp

    # Первый запрос падает, остальные отвечают пустым, но валидным телом.
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: _FakeSession([503, 200]))

    cards = asyncio.run(
        hackernews.fetch_hn_stories(queries=["AI agents"], snapshot_date="2026-08-04")
    )

    assert cards == []


def test_producthunt_raises_instead_of_reporting_an_empty_feed(monkeypatch) -> None:
    """Единственный запрос Product Hunt: его отказ — отказ источника, а не тихие сутки."""
    import aiohttp

    from reddit_compass.sources.errors import SourceTransportError

    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: _FakeSession([503]))

    with pytest.raises(SourceTransportError):
        asyncio.run(producthunt.fetch_producthunt(snapshot_date="2026-08-04"))


def test_rss_filters_by_date_before_truncating_the_feed(monkeypatch) -> None:
    """Обрезание до фильтра съедало лимит самыми свежими записями.

    У шести прямых фидов (BBC, Guardian, TechCrunch, Verge, Ars, Medium)
    `_historical_feed_url` возвращает URL без изменений, поэтому фид всегда текущий.
    При `items[:max_items_per_feed]` до фильтра лимит выбирали сегодняшние записи, и до
    нужного дня очередь не доходила — историческое восстановление структурно давало ноль.
    """
    import aiohttp

    target = "Mon, 04 Aug 2025 12:00:00 GMT"
    fresh = "Tue, 05 Aug 2025 12:00:00 GMT"
    items = "".join(
        f"<item><title>Fresh {i}</title><link>https://example.com/fresh{i}</link>"
        f"<description>d</description><pubDate>{fresh}</pubDate></item>"
        for i in range(5)
    )
    items += (
        "<item><title>Target</title><link>https://example.com/target</link>"
        f"<description>d</description><pubDate>{target}</pubDate></item>"
    )
    feed = f"<rss><channel>{items}</channel></rss>"

    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: _FakeSession([200]))
    monkeypatch.setattr(_FakeSession, "get", lambda self, url, **kw: _FakeResponse(200, feed))

    source = rss.RSSSource(name="verge", cluster="tech", feeds=["https://example.com/rss"])
    cards = asyncio.run(
        rss.fetch_rss_source(
            source,
            snapshot_date="2025-08-04",
            max_items_per_feed=3,
            historical_date="2025-08-04",
        )
    )

    assert [card.title for card in cards] == ["Target"]
