"""Offline corpus repair tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from reddit_compass.intelligence.migrations import _V2_SCHEMA
from reddit_compass.intelligence.repair import repair_corpus_db


def test_repair_migrates_v2_db_and_backfills_reddit_urls(tmp_path: Path) -> None:
    db_path = tmp_path / "compass.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_V2_SCHEMA)
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        """
        INSERT INTO runs (run_id, snapshot_date, profile, status, started_at, finished_at)
        VALUES ('2026-07-27:ai-native', '2026-07-27', 'ai-native', 'complete',
                '2026-07-27T07:00:00Z', '2026-07-27T07:10:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO items
            (item_id, provider, source_cluster, external_id, canonical_url, title,
             summary_ru, excerpt, author, published_at, observed_at, snapshot_date,
             language, content_scope, source_section, raw_engagement, metadata)
        VALUES
            ('reddit:abc123', 'reddit', 'voices', 'abc123',
             'https://www.reddit.com/r/artificial/comments/abc123/story',
             'External AI story', '', '', 'alice', '2026-07-27T06:00:00Z',
             '2026-07-27T07:00:00Z', '2026-07-27', 'en', 'headline',
             'technology', '{}', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO observations (run_id, item_id, observed_at)
        VALUES ('2026-07-27:ai-native', 'reddit:abc123', '2026-07-27T07:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO source_health
            (run_id, source_id, provider, cluster, status, count, message)
        VALUES ('2026-07-27:ai-native', 'rss', 'rss', 'voices', 'ok', 999, 'old aggregate')
        """
    )
    conn.commit()

    snapshots_dir = tmp_path / "snapshots"
    snap_dir = snapshots_dir / "2026-07-27"
    snap_dir.mkdir(parents=True)
    post = {
        "subreddit": "artificial",
        "post_id": "abc123",
        "title": "External AI story",
        "author": "alice",
        "created_utc": "2026-07-27T06:00:00Z",
        "score": 42,
        "upvote_ratio": 0.9,
        "num_comments": 7,
        "url": "https://example.com/story?utm_source=reddit",
        "selftext": "Useful external evidence.",
        "link_flair_text": "News",
        "is_self": False,
        "permalink": "/r/artificial/comments/abc123/story/",
        "monitoring_type": "hot",
        "snapshot_date": "2026-07-27",
    }
    (snap_dir / "posts.jsonl").write_text(json.dumps(post) + "\n", encoding="utf-8")

    stats = repair_corpus_db(conn, snapshots_dir)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
    assert {
        "domain_ids",
        "discussion_url",
        "target_url",
        "dedupe_group_id",
        "evidence_refs",
    } <= columns
    row = conn.execute("SELECT * FROM items WHERE item_id = 'reddit:abc123'").fetchone()
    assert row["canonical_url"] == "https://example.com/story"
    assert row["target_url"] == "https://example.com/story"
    assert row["discussion_url"] == "https://www.reddit.com/r/artificial/comments/abc123/story"
    assert json.loads(row["domain_ids"]) != ["other"]

    health = conn.execute(
        """
        SELECT source_id, provider, cluster, status, count
        FROM source_health
        WHERE run_id = '2026-07-27:ai-native'
        ORDER BY source_id
        """
    ).fetchall()
    assert [row["source_id"] for row in health] == ["hackernews", "reddit:artificial"]
    assert health[0]["status"] == "empty"
    assert health[1]["status"] == "ok"
    assert health[1]["count"] == 1
    assert stats["items_backfilled"] == 1
    assert stats["runs_health_rebuilt"] == 1
