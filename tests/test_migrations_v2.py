"""Тесты SQLite v2 миграций (intelligence/migrations.py)."""

from __future__ import annotations

from pathlib import Path

from reddit_compass.db import get_db
from reddit_compass.intelligence.migrations import CURRENT_SCHEMA_VERSION, get_user_version, migrate


def test_fresh_db_migrates_to_v2(tmp_path: Path):
    conn = get_db(tmp_path / "test.db")
    assert get_user_version(conn) == 0

    migrate(conn)
    assert get_user_version(conn) == CURRENT_SCHEMA_VERSION

    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected = {
        "runs",
        "items",
        "observations",
        "stories",
        "story_items",
        "story_metrics",
        "item_signals",
        "briefings",
        "research_state",
        "source_health",
    }
    assert expected.issubset(tables)
    conn.close()


def test_migration_idempotent(tmp_path: Path):
    conn = get_db(tmp_path / "test.db")
    migrate(conn)
    v1 = get_user_version(conn)

    migrate(conn)
    v2 = get_user_version(conn)

    assert v1 == v2 == CURRENT_SCHEMA_VERSION
    conn.close()


def test_legacy_tables_preserved(tmp_path: Path):
    """Существующие таблицы не удаляются и не переименовываются."""
    conn = get_db(tmp_path / "test.db")

    legacy_tables = {"snapshots", "posts", "comments", "virality_signals", "tracked_threads"}
    tables_before = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert legacy_tables.issubset(tables_before)

    migrate(conn)

    tables_after = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert legacy_tables.issubset(tables_after)
    conn.close()


def test_v2_indexes_created(tmp_path: Path):
    conn = get_db(tmp_path / "test.db")
    migrate(conn)

    indexes = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    expected = {
        "idx_items_provider_published",
        "idx_items_cluster_published",
        "idx_items_snapshot",
        "idx_items_canonical_url",
        "idx_observations_item",
        "idx_observations_run",
        "idx_stories_canonical_key",
        "idx_story_metrics_run_trend",
    }
    assert expected.issubset(indexes)
    conn.close()


def test_v3_columns_created(tmp_path: Path):
    conn = get_db(tmp_path / "test.db")
    migrate(conn)

    def columns(table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    assert {
        "domain_ids",
        "discussion_url",
        "target_url",
        "dedupe_group_id",
        "evidence_refs",
    }.issubset(columns("items"))
    assert {"domain_ids", "trend_id", "lifecycle", "project_scores"}.issubset(columns("stories"))
    assert {"trend_id", "lifecycle", "project_scores"}.issubset(columns("story_metrics"))
    assert "domain_ids" in columns("item_signals")
    conn.close()


def test_reopen_db_no_change(tmp_path: Path):
    """Повторное открытие БД ничего не меняет."""
    db_path = tmp_path / "test.db"
    conn = get_db(db_path)
    migrate(conn)
    conn.close()

    conn2 = get_db(db_path)
    assert get_user_version(conn2) == CURRENT_SCHEMA_VERSION
    conn2.close()
