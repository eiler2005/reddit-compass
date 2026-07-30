"""Offline corpus DB repair utilities.

Repair is intentionally narrower than `db rebuild`: it migrates the existing
SQLite corpus projection and backfills missing v3 fields from local JSONL
snapshots without running network collection or rebuilding legacy story tables.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..sources.registry import SOURCES
from .compat import load_legacy_jsonl
from .migrations import migrate

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LEGACY_FILES = (
    "posts.jsonl",
    "keyword-search.jsonl",
    "hackernews.jsonl",
    "rss.jsonl",
    "ladder.jsonl",
    "producthunt.jsonl",
)


def repair_corpus_db(conn: sqlite3.Connection, snapshots_dir: Path) -> dict[str, int]:
    """Bring an existing `compass.db` to the current corpus schema offline.

    The repair is idempotent:
    - applies normal SQLite migrations;
    - updates existing items from local snapshots when item IDs match;
    - rebuilds `source_health` from observations/items for existing runs;
    - does not insert new items and does not change network-facing state.
    """
    conn.row_factory = sqlite3.Row
    migrate(conn)
    stats = {
        "dates": 0,
        "snapshot_items_seen": 0,
        "items_backfilled": 0,
        "snapshot_items_missing_in_db": 0,
        "runs_health_rebuilt": 0,
        "source_health_rows": 0,
        "expected_empty_sources": 0,
    }
    item_stats = _backfill_items_from_snapshots(conn, snapshots_dir)
    stats.update(item_stats)
    health_stats = _rebuild_source_health(conn)
    stats.update(health_stats)
    conn.commit()
    return stats


def _backfill_items_from_snapshots(conn: sqlite3.Connection, snapshots_dir: Path) -> dict[str, int]:
    stats = {
        "dates": 0,
        "snapshot_items_seen": 0,
        "items_backfilled": 0,
        "snapshot_items_missing_in_db": 0,
    }
    if not snapshots_dir.exists():
        return stats
    dates = sorted(
        path.name
        for path in snapshots_dir.iterdir()
        if path.is_dir() and _DATE_DIR_RE.match(path.name)
    )
    stats["dates"] = len(dates)
    existing_ids = {
        str(row["item_id"]) for row in conn.execute("SELECT item_id FROM items").fetchall()
    }
    for snapshot_date in dates:
        snap_dir = snapshots_dir / snapshot_date
        observed_at = f"{snapshot_date}T00:00:00Z"
        for filename in _LEGACY_FILES:
            path = snap_dir / filename
            if not path.exists():
                continue
            items, _ = load_legacy_jsonl(path, filename, observed_at)
            for item in items:
                stats["snapshot_items_seen"] += 1
                if item.item_id not in existing_ids:
                    stats["snapshot_items_missing_in_db"] += 1
                    continue
                conn.execute(
                    """
                    UPDATE items
                    SET canonical_url = ?,
                        excerpt = ?,
                        author = ?,
                        published_at = COALESCE(NULLIF(?, ''), published_at),
                        language = ?,
                        content_scope = ?,
                        source_section = ?,
                        domain_ids = ?,
                        discussion_url = ?,
                        target_url = ?,
                        dedupe_group_id = ?,
                        evidence_refs = ?,
                        raw_engagement = ?,
                        metadata = ?
                    WHERE item_id = ?
                    """,
                    (
                        item.canonical_url,
                        item.excerpt,
                        item.author,
                        item.published_at or "",
                        item.language,
                        item.content_scope,
                        item.source_section,
                        _json(item.domain_ids),
                        item.discussion_url,
                        item.target_url,
                        item.dedupe_group_id,
                        _json(item.evidence_refs),
                        _json(item.raw_engagement),
                        _json(item.metadata),
                        item.item_id,
                    ),
                )
                stats["items_backfilled"] += 1
    return stats


def _rebuild_source_health(conn: sqlite3.Connection) -> dict[str, int]:
    stats = {
        "runs_health_rebuilt": 0,
        "source_health_rows": 0,
        "expected_empty_sources": 0,
    }
    runs = conn.execute("SELECT run_id FROM runs ORDER BY snapshot_date, run_id").fetchall()
    for run in runs:
        run_id = str(run["run_id"])
        conn.execute("DELETE FROM source_health WHERE run_id = ?", (run_id,))
        rows = conn.execute(
            """
            SELECT
                i.provider AS provider,
                i.source_cluster AS cluster,
                COALESCE(NULLIF(i.source_section, ''), i.provider) AS source_section,
                COUNT(DISTINCT i.item_id) AS count
            FROM observations o
            JOIN items i ON i.item_id = o.item_id
            WHERE o.run_id = ?
            GROUP BY i.provider, i.source_cluster, source_section
            ORDER BY i.provider, source_section
            """,
            (run_id,),
        ).fetchall()
        providers_with_items = {str(row["provider"]) for row in rows}
        for row in rows:
            count = int(row["count"] or 0)
            source_id = _source_id(
                provider=str(row["provider"]),
                source_section=str(row["source_section"] or ""),
            )
            conn.execute(
                """
                INSERT INTO source_health
                    (run_id, source_id, provider, cluster, status, count,
                     duration_sec, error_code, message)
                VALUES (?, ?, ?, ?, ?, ?, 0.0, NULL, ?)
                """,
                (
                    run_id,
                    source_id,
                    str(row["provider"]),
                    str(row["cluster"]),
                    "ok" if count > 0 else "empty",
                    count,
                    "offline repair from observations",
                ),
            )
            stats["source_health_rows"] += 1
        for source in SOURCES.values():
            if (
                not source.enabled_by_default
                or source.expected_min_items <= 0
                or source.provider in providers_with_items
            ):
                continue
            conn.execute(
                """
                INSERT INTO source_health
                    (run_id, source_id, provider, cluster, status, count,
                     duration_sec, error_code, message)
                VALUES (?, ?, ?, ?, 'empty', 0, 0.0, NULL, ?)
                """,
                (
                    run_id,
                    source.source_id,
                    source.provider,
                    source.cluster,
                    f"expected at least {source.expected_min_items} item(s), got 0",
                ),
            )
            stats["source_health_rows"] += 1
            stats["expected_empty_sources"] += 1
        stats["runs_health_rebuilt"] += 1
    return stats


def _source_id(*, provider: str, source_section: str) -> str:
    section = source_section.strip()
    if not section or section.lower() == provider.lower():
        return provider
    return f"{provider}:{section}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
