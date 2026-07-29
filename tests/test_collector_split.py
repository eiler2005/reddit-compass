"""Collector writes corpus facts only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from reddit_compass.collector import SourceResult, persist_collection
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import ContentItem


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
