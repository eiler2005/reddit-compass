"""Тесты парсеров client.py (без сети/браузера)."""

from __future__ import annotations

import json
from pathlib import Path

from reddit_compass.client import parse_comments_json, parse_listing_json, parse_rss

FIX = Path(__file__).parent / "fixtures"


def test_parse_listing_json_picks_only_t3() -> None:
    data = json.loads((FIX / "listing.json").read_text(encoding="utf-8"))
    posts = parse_listing_json(data)
    assert len(posts) == 2  # запись kind="more" отброшена
    assert posts[0]["post_id"] == "abc123"
    assert posts[0]["subreddit"] == "artificial"
    assert posts[0]["score"] == 1500
    assert len(posts[1]["crosspost_parent_list"]) == 2


def test_parse_listing_json_handles_empty() -> None:
    assert parse_listing_json({}) == []
    assert parse_listing_json(None) == []


def test_parse_comments_filters_and_sorts() -> None:
    data = json.loads((FIX / "comments.json").read_text(encoding="utf-8"))
    comments = parse_comments_json(data, limit=5)
    # [removed] и stickied отфильтрованы; сортировка по score убыв.
    assert [c["comment_id"] for c in comments] == ["c2", "c1"]
    assert comments[0]["score"] == 900


def test_parse_comments_respects_limit() -> None:
    data = json.loads((FIX / "comments.json").read_text(encoding="utf-8"))
    assert len(parse_comments_json(data, limit=1)) == 1


def test_parse_rss_extracts_fields() -> None:
    xml = (FIX / "feed.xml").read_text(encoding="utf-8")
    entries = parse_rss(xml, "technology")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.author == "rssuser"  # префикс /u/ снят
    assert entry.post_id == "xyz789"  # префикс t3_ снят
    assert entry.permalink == "/r/technology/comments/xyz789/test_rss_post/"
    assert entry.subreddit == "technology"
    assert entry.created_utc is not None


def test_parse_rss_empty() -> None:
    assert parse_rss("", "technology") == []
