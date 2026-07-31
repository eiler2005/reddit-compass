"""Collector writes corpus facts only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from reddit_compass.collector import (
    SourceResult,
    finalize_snapshot_collection,
    persist_collection,
)
from reddit_compass.config import MonitorConfig
from reddit_compass.export import write_posts_jsonl
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import ContentItem
from reddit_compass.models import PostCard


def test_persist_collection_does_not_write_derived_tables(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "compass.db")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    item = ContentItem(
        item_id="raw_1",
        provider="reuters",
        source_cluster="business",
        external_id="raw_1",
        canonical_url="https://example.com/raw-1",
        title="Raw collection item",
        observed_at="2026-07-29T07:00:00Z",
        snapshot_date="2026-07-29",
    )

    persist_collection(
        conn,
        run_id="2026-07-29:broad",
        snapshot_date="2026-07-29",
        profile="broad",
        status="complete",
        started_at="2026-07-29T07:00:00Z",
        finished_at="2026-07-29T07:10:00Z",
        items=[item],
        source_results=[SourceResult(source_id="rss", status="ok", count=1)],
    )

    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM source_health").fetchone()[0] >= 1
    assert conn.execute("SELECT COUNT(*) FROM item_signals").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM story_metrics").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == 0


def test_partial_collection_updates_one_source_without_erasing_other_health(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(tmp_path / "compass.db")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    common = {
        "conn": conn,
        "run_id": "2026-07-29:broad",
        "snapshot_date": "2026-07-29",
        "profile": "broad",
        "started_at": "2026-07-29T07:00:00Z",
        "finished_at": "2026-07-29T07:10:00Z",
        "items": [],
    }
    persist_collection(
        **common,
        status="partial",
        source_results=[SourceResult(source_id="rss", status="ok", count=10)],
    )
    persist_collection(
        **common,
        status="complete",
        source_results=[SourceResult(source_id="hackernews", status="ok", count=20)],
    )

    source_ids = {
        row[0]
        for row in conn.execute(
            "SELECT source_id FROM source_health WHERE run_id = '2026-07-29:broad'"
        ).fetchall()
    }
    assert {"rss", "hackernews"} <= source_ids


def test_finalize_snapshot_collection_creates_one_complete_raw_run(tmp_path: Path) -> None:
    date = "2026-07-30"
    snapshot_dir = tmp_path / "snapshots" / date
    write_posts_jsonl([_legacy_card("reddit-post", date)], snapshot_dir / "posts.jsonl")
    write_posts_jsonl([_legacy_card("rss-post", date)], snapshot_dir / "rss.jsonl")

    result = finalize_snapshot_collection(
        config=MonitorConfig(),
        snapshots_dir=tmp_path / "snapshots",
        db_path=tmp_path / "compass.db",
        sources=["reddit", "rss"],
        profile="broad",
        snapshot_date=date,
    )

    conn = sqlite3.connect(tmp_path / "compass.db")
    assert result.status == "complete"
    assert result.run_id == f"{date}:broad"
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "complete"
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
    assert {row[0] for row in conn.execute("SELECT source_id FROM source_health").fetchall()} >= {
        "reddit",
        "rss",
    }


def test_finalize_snapshot_collection_marks_missing_artifact_pending(tmp_path: Path) -> None:
    date = "2026-07-30"
    snapshot_dir = tmp_path / "snapshots" / date
    write_posts_jsonl([_legacy_card("reddit-post", date)], snapshot_dir / "posts.jsonl")

    result = finalize_snapshot_collection(
        config=MonitorConfig(),
        snapshots_dir=tmp_path / "snapshots",
        db_path=tmp_path / "compass.db",
        sources=["reddit", "hn"],
        profile="broad",
        snapshot_date=date,
    )

    assert result.status == "pending"
    assert [source.error_code for source in result.source_results] == [None, "snapshot_missing"]


def _legacy_card(post_id: str, date: str) -> PostCard:
    return PostCard(
        subreddit="technology",
        post_id=post_id,
        title=f"Title for {post_id}",
        author="author",
        created_utc=f"{date}T07:00:00Z",
        score=12,
        upvote_ratio=0.9,
        num_comments=4,
        url=f"https://example.com/{post_id}",
        selftext="Excerpt",
        link_flair_text=None,
        is_self=False,
        permalink=f"/r/technology/comments/{post_id}",
        monitoring_type="hot",
        snapshot_date=date,
    )
