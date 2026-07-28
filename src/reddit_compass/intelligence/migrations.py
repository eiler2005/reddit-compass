"""SQLite migrations для intelligence layer.

Миграция через PRAGMA user_version:
- 0/1 → 2: создать intelligence tables.
- 2 → 3: broad Radar taxonomy fields.

Существующие таблицы (snapshots, posts, comments, virality_signals,
tracked_threads) не изменяются.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("reddit_compass")

CURRENT_SCHEMA_VERSION = 3

_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    snapshot_date   TEXT NOT NULL,
    profile         TEXT NOT NULL,
    status          TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 2,
    UNIQUE(snapshot_date, profile)
);

CREATE TABLE IF NOT EXISTS items (
    item_id         TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    source_cluster  TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    canonical_url   TEXT NOT NULL,
    title           TEXT NOT NULL,
    summary_ru      TEXT NOT NULL DEFAULT '',
    excerpt         TEXT NOT NULL DEFAULT '',
    author          TEXT NOT NULL DEFAULT '',
    published_at    TEXT,
    observed_at     TEXT NOT NULL DEFAULT '',
    snapshot_date   TEXT NOT NULL DEFAULT '',
    language        TEXT NOT NULL DEFAULT 'en',
    content_scope   TEXT NOT NULL DEFAULT 'headline',
    source_section  TEXT NOT NULL DEFAULT '',
    domain_ids      TEXT NOT NULL DEFAULT '["other"]',
    discussion_url  TEXT NOT NULL DEFAULT '',
    target_url      TEXT NOT NULL DEFAULT '',
    dedupe_group_id TEXT NOT NULL DEFAULT '',
    evidence_refs   TEXT NOT NULL DEFAULT '[]',
    raw_engagement  TEXT NOT NULL DEFAULT '{}',
    metadata        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_items_provider_published
    ON items(provider, published_at);
CREATE INDEX IF NOT EXISTS idx_items_cluster_published
    ON items(source_cluster, published_at);
CREATE INDEX IF NOT EXISTS idx_items_snapshot
    ON items(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_items_canonical_url
    ON items(canonical_url);

CREATE TABLE IF NOT EXISTS observations (
    run_id                  TEXT NOT NULL,
    item_id                 TEXT NOT NULL,
    observed_at             TEXT NOT NULL,
    source_rank             INTEGER,
    engagement_percentile   REAL NOT NULL DEFAULT 0.0,
    score_delta             REAL,
    comments_delta          REAL,
    PRIMARY KEY (run_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_observations_item ON observations(item_id);
CREATE INDEX IF NOT EXISTS idx_observations_run ON observations(run_id);

CREATE TABLE IF NOT EXISTS stories (
    story_id        TEXT PRIMARY KEY,
    canonical_key   TEXT NOT NULL,
    title           TEXT NOT NULL,
    summary_ru      TEXT NOT NULL DEFAULT '',
    domain_ids      TEXT NOT NULL DEFAULT '["other"]',
    theme_ids       TEXT NOT NULL DEFAULT '[]',
    trend_id        TEXT NOT NULL DEFAULT '',
    lifecycle       TEXT NOT NULL DEFAULT 'new',
    project_scores  TEXT NOT NULL DEFAULT '{}',
    first_seen      TEXT NOT NULL DEFAULT '',
    last_seen       TEXT NOT NULL DEFAULT '',
    item_ids        TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_stories_canonical_key ON stories(canonical_key);

CREATE TABLE IF NOT EXISTS story_items (
    run_id      TEXT NOT NULL,
    story_id    TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    PRIMARY KEY (run_id, story_id, item_id)
);

CREATE TABLE IF NOT EXISTS story_metrics (
    run_id                  TEXT NOT NULL,
    story_id                TEXT NOT NULL,
    goal_relevance          REAL NOT NULL DEFAULT 0.0,
    cross_source_coverage   REAL NOT NULL DEFAULT 0.0,
    momentum                REAL NOT NULL DEFAULT 0.0,
    novelty                 REAL NOT NULL DEFAULT 0.0,
    evidence_quality        REAL NOT NULL DEFAULT 0.0,
    trend_score             REAL NOT NULL DEFAULT 0.0,
    confidence              TEXT NOT NULL DEFAULT 'low',
    direction               TEXT NOT NULL DEFAULT 'new',
    trend_id                TEXT NOT NULL DEFAULT '',
    lifecycle               TEXT NOT NULL DEFAULT 'new',
    project_scores          TEXT NOT NULL DEFAULT '{}',
    item_count              INTEGER NOT NULL DEFAULT 0,
    source_count            INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, story_id)
);

CREATE INDEX IF NOT EXISTS idx_story_metrics_run_trend
    ON story_metrics(run_id, trend_score DESC);

CREATE TABLE IF NOT EXISTS item_signals (
    run_id          TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    domain_ids      TEXT NOT NULL DEFAULT '[]',
    theme_ids       TEXT NOT NULL DEFAULT '[]',
    candidate_themes TEXT NOT NULL DEFAULT '[]',
    pain_points     TEXT NOT NULL DEFAULT '[]',
    buying_intent   INTEGER NOT NULL DEFAULT 0,
    goal_relevance  TEXT NOT NULL DEFAULT '{}',
    summary_ru      TEXT NOT NULL DEFAULT '',
    evidence_scope  TEXT NOT NULL DEFAULT 'headline',
    model           TEXT NOT NULL DEFAULT '',
    analyzed_at     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, item_id)
);

CREATE TABLE IF NOT EXISTS briefings (
    run_id          TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    briefing_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, schema_version)
);

CREATE TABLE IF NOT EXISTS research_state (
    story_id    TEXT PRIMARY KEY,
    saved       INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'unread',
    note        TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_health (
    run_id      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    provider    TEXT NOT NULL,
    cluster     TEXT NOT NULL,
    status      TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    duration_sec REAL NOT NULL DEFAULT 0.0,
    error_code  TEXT,
    message     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, source_id)
);
"""


_V3_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "items": [
        ("domain_ids", "TEXT NOT NULL DEFAULT '[\"other\"]'"),
        ("discussion_url", "TEXT NOT NULL DEFAULT ''"),
        ("target_url", "TEXT NOT NULL DEFAULT ''"),
        ("dedupe_group_id", "TEXT NOT NULL DEFAULT ''"),
        ("evidence_refs", "TEXT NOT NULL DEFAULT '[]'"),
    ],
    "stories": [
        ("domain_ids", "TEXT NOT NULL DEFAULT '[\"other\"]'"),
        ("trend_id", "TEXT NOT NULL DEFAULT ''"),
        ("lifecycle", "TEXT NOT NULL DEFAULT 'new'"),
        ("project_scores", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "story_metrics": [
        ("trend_id", "TEXT NOT NULL DEFAULT ''"),
        ("lifecycle", "TEXT NOT NULL DEFAULT 'new'"),
        ("project_scores", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "item_signals": [
        ("domain_ids", "TEXT NOT NULL DEFAULT '[]'"),
    ],
}


def get_user_version(conn: sqlite3.Connection) -> int:
    """Текущая версия схемы."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    for table, columns in _V3_COLUMNS.items():
        for column, definition in columns:
            if not _column_exists(conn, table, column):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate(conn: sqlite3.Connection) -> None:
    """Мигрирует БД до текущей версии.

    Идемпотентна: повторный вызов ничего не меняет.
    Каждая миграция выполняется в транзакции.
    """
    version = get_user_version(conn)

    if version >= CURRENT_SCHEMA_VERSION:
        return

    if version < 2:
        logger.info("Migrating SQLite schema: v%d → v2", version)
        conn.executescript(_V2_SCHEMA)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        logger.info("SQLite schema migrated to v2")
        version = 2

    if version < 3:
        logger.info("Migrating SQLite schema: v%d → v3", version)
        _migrate_to_v3(conn)
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        logger.info("SQLite schema migrated to v3")
