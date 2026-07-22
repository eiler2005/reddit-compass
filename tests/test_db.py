"""Тесты SQLite-хранилища (db.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from reddit_compass.db import (
    get_db,
    query_posts,
    query_signals,
    query_snapshots,
    query_stats,
    save_snapshot,
)
from reddit_compass.models import CommentCard, PostCard, ViralitySignal


@pytest.fixture()
def db(tmp_path: Path):
    conn = get_db(tmp_path / "test.db")
    yield conn
    conn.close()


def _make_card(post_id: str = "abc123", score: int = 100, subreddit: str = "test") -> PostCard:
    return PostCard(
        subreddit=subreddit,
        post_id=post_id,
        title="Test post",
        author="tester",
        created_utc="2026-07-22T12:00:00Z",
        score=score,
        upvote_ratio=0.95,
        num_comments=42,
        url="https://example.com",
        selftext="Hello",
        link_flair_text=None,
        is_self=True,
        permalink="/r/test/comments/abc123",
        monitoring_type="hot",
        snapshot_date="2026-07-22",
        top_comments=[CommentCard(comment_id="c1", author="commenter", score=50, body="Nice")],
    )


def _make_signal() -> ViralitySignal:
    return ViralitySignal(
        post_id="abc123",
        title="Viral post",
        original_subreddit="test",
        crossposted_to=["other1", "other2"],
        total_score=5000,
        total_comments=500,
        signal_type="crosspost",
        detected_at="2026-07-22T12:00:00Z",
        url="https://reddit.com/r/test/abc123",
    )


class TestSaveSnapshot:
    def test_save_and_query(self, db):
        cards = [_make_card("p1", 100), _make_card("p2", 200, "other")]
        signals = [_make_signal()]
        sid = save_snapshot(db, "2026-07-22", cards, signals)
        assert sid > 0

        snapshots = query_snapshots(db)
        assert len(snapshots) == 1
        assert snapshots[0]["date"] == "2026-07-22"
        assert snapshots[0]["posts_count"] == 2

    def test_posts_saved_with_comments(self, db):
        cards = [_make_card()]
        save_snapshot(db, "2026-07-22", cards)
        posts = query_posts(db, date="2026-07-22")
        assert len(posts) == 1
        assert posts[0]["post_id"] == "abc123"
        # Check comment saved
        row = db.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        assert row == 1

    def test_duplicate_post_ignored(self, db):
        cards = [_make_card("p1")]
        save_snapshot(db, "2026-07-22", cards)
        save_snapshot(db, "2026-07-22", cards)  # same date, same post
        posts = query_posts(db, date="2026-07-22")
        assert len(posts) == 1


class TestQueryPosts:
    def test_filter_by_subreddit(self, db):
        cards = [_make_card("p1", 100, "alpha"), _make_card("p2", 200, "beta")]
        save_snapshot(db, "2026-07-22", cards)
        posts = query_posts(db, subreddit="alpha")
        assert len(posts) == 1
        assert posts[0]["subreddit"] == "alpha"

    def test_pagination(self, db):
        cards = [_make_card(f"p{i}", i * 10) for i in range(10)]
        save_snapshot(db, "2026-07-22", cards)
        page1 = query_posts(db, limit=3, offset=0)
        page2 = query_posts(db, limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0]["post_id"] != page2[0]["post_id"]

    def test_ordered_by_score(self, db):
        cards = [_make_card("low", 10), _make_card("high", 999)]
        save_snapshot(db, "2026-07-22", cards)
        posts = query_posts(db)
        assert posts[0]["post_id"] == "high"


class TestQuerySignals:
    def test_signals_by_date(self, db):
        save_snapshot(db, "2026-07-22", [_make_card()], [_make_signal()])
        signals = query_signals(db, date="2026-07-22")
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "crosspost"


class TestQueryStats:
    def test_stats(self, db):
        save_snapshot(db, "2026-07-22", [_make_card("p1"), _make_card("p2")], [_make_signal()])
        stats = query_stats(db)
        assert stats["total_snapshots"] == 1
        assert stats["total_posts"] == 2
        assert stats["total_signals"] == 1
        assert stats["latest_snapshot"] == "2026-07-22"
