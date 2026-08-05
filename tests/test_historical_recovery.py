"""Date-bound source queries used by manual collection recovery."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

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
    ) -> None:
        del session, snapshot_date, seen_ids, cards
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
