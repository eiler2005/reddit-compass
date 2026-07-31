"""Versioned Story/Trend Engine over immutable corpus releases.

The collector owns ``compass.db``. This module opens that database read-only,
copies a finalized run into ``trend_engine.db`` and performs all derived work
there. Published Radar versions are immutable; rollback only changes a pointer.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from rapidfuzz import fuzz

from ..config import DEFAULT_DATA_DIR, PROJECT_ROOT
from ..sources.registry import SOURCES
from .clustering import (
    extract_entities,
    extract_ordered_tokens,
    extract_tokens,
    is_generic_title,
    is_low_signal_title,
    normalize_title,
)
from .embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    MODEL2VEC_DEFAULT,
    encode_passages,
    top_k_cosine_pairs,
)
from .engine_reviews import (
    STORY_REVIEW_PROMPT_VERSION,
    TREND_REVIEW_PROMPT_VERSION,
    build_story_review_prompt,
    build_trend_review_prompt,
    validate_story_review,
    validate_trend_review,
)
from .entities import extract_structured_event_frame
from .llm_pipeline import build_deterministic_item_signals
from .models import ContentItem
from .reddit_pulse import build_reddit_pulse_signals, perspective_gap_available_counts
from .story_scoring import MergeModel, auto_label_pair, extract_feature_vector, train_merge_model
from .taxonomy import compute_project_scores, is_routine_beat, normalize_domain_ids

DEFAULT_ENGINE_DB_PATH = DEFAULT_DATA_DIR / "trend_engine.db"
ENGINE_SCHEMA_VERSION = 6
DEFAULT_STORY_METHOD = "hybrid_v2"
DEFAULT_TREND_METHOD = "story_graph_v1"

ReleaseStatus = Literal["building", "finalized"]
AnalysisStatus = Literal["building", "evaluated", "rejected", "published"]
LabelValue = Literal[
    "same_story",
    "different_story",
    "overmerge",
    "undermerge",
    "low_signal",
    "useful_trend",
    "useless_trend",
    "useful",
    "useless",
]

_ENGINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_releases (
    release_id              TEXT PRIMARY KEY,
    profile                 TEXT NOT NULL,
    dates_json              TEXT NOT NULL,
    run_ids_json            TEXT NOT NULL,
    source_db_path          TEXT NOT NULL,
    source_db_checksum      TEXT NOT NULL,
    input_checksum          TEXT NOT NULL DEFAULT '',
    input_status            TEXT NOT NULL,
    source_coverage_json    TEXT NOT NULL DEFAULT '{}',
    config_hash             TEXT NOT NULL DEFAULT '',
    git_sha                 TEXT NOT NULL DEFAULT '',
    item_count              INTEGER NOT NULL DEFAULT 0,
    observation_count       INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'building',
    created_at              TEXT NOT NULL,
    finalized_at            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS release_items (
    release_id       TEXT NOT NULL,
    item_id          TEXT NOT NULL,
    provider         TEXT NOT NULL,
    source_cluster   TEXT NOT NULL,
    external_id      TEXT NOT NULL,
    canonical_url    TEXT NOT NULL,
    title            TEXT NOT NULL,
    summary_ru       TEXT NOT NULL DEFAULT '',
    excerpt          TEXT NOT NULL DEFAULT '',
    author           TEXT NOT NULL DEFAULT '',
    published_at     TEXT,
    observed_at      TEXT NOT NULL DEFAULT '',
    snapshot_date    TEXT NOT NULL DEFAULT '',
    language         TEXT NOT NULL DEFAULT 'en',
    content_scope    TEXT NOT NULL DEFAULT 'headline',
    source_section   TEXT NOT NULL DEFAULT '',
    domain_ids       TEXT NOT NULL DEFAULT '["other"]',
    discussion_url   TEXT NOT NULL DEFAULT '',
    target_url       TEXT NOT NULL DEFAULT '',
    dedupe_group_id  TEXT NOT NULL DEFAULT '',
    evidence_refs    TEXT NOT NULL DEFAULT '[]',
    raw_engagement   TEXT NOT NULL DEFAULT '{}',
    metadata         TEXT NOT NULL DEFAULT '{}',
    row_checksum     TEXT NOT NULL,
    PRIMARY KEY (release_id, item_id)
);

CREATE TABLE IF NOT EXISTS release_observations (
    release_id              TEXT NOT NULL,
    run_id                  TEXT NOT NULL,
    item_id                 TEXT NOT NULL,
    observed_at             TEXT NOT NULL,
    source_rank             INTEGER,
    engagement_percentile   REAL NOT NULL DEFAULT 0.0,
    score_delta             REAL,
    comments_delta          REAL,
    row_checksum            TEXT NOT NULL,
    PRIMARY KEY (release_id, run_id, item_id)
);

CREATE TABLE IF NOT EXISTS release_source_health (
    release_id    TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    provider      TEXT NOT NULL,
    cluster       TEXT NOT NULL,
    status        TEXT NOT NULL,
    count         INTEGER NOT NULL DEFAULT 0,
    duration_sec  REAL NOT NULL DEFAULT 0.0,
    error_code    TEXT,
    message       TEXT NOT NULL DEFAULT '',
    row_checksum  TEXT NOT NULL,
    PRIMARY KEY (release_id, run_id, source_id)
);

CREATE TABLE IF NOT EXISTS facet_releases (
    facet_release_id  TEXT PRIMARY KEY,
    data_release_id   TEXT NOT NULL,
    method            TEXT NOT NULL,
    model             TEXT NOT NULL DEFAULT '',
    prompt_version    TEXT NOT NULL DEFAULT '',
    params_hash       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'building',
    metrics_json      TEXT NOT NULL DEFAULT '{}',
    git_sha           TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_facets (
    facet_release_id  TEXT NOT NULL,
    item_id           TEXT NOT NULL,
    domain_ids        TEXT NOT NULL DEFAULT '[]',
    theme_ids         TEXT NOT NULL DEFAULT '[]',
    candidate_themes  TEXT NOT NULL DEFAULT '[]',
    pain_points       TEXT NOT NULL DEFAULT '[]',
    entities          TEXT NOT NULL DEFAULT '[]',
    event_frame_json  TEXT NOT NULL DEFAULT '{}',
    goal_relevance    TEXT NOT NULL DEFAULT '{}',
    summary_ru        TEXT NOT NULL DEFAULT '',
    evidence_scope    TEXT NOT NULL DEFAULT 'headline',
    PRIMARY KEY (facet_release_id, item_id)
);

CREATE TABLE IF NOT EXISTS story_releases (
    story_release_id  TEXT PRIMARY KEY,
    facet_release_id  TEXT NOT NULL,
    method            TEXT NOT NULL,
    params_hash       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'building',
    metrics_json      TEXT NOT NULL DEFAULT '{}',
    git_sha           TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_candidate_pairs (
    story_release_id  TEXT NOT NULL,
    item_id_a         TEXT NOT NULL,
    item_id_b         TEXT NOT NULL,
    score             REAL NOT NULL,
    decision          TEXT NOT NULL,
    features_json     TEXT NOT NULL DEFAULT '{}',
    reason            TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (story_release_id, item_id_a, item_id_b)
);

CREATE TABLE IF NOT EXISTS engine_stories (
    story_release_id  TEXT NOT NULL,
    story_id          TEXT NOT NULL,
    canonical_key     TEXT NOT NULL,
    title             TEXT NOT NULL,
    summary_ru        TEXT NOT NULL DEFAULT '',
    domain_ids        TEXT NOT NULL DEFAULT '["other"]',
    theme_ids         TEXT NOT NULL DEFAULT '[]',
    project_scores    TEXT NOT NULL DEFAULT '{}',
    first_seen        TEXT NOT NULL DEFAULT '',
    last_seen         TEXT NOT NULL DEFAULT '',
    confidence        TEXT NOT NULL DEFAULT 'low',
    source_count      INTEGER NOT NULL DEFAULT 0,
    item_count        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_release_id, story_id)
);

CREATE TABLE IF NOT EXISTS engine_story_items (
    story_release_id  TEXT NOT NULL,
    story_id          TEXT NOT NULL,
    item_id           TEXT NOT NULL,
    membership_score  REAL NOT NULL DEFAULT 1.0,
    membership_reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (story_release_id, story_id, item_id)
);

CREATE TABLE IF NOT EXISTS story_redirects (
    story_release_id  TEXT NOT NULL,
    old_story_id      TEXT NOT NULL,
    new_story_id      TEXT NOT NULL,
    reason            TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (story_release_id, old_story_id)
);

CREATE TABLE IF NOT EXISTS trend_releases (
    trend_release_id  TEXT PRIMARY KEY,
    story_release_id  TEXT NOT NULL,
    window            TEXT NOT NULL,
    method            TEXT NOT NULL,
    params_hash       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'building',
    history_status    TEXT NOT NULL DEFAULT 'insufficient_history',
    metrics_json      TEXT NOT NULL DEFAULT '{}',
    git_sha           TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS engine_trends (
    trend_release_id   TEXT NOT NULL,
    trend_id           TEXT NOT NULL,
    name_ru            TEXT NOT NULL,
    pattern            TEXT NOT NULL,
    domain_ids         TEXT NOT NULL DEFAULT '[]',
    confidence         REAL NOT NULL DEFAULT 0.0,
    lifecycle          TEXT NOT NULL DEFAULT 'insufficient_history',
    source_scope       TEXT NOT NULL DEFAULT 'cross_source',
    first_seen         TEXT NOT NULL DEFAULT '',
    last_seen          TEXT NOT NULL DEFAULT '',
    story_count        INTEGER NOT NULL DEFAULT 0,
    source_count       INTEGER NOT NULL DEFAULT 0,
    project_scores     TEXT NOT NULL DEFAULT '{}',
    evidence_story_ids TEXT NOT NULL DEFAULT '[]',
    counterpoints      TEXT NOT NULL DEFAULT '[]',
    review_status      TEXT NOT NULL DEFAULT 'pending',
    review_id          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (trend_release_id, trend_id)
);

CREATE TABLE IF NOT EXISTS engine_trend_stories (
    trend_release_id  TEXT NOT NULL,
    trend_id          TEXT NOT NULL,
    story_id          TEXT NOT NULL,
    membership_score  REAL NOT NULL DEFAULT 1.0,
    reason            TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (trend_release_id, trend_id, story_id)
);

CREATE TABLE IF NOT EXISTS engine_labels (
    label_id          TEXT PRIMARY KEY,
    target_kind       TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    release_id        TEXT NOT NULL,
    label             TEXT NOT NULL,
    note              TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_reviews (
    review_id         TEXT PRIMARY KEY,
    target_kind       TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    input_hash        TEXT NOT NULL,
    decision          TEXT NOT NULL,
    response_json     TEXT NOT NULL,
    valid             INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    UNIQUE(model, prompt_version, input_hash)
);

-- Quality is derived from immutable releases but stored separately, so the
-- operational Run journal never has to recompute expensive taxonomy metrics
-- for every historical release during an HTTP request.
CREATE TABLE IF NOT EXISTS engine_quality_reports (
    data_release_id   TEXT NOT NULL,
    story_release_id  TEXT NOT NULL,
    trend_release_id  TEXT NOT NULL,
    signal_release_id TEXT NOT NULL DEFAULT '',
    metrics_json      TEXT NOT NULL DEFAULT '{}',
    floors_json       TEXT NOT NULL DEFAULT '[]',
    passed            INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (data_release_id, story_release_id, trend_release_id)
);

CREATE TABLE IF NOT EXISTS embedding_vectors (
    model_hash       TEXT NOT NULL,
    input_hash       TEXT NOT NULL,
    model_name       TEXT NOT NULL,
    dimension        INTEGER NOT NULL,
    vector_json      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (model_hash, input_hash)
);

CREATE TABLE IF NOT EXISTS item_embedding_refs (
    data_release_id  TEXT NOT NULL,
    item_id          TEXT NOT NULL,
    model_hash       TEXT NOT NULL,
    input_hash       TEXT NOT NULL,
    PRIMARY KEY (data_release_id, item_id, model_hash)
);

CREATE TABLE IF NOT EXISTS radar_publications (
    publication_id   TEXT PRIMARY KEY,
    channel          TEXT NOT NULL,
    data_release_id  TEXT NOT NULL,
    story_release_id TEXT NOT NULL,
    trend_release_id TEXT NOT NULL,
    input_status     TEXT NOT NULL,
    allow_partial    INTEGER NOT NULL DEFAULT 0,
    previous_publication_id TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS published_channels (
    channel                TEXT PRIMARY KEY,
    current_publication_id TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publication_history (
    event_id        TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,
    action          TEXT NOT NULL,
    from_publication_id TEXT NOT NULL DEFAULT '',
    to_publication_id   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_lab_imports (
    legacy_kind     TEXT NOT NULL,
    legacy_id       TEXT NOT NULL,
    engine_id       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    PRIMARY KEY (legacy_kind, legacy_id)
);

CREATE TABLE IF NOT EXISTS signal_releases (
    signal_release_id TEXT PRIMARY KEY,
    data_release_id   TEXT NOT NULL,
    facet_release_id  TEXT NOT NULL,
    story_release_id  TEXT,
    date              TEXT NOT NULL,
    method            TEXT NOT NULL DEFAULT 'reddit_pulse_v1',
    params_hash       TEXT NOT NULL DEFAULT '',
    metrics_json      TEXT NOT NULL DEFAULT '{}',
    git_sha           TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL,
    signal_count      INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    finalized_at      TEXT
);

CREATE TABLE IF NOT EXISTS community_signals (
    signal_release_id        TEXT NOT NULL,
    signal_id                TEXT NOT NULL,
    item_id                  TEXT NOT NULL,
    subreddit                TEXT NOT NULL,
    pack_id                  TEXT NOT NULL DEFAULT '',
    signal_type              TEXT NOT NULL,
    title                    TEXT NOT NULL,
    discussion_url           TEXT NOT NULL DEFAULT '',
    target_url               TEXT NOT NULL DEFAULT '',
    pulse_score              REAL NOT NULL DEFAULT 0,
    subreddit_percentile     REAL NOT NULL DEFAULT 0,
    score_velocity           REAL NOT NULL DEFAULT 0,
    comment_velocity         REAL NOT NULL DEFAULT 0,
    discussion_depth         REAL NOT NULL DEFAULT 0,
    comment_score_ratio      REAL NOT NULL DEFAULT 0,
    cross_subreddit_repetition REAL NOT NULL DEFAULT 0,
    novelty                  REAL NOT NULL DEFAULT 0,
    domain_ids_json          TEXT NOT NULL DEFAULT '[]',
    theme_ids_json           TEXT NOT NULL DEFAULT '[]',
    pain_points_json         TEXT NOT NULL DEFAULT '[]',
    project_scores_json      TEXT NOT NULL DEFAULT '{}',
    linked_story_id          TEXT,
    mainstream_coverage_count INTEGER NOT NULL DEFAULT 0,
    perspective_gap          REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (signal_release_id, signal_id)
);

CREATE INDEX IF NOT EXISTS idx_release_items_provider
    ON release_items(release_id, provider);
CREATE INDEX IF NOT EXISTS idx_release_items_date
    ON release_items(release_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_story_pairs_release_score
    ON story_candidate_pairs(story_release_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_engine_story_items_item
    ON engine_story_items(story_release_id, item_id);
CREATE INDEX IF NOT EXISTS idx_engine_trend_stories_story
    ON engine_trend_stories(trend_release_id, story_id);
CREATE INDEX IF NOT EXISTS idx_item_embedding_refs_release
    ON item_embedding_refs(data_release_id, model_hash);
CREATE INDEX IF NOT EXISTS idx_signals_subreddit
    ON community_signals(signal_release_id, subreddit);
CREATE INDEX IF NOT EXISTS idx_signals_type
    ON community_signals(signal_release_id, signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_pulse
    ON community_signals(signal_release_id, pulse_score DESC);

CREATE TRIGGER IF NOT EXISTS immutable_release_header_update
BEFORE UPDATE ON data_releases
WHEN OLD.status = 'finalized'
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_header_delete
BEFORE DELETE ON data_releases
WHEN OLD.status = 'finalized'
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_items_insert
BEFORE INSERT ON release_items
WHEN EXISTS (
    SELECT 1 FROM data_releases
    WHERE release_id = NEW.release_id AND status = 'finalized'
)
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_items_update
BEFORE UPDATE ON release_items
WHEN EXISTS (
    SELECT 1 FROM data_releases
    WHERE release_id = OLD.release_id AND status = 'finalized'
)
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_items_delete
BEFORE DELETE ON release_items
WHEN EXISTS (
    SELECT 1 FROM data_releases
    WHERE release_id = OLD.release_id AND status = 'finalized'
)
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_observations_insert
BEFORE INSERT ON release_observations
WHEN EXISTS (
    SELECT 1 FROM data_releases
    WHERE release_id = NEW.release_id AND status = 'finalized'
)
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_observations_update
BEFORE UPDATE ON release_observations
WHEN EXISTS (
    SELECT 1 FROM data_releases
    WHERE release_id = OLD.release_id AND status = 'finalized'
)
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_observations_delete
BEFORE DELETE ON release_observations
WHEN EXISTS (
    SELECT 1 FROM data_releases
    WHERE release_id = OLD.release_id AND status = 'finalized'
)
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_health_insert
BEFORE INSERT ON release_source_health
WHEN EXISTS (
    SELECT 1 FROM data_releases
    WHERE release_id = NEW.release_id AND status = 'finalized'
)
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_health_update
BEFORE UPDATE ON release_source_health
WHEN EXISTS (
    SELECT 1 FROM data_releases
    WHERE release_id = OLD.release_id AND status = 'finalized'
)
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;

CREATE TRIGGER IF NOT EXISTS immutable_release_health_delete
BEFORE DELETE ON release_source_health
WHEN EXISTS (
    SELECT 1 FROM data_releases
    WHERE release_id = OLD.release_id AND status = 'finalized'
)
BEGIN
    SELECT RAISE(ABORT, 'finalized data release is immutable');
END;
"""


@dataclass(frozen=True)
class DataRelease:
    release_id: str
    profile: str
    dates: list[str]
    run_ids: list[str]
    input_status: str
    input_checksum: str
    item_count: int
    observation_count: int
    status: ReleaseStatus
    created_at: str


@dataclass(frozen=True)
class FacetRelease:
    facet_release_id: str
    data_release_id: str
    method: str
    status: AnalysisStatus
    metrics: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class StoryRelease:
    story_release_id: str
    facet_release_id: str
    method: str
    status: AnalysisStatus
    metrics: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class TrendRelease:
    trend_release_id: str
    story_release_id: str
    method: str
    window: str
    status: AnalysisStatus
    history_status: str
    metrics: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class Publication:
    publication_id: str
    channel: str
    data_release_id: str
    story_release_id: str
    trend_release_id: str
    input_status: str
    previous_publication_id: str
    created_at: str


@dataclass(frozen=True)
class FrozenItem:
    item_id: str
    provider: str
    source_cluster: str
    canonical_url: str
    target_url: str
    discussion_url: str
    title: str
    excerpt: str
    published_at: str
    snapshot_date: str
    content_scope: str
    source_section: str
    domain_ids: list[str]
    raw_engagement: dict[str, float]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PairCandidate:
    item_id_a: str
    item_id_b: str
    score: float
    decision: str
    reason: str
    features: dict[str, Any]


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def engine_db(path: Path = DEFAULT_ENGINE_DB_PATH) -> sqlite3.Connection:
    """Open the mutable Engine store with one writer-friendly SQLite policy.

    The API uses a separate read-only connection.  WAL lets that reader render
    Radar while a nightly Engine attempt writes a new immutable release;
    ``busy_timeout`` turns a short lock race into a bounded wait rather than an
    operational failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_engine(conn)
    return conn


def migrate_engine(conn: sqlite3.Connection) -> None:
    conn.executescript(_ENGINE_SCHEMA)
    _ensure_engine_column(
        conn,
        "engine_trends",
        "review_status",
        "TEXT NOT NULL DEFAULT 'pending'",
    )
    _ensure_engine_column(
        conn,
        "engine_trends",
        "review_id",
        "TEXT NOT NULL DEFAULT ''",
    )
    _ensure_engine_column(
        conn,
        "signal_releases",
        "method",
        "TEXT NOT NULL DEFAULT 'reddit_pulse_v1'",
    )
    _ensure_engine_column(
        conn,
        "signal_releases",
        "params_hash",
        "TEXT NOT NULL DEFAULT ''",
    )
    _ensure_engine_column(
        conn,
        "signal_releases",
        "metrics_json",
        "TEXT NOT NULL DEFAULT '{}'",
    )
    _ensure_engine_column(
        conn,
        "signal_releases",
        "git_sha",
        "TEXT NOT NULL DEFAULT ''",
    )
    conn.execute(f"PRAGMA user_version = {ENGINE_SCHEMA_VERSION}")
    conn.commit()


def _ensure_engine_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def store_quality_report(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    story_release_id: str,
    trend_release_id: str,
    signal_release_id: str | None,
    metrics: dict[str, Any],
    floors: list[Any],
) -> dict[str, Any]:
    """Persist an immutable-release quality result for fast operational reads."""
    floor_payloads = [asdict(floor) for floor in floors]
    passed = bool(floor_payloads) and all(bool(floor["passed"]) for floor in floor_payloads)
    report = {
        "passed": passed,
        "failed": [str(floor["metric"]) for floor in floor_payloads if not floor["passed"]],
        "metrics": metrics,
        "floors": floor_payloads,
    }
    with conn:
        conn.execute(
            """INSERT INTO engine_quality_reports (
                   data_release_id, story_release_id, trend_release_id, signal_release_id,
                   metrics_json, floors_json, passed, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(data_release_id, story_release_id, trend_release_id)
               DO UPDATE SET
                   signal_release_id = excluded.signal_release_id,
                   metrics_json = excluded.metrics_json,
                   floors_json = excluded.floors_json,
                   passed = excluded.passed,
                   created_at = excluded.created_at""",
            (
                data_release_id,
                story_release_id,
                trend_release_id,
                signal_release_id or "",
                json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                json.dumps(floor_payloads, ensure_ascii=False, sort_keys=True),
                int(passed),
                now_iso(),
            ),
        )
    return report


def open_corpus_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA query_only = ON")
    return conn


def open_engine_readonly(path: Path = DEFAULT_ENGINE_DB_PATH) -> sqlite3.Connection:
    """Open an existing derived store without creating or migrating it."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA query_only = ON")
    return conn


def import_legacy_lab(
    engine_conn: sqlite3.Connection,
    *,
    legacy_lab_path: Path,
    source_db_path: Path,
) -> dict[str, Any]:
    """Migrate safe legacy releases and register experiments that require rerun."""
    legacy_conn = sqlite3.connect(f"file:{legacy_lab_path}?mode=ro", uri=True)
    legacy_conn.row_factory = sqlite3.Row
    source_conn = open_corpus_readonly(source_db_path)
    imported_releases = 0
    skipped_releases = 0
    registered_experiments = 0
    release_mapping: dict[str, str] = {}
    try:
        tables = {
            str(row["name"])
            for row in legacy_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "data_releases" not in tables:
            raise ValueError("Legacy lab DB does not contain data_releases")
        current_source_checksum = _sha256_file(source_db_path)
        for row in legacy_conn.execute(
            "SELECT * FROM data_releases ORDER BY created_at"
        ).fetchall():
            legacy_id = str(row["release_id"])
            existing = engine_conn.execute(
                """
                SELECT engine_id, status FROM legacy_lab_imports
                WHERE legacy_kind = 'data_release' AND legacy_id = ?
                """,
                (legacy_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["engine_id"]):
                    release_mapping[legacy_id] = str(existing["engine_id"])
                skipped_releases += 1
                continue
            legacy_checksum = str(row["source_db_checksum"] or "")
            if not legacy_checksum or legacy_checksum != current_source_checksum:
                engine_conn.execute(
                    """
                    INSERT INTO legacy_lab_imports (
                        legacy_kind, legacy_id, status, metadata_json, created_at
                    ) VALUES ('data_release', ?, 'checksum_mismatch', ?, ?)
                    """,
                    (
                        legacy_id,
                        _json(
                            {
                                "legacy_checksum": legacy_checksum,
                                "current_checksum": current_source_checksum,
                            }
                        ),
                        now_iso(),
                    ),
                )
                engine_conn.commit()
                skipped_releases += 1
                continue
            run_ids = _json_list(row["run_ids_json"])
            try:
                release = create_data_release(
                    source_conn,
                    engine_conn,
                    source_db_path=source_db_path,
                    run_ids=run_ids,
                )
            except ValueError as exc:
                engine_conn.execute(
                    """
                    INSERT INTO legacy_lab_imports (
                        legacy_kind, legacy_id, status, metadata_json, created_at
                    ) VALUES ('data_release', ?, 'requires_manual_recovery', ?, ?)
                    """,
                    (legacy_id, _json({"error": str(exc)}), now_iso()),
                )
                engine_conn.commit()
                skipped_releases += 1
                continue
            release_mapping[legacy_id] = release.release_id
            engine_conn.execute(
                """
                INSERT INTO legacy_lab_imports (
                    legacy_kind, legacy_id, engine_id, status, metadata_json, created_at
                ) VALUES ('data_release', ?, ?, 'imported', ?, ?)
                """,
                (
                    legacy_id,
                    release.release_id,
                    _json({"run_ids": run_ids}),
                    now_iso(),
                ),
            )
            engine_conn.commit()
            imported_releases += 1

        if "cluster_experiments" in tables:
            for row in legacy_conn.execute(
                "SELECT * FROM cluster_experiments ORDER BY created_at"
            ).fetchall():
                experiment_id = str(row["experiment_id"])
                if engine_conn.execute(
                    """
                    SELECT 1 FROM legacy_lab_imports
                    WHERE legacy_kind = 'experiment' AND legacy_id = ?
                    """,
                    (experiment_id,),
                ).fetchone():
                    continue
                legacy_release_id = str(row["release_id"])
                engine_conn.execute(
                    """
                    INSERT INTO legacy_lab_imports (
                        legacy_kind, legacy_id, engine_id, status,
                        metadata_json, created_at
                    ) VALUES ('experiment', ?, ?, 'requires_rerun', ?, ?)
                    """,
                    (
                        experiment_id,
                        release_mapping.get(legacy_release_id, ""),
                        _json(
                            {
                                "legacy_release_id": legacy_release_id,
                                "method": str(row["method"]),
                                "prompt_version": str(row["prompt_version"] or ""),
                                "params": _json_dict(row["params_json"]),
                                "reason": (
                                    "legacy proposals used different semantics; "
                                    "rerun facets/stories/trends on the imported frozen release"
                                ),
                            }
                        ),
                        now_iso(),
                    ),
                )
                registered_experiments += 1
            engine_conn.commit()
    finally:
        source_conn.close()
        legacy_conn.close()
    return {
        "imported_releases": imported_releases,
        "skipped_releases": skipped_releases,
        "registered_experiments": registered_experiments,
        "release_mapping": release_mapping,
    }


def create_data_release(
    corpus_conn: sqlite3.Connection,
    engine_conn: sqlite3.Connection,
    *,
    source_db_path: Path,
    run_ids: list[str],
    config_hash: str = "",
) -> DataRelease:
    """Copy finalized collection runs into an immutable engine release."""
    if not run_ids:
        raise ValueError("At least one run ID is required")
    corpus_conn.row_factory = sqlite3.Row
    run_rows = _fetch_runs(corpus_conn, run_ids)
    missing = sorted(set(run_ids) - {str(row["run_id"]) for row in run_rows})
    if missing:
        raise ValueError(f"Collection runs not found: {', '.join(missing)}")
    running = [str(row["run_id"]) for row in run_rows if str(row["status"]) == "running"]
    if running:
        raise ValueError(f"Collection runs are not finalized: {', '.join(running)}")

    profiles = {str(row["profile"]) for row in run_rows}
    if len(profiles) != 1:
        raise ValueError("A data release must contain one profile")
    profile = next(iter(profiles))
    dates = sorted({str(row["snapshot_date"]) for row in run_rows})
    item_rows = _fetch_corpus_items(corpus_conn, run_ids)
    observation_rows = _fetch_table_for_runs(corpus_conn, "observations", run_ids)
    health_rows = _fetch_table_for_runs(corpus_conn, "source_health", run_ids)
    release_id = _next_release_id(engine_conn, profile, dates)
    created_at = now_iso()
    source_db_checksum = _sha256_file(source_db_path)

    item_payloads = [_normalize_item_row(row) for row in item_rows]
    observation_payloads = [_normalize_observation_row(row) for row in observation_rows]
    health_payloads = _normalize_release_health_payloads(health_rows)
    input_status = _release_input_status(
        profile=profile,
        run_rows=run_rows,
        item_payloads=item_payloads,
        health_payloads=health_payloads,
    )
    coverage = _source_coverage_from_payloads(health_payloads)
    input_checksum = _release_checksum(item_payloads, observation_payloads, health_payloads)

    try:
        engine_conn.execute("BEGIN IMMEDIATE")
        engine_conn.execute(
            """INSERT INTO data_releases
               (release_id, profile, dates_json, run_ids_json, source_db_path,
                source_db_checksum, input_checksum, input_status, source_coverage_json,
                config_hash, git_sha, item_count, observation_count, status,
                created_at, finalized_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'building', ?, '')""",
            (
                release_id,
                profile,
                _json(dates),
                _json(run_ids),
                str(source_db_path),
                source_db_checksum,
                input_checksum,
                input_status,
                _json(coverage),
                config_hash,
                _git_sha(),
                len(item_payloads),
                len(observation_payloads),
                created_at,
            ),
        )
        engine_conn.executemany(
            """INSERT INTO release_items
               (release_id, item_id, provider, source_cluster, external_id, canonical_url,
                title, summary_ru, excerpt, author, published_at, observed_at,
                snapshot_date, language, content_scope, source_section, domain_ids,
                discussion_url, target_url, dedupe_group_id, evidence_refs,
                raw_engagement, metadata, row_checksum)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(release_id, *payload, _row_checksum(payload)) for payload in item_payloads],
        )
        engine_conn.executemany(
            """INSERT INTO release_observations
               (release_id, run_id, item_id, observed_at, source_rank,
                engagement_percentile, score_delta, comments_delta, row_checksum)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(release_id, *payload, _row_checksum(payload)) for payload in observation_payloads],
        )
        engine_conn.executemany(
            """INSERT INTO release_source_health
               (release_id, run_id, source_id, provider, cluster, status, count,
                duration_sec, error_code, message, row_checksum)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(release_id, *payload, _row_checksum(payload)) for payload in health_payloads],
        )
        finalized_at = now_iso()
        engine_conn.execute(
            "UPDATE data_releases SET status = 'finalized', finalized_at = ? WHERE release_id = ?",
            (finalized_at, release_id),
        )
        engine_conn.commit()
    except Exception:
        engine_conn.rollback()
        raise

    return DataRelease(
        release_id=release_id,
        profile=profile,
        dates=dates,
        run_ids=list(run_ids),
        input_status=input_status,
        input_checksum=input_checksum,
        item_count=len(item_payloads),
        observation_count=len(observation_payloads),
        status="finalized",
        created_at=created_at,
    )


def get_data_release(conn: sqlite3.Connection, release_id: str) -> DataRelease | None:
    row = conn.execute("SELECT * FROM data_releases WHERE release_id = ?", (release_id,)).fetchone()
    if row is None:
        return None
    return DataRelease(
        release_id=str(row["release_id"]),
        profile=str(row["profile"]),
        dates=_json_list(row["dates_json"]),
        run_ids=_json_list(row["run_ids_json"]),
        input_status=str(row["input_status"]),
        input_checksum=str(row["input_checksum"]),
        item_count=int(row["item_count"]),
        observation_count=int(row["observation_count"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        created_at=str(row["created_at"]),
    )


def list_data_releases(conn: sqlite3.Connection) -> list[DataRelease]:
    rows = conn.execute("SELECT release_id FROM data_releases ORDER BY created_at DESC").fetchall()
    return [
        release
        for row in rows
        if (release := get_data_release(conn, str(row["release_id"]))) is not None
    ]


def verify_data_release(conn: sqlite3.Connection, release_id: str) -> bool:
    release = get_data_release(conn, release_id)
    if release is None or release.status != "finalized":
        return False
    item_payloads = [
        tuple(row[column] for column in _RELEASE_ITEM_PAYLOAD_COLUMNS)
        for row in conn.execute(
            "SELECT * FROM release_items WHERE release_id = ? ORDER BY item_id",
            (release_id,),
        ).fetchall()
    ]
    observation_payloads = [
        tuple(row[column] for column in _RELEASE_OBSERVATION_PAYLOAD_COLUMNS)
        for row in conn.execute(
            """SELECT * FROM release_observations
               WHERE release_id = ? ORDER BY run_id, item_id""",
            (release_id,),
        ).fetchall()
    ]
    health_payloads = [
        tuple(row[column] for column in _RELEASE_HEALTH_PAYLOAD_COLUMNS)
        for row in conn.execute(
            """SELECT * FROM release_source_health
               WHERE release_id = ? ORDER BY run_id, source_id""",
            (release_id,),
        ).fetchall()
    ]
    if any(
        str(row["row_checksum"]) != _row_checksum(payload)
        for row, payload in zip(
            conn.execute(
                "SELECT * FROM release_items WHERE release_id = ? ORDER BY item_id",
                (release_id,),
            ).fetchall(),
            item_payloads,
            strict=True,
        )
    ):
        return False
    return (
        _release_checksum(item_payloads, observation_payloads, health_payloads)
        == release.input_checksum
    )


def create_facet_release(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    method: str = "deterministic_facets_v1",
    model: str = "deterministic-facets-v1",
    prompt_version: str = "",
    params: dict[str, Any] | None = None,
    theme_catalog: dict[str, list[str]] | None = None,
) -> FacetRelease:
    if not verify_data_release(conn, data_release_id):
        raise ValueError(f"Data release checksum failed: {data_release_id}")
    items = load_frozen_items(conn, data_release_id)
    params = {"use_spacy": True, **(params or {})}
    params_hash = _hash_json(params)
    created_at = now_iso()
    facet_release_id = _stable_id(
        "facets", data_release_id, method, model, prompt_version, params_hash, created_at
    )
    content_items = [_to_content_item(item) for item in items]
    signals = build_deterministic_item_signals(
        content_items,
        theme_catalog=theme_catalog,
        analyzed_at=created_at,
    )
    signals_by_id = {signal.item_id: signal for signal in signals}

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO facet_releases
               (facet_release_id, data_release_id, method, model, prompt_version,
                params_hash, status, metrics_json, git_sha, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'building', '{}', ?, ?)""",
            (
                facet_release_id,
                data_release_id,
                method,
                model,
                prompt_version,
                params_hash,
                _git_sha(),
                created_at,
            ),
        )
        entity_backends: dict[str, int] = defaultdict(int)
        for item in items:
            signal = signals_by_id[item.item_id]
            fallback_entities = sorted(_meaningful_entities(item.title))
            event_frame, entities, entity_backend = extract_structured_event_frame(
                title=item.title,
                excerpt=item.excerpt,
                event_date=_item_date(item),
                fallback_entities=fallback_entities,
                use_spacy=bool(params["use_spacy"]),
            )
            entity_backends[entity_backend] += 1
            conn.execute(
                """INSERT INTO item_facets
                   (facet_release_id, item_id, domain_ids, theme_ids, candidate_themes,
                    pain_points, entities, event_frame_json, goal_relevance, summary_ru,
                    evidence_scope)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    facet_release_id,
                    item.item_id,
                    _json(signal.domain_ids),
                    _json(signal.theme_ids),
                    _json(signal.candidate_themes),
                    _json(signal.pain_points),
                    _json(entities),
                    _json(event_frame),
                    _json(signal.goal_relevance),
                    signal.summary_ru,
                    signal.evidence_scope,
                ),
            )
        metrics = {
            "item_count": len(items),
            "coverage": round(len(signals) / max(len(items), 1), 4),
            "entity_coverage": round(
                sum(1 for item in items if _meaningful_entities(item.title)) / max(len(items), 1),
                4,
            ),
            "entity_backends": dict(sorted(entity_backends.items())),
        }
        conn.execute(
            """UPDATE facet_releases
               SET status = 'evaluated', metrics_json = ?
               WHERE facet_release_id = ?""",
            (_json(metrics), facet_release_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return FacetRelease(
        facet_release_id=facet_release_id,
        data_release_id=data_release_id,
        method=method,
        status="evaluated",
        metrics=metrics,
        created_at=created_at,
    )


def get_facet_release(conn: sqlite3.Connection, facet_release_id: str) -> FacetRelease | None:
    row = conn.execute(
        "SELECT * FROM facet_releases WHERE facet_release_id = ?",
        (facet_release_id,),
    ).fetchone()
    if row is None:
        return None
    return FacetRelease(
        facet_release_id=str(row["facet_release_id"]),
        data_release_id=str(row["data_release_id"]),
        method=str(row["method"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        metrics=_json_dict(row["metrics_json"]),
        created_at=str(row["created_at"]),
    )


def load_frozen_items(conn: sqlite3.Connection, data_release_id: str) -> list[FrozenItem]:
    rows = conn.execute(
        "SELECT * FROM release_items WHERE release_id = ? ORDER BY item_id",
        (data_release_id,),
    ).fetchall()
    return [
        FrozenItem(
            item_id=str(row["item_id"]),
            provider=str(row["provider"]),
            source_cluster=str(row["source_cluster"]),
            canonical_url=str(row["canonical_url"]),
            target_url=str(row["target_url"]),
            discussion_url=str(row["discussion_url"]),
            title=str(row["title"]),
            excerpt=str(row["excerpt"]),
            published_at=str(row["published_at"] or ""),
            snapshot_date=str(row["snapshot_date"]),
            content_scope=str(row["content_scope"]),
            source_section=str(row["source_section"]),
            domain_ids=_json_list(row["domain_ids"], ["other"]),
            raw_engagement=_json_float_dict(row["raw_engagement"]),
            metadata=_json_dict(row["metadata"]),
        )
        for row in rows
    ]


def cache_release_embeddings(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    model_revision: str = "default",
    batch_size: int = 32,
) -> dict[str, Any]:
    """Generate missing local vectors and attach immutable item refs to a release."""
    if not verify_data_release(conn, data_release_id):
        raise ValueError(f"Data release checksum failed: {data_release_id}")
    items = load_frozen_items(conn, data_release_id)
    model_hash = _stable_id("embedding_model", model_name, model_revision)
    inputs = {item.item_id: _embedding_input(item) for item in items}
    input_hashes = {item_id: _hash_json({"text": text}) for item_id, text in inputs.items()}
    existing = {
        str(row["input_hash"])
        for row in conn.execute(
            "SELECT input_hash FROM embedding_vectors WHERE model_hash = ?",
            (model_hash,),
        ).fetchall()
    }
    missing_hashes = sorted(set(input_hashes.values()) - existing)
    text_by_hash = {input_hashes[item_id]: text for item_id, text in inputs.items()}
    # Embedding vectors are global cache entries while item refs are release
    # specific.  A later DataRelease often has no new text hashes, but still
    # needs its refs written below.  In that case do not call a backend with an
    # empty batch: model2vec delegates to NumPy and raises ``need at least one
    # array to concatenate`` for ``encode([])``.
    vectors = (
        encode_passages(
            [text_by_hash[input_hash] for input_hash in missing_hashes],
            model_name=model_name,
            batch_size=batch_size,
        )
        if missing_hashes
        else []
    )
    if len(vectors) != len(missing_hashes):
        raise ValueError("Embedding model returned an unexpected vector count")

    created_at = now_iso()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            """
            INSERT OR IGNORE INTO embedding_vectors (
                model_hash, input_hash, model_name, dimension, vector_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    model_hash,
                    input_hash,
                    model_name,
                    len(vector),
                    _json(vector),
                    created_at,
                )
                for input_hash, vector in zip(missing_hashes, vectors, strict=True)
            ],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO item_embedding_refs (
                data_release_id, item_id, model_hash, input_hash
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (data_release_id, item_id, model_hash, input_hashes[item_id])
                for item_id in sorted(inputs)
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "data_release_id": data_release_id,
        "model_name": model_name,
        "model_hash": model_hash,
        "item_count": len(items),
        "new_vector_count": len(missing_hashes),
        "cached_vector_count": len(items) - len(missing_hashes),
    }


def load_release_embeddings(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    model_revision: str = "default",
    item_ids: Iterable[str] | None = None,
) -> dict[str, list[float]]:
    model_hash = _stable_id("embedding_model", model_name, model_revision)
    selected_item_ids = sorted(set(item_ids)) if item_ids is not None else None
    if selected_item_ids == []:
        return {}
    item_filter = ""
    args: list[Any] = [data_release_id, model_hash]
    if selected_item_ids is not None:
        placeholders = ",".join("?" for _ in selected_item_ids)
        item_filter = f" AND r.item_id IN ({placeholders})"
        args.extend(selected_item_ids)
    rows = conn.execute(
        f"""
        SELECT r.item_id, v.vector_json
        FROM item_embedding_refs AS r
        JOIN embedding_vectors AS v
          ON v.model_hash = r.model_hash
         AND v.input_hash = r.input_hash
        WHERE r.data_release_id = ? AND r.model_hash = ?{item_filter}
        ORDER BY r.item_id
        """,
        args,
    ).fetchall()
    return {
        str(row["item_id"]): [float(value) for value in (_json_value(row["vector_json"]) or [])]
        for row in rows
    }


def _embedding_input(item: FrozenItem) -> str:
    title = " ".join(item.title.split())
    excerpt = " ".join(item.excerpt.split())[:1200]
    return f"{title}\n{excerpt}".strip()


_RELEASE_ITEM_PAYLOAD_COLUMNS = (
    "item_id",
    "provider",
    "source_cluster",
    "external_id",
    "canonical_url",
    "title",
    "summary_ru",
    "excerpt",
    "author",
    "published_at",
    "observed_at",
    "snapshot_date",
    "language",
    "content_scope",
    "source_section",
    "domain_ids",
    "discussion_url",
    "target_url",
    "dedupe_group_id",
    "evidence_refs",
    "raw_engagement",
    "metadata",
)
_RELEASE_OBSERVATION_PAYLOAD_COLUMNS = (
    "run_id",
    "item_id",
    "observed_at",
    "source_rank",
    "engagement_percentile",
    "score_delta",
    "comments_delta",
)
_RELEASE_HEALTH_PAYLOAD_COLUMNS = (
    "run_id",
    "source_id",
    "provider",
    "cluster",
    "status",
    "count",
    "duration_sec",
    "error_code",
    "message",
)


def _fetch_runs(conn: sqlite3.Connection, run_ids: list[str]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in run_ids)
    return conn.execute(
        f"SELECT * FROM runs WHERE run_id IN ({placeholders}) ORDER BY snapshot_date, run_id",
        run_ids,
    ).fetchall()


def _fetch_corpus_items(conn: sqlite3.Connection, run_ids: list[str]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in run_ids)
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(items)").fetchall()}

    def expr(column: str, default: str) -> str:
        return f"i.{column}" if column in columns else f"{default} AS {column}"

    select = ", ".join(
        [
            "i.item_id",
            "i.provider",
            "i.source_cluster",
            "i.external_id",
            "i.canonical_url",
            "i.title",
            expr("summary_ru", "''"),
            expr("excerpt", "''"),
            expr("author", "''"),
            expr("published_at", "NULL"),
            expr("observed_at", "''"),
            expr("snapshot_date", "''"),
            expr("language", "'en'"),
            expr("content_scope", "'headline'"),
            expr("source_section", "''"),
            expr("domain_ids", "'[\"other\"]'"),
            expr("discussion_url", "''"),
            expr("target_url", "''"),
            expr("dedupe_group_id", "''"),
            expr("evidence_refs", "'[]'"),
            expr("raw_engagement", "'{}'"),
            expr("metadata", "'{}'"),
        ]
    )
    return conn.execute(
        f"""SELECT DISTINCT {select}
            FROM items i
            JOIN observations o ON o.item_id = i.item_id
            WHERE o.run_id IN ({placeholders})
            ORDER BY i.item_id""",
        run_ids,
    ).fetchall()


def _fetch_table_for_runs(
    conn: sqlite3.Connection, table: str, run_ids: list[str]
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in run_ids)
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if not exists:
        return []
    order_by = {
        "observations": "run_id, item_id",
        "source_health": "run_id, source_id",
    }.get(table, "run_id")
    return conn.execute(
        f"SELECT * FROM {table} WHERE run_id IN ({placeholders}) ORDER BY {order_by}",
        run_ids,
    ).fetchall()


def _normalize_item_row(row: sqlite3.Row) -> tuple[Any, ...]:
    return tuple(_normalize_scalar(row[column]) for column in _RELEASE_ITEM_PAYLOAD_COLUMNS)


def _normalize_observation_row(row: sqlite3.Row) -> tuple[Any, ...]:
    return tuple(
        _normalize_scalar(_row_value(row, column, _observation_default(column)))
        for column in _RELEASE_OBSERVATION_PAYLOAD_COLUMNS
    )


def _normalize_health_row(row: sqlite3.Row) -> tuple[Any, ...]:
    return tuple(
        _normalize_scalar(_row_value(row, column, _health_default(column)))
        for column in _RELEASE_HEALTH_PAYLOAD_COLUMNS
    )


def _normalize_release_health_payloads(rows: list[sqlite3.Row]) -> list[tuple[Any, ...]]:
    payloads: list[tuple[Any, ...]] = []
    for row in rows:
        payload = list(_normalize_health_row(row))
        source_id = str(payload[1])
        provider = str(payload[2])
        status = str(payload[4])
        count = int(payload[5] or 0)
        source = SOURCES.get(source_id) or next(
            (definition for definition in SOURCES.values() if definition.provider == provider),
            None,
        )
        expected_min = source.expected_min_items if source else 0
        if status == "ok" and expected_min > 0 and count == 0:
            payload[4] = "empty"
            payload[8] = _append_health_message(
                str(payload[8] or ""),
                f"expected at least {expected_min} item(s), got 0",
            )
        elif status == "ok" and expected_min > 0 and count < expected_min:
            payload[4] = "degraded"
            payload[8] = _append_health_message(
                str(payload[8] or ""),
                f"expected at least {expected_min} item(s), got {count}",
            )
        payloads.append(tuple(payload))
    return payloads


def _append_health_message(message: str, addition: str) -> str:
    return f"{message}; {addition}" if message else addition


def _profile_expects_voice_cluster(profile: str) -> bool:
    if profile not in {"broad", "ai-native"}:
        return False
    return any(
        definition.enabled_by_default
        and definition.expected_min_items > 0
        and definition.cluster == "voices"
        for definition in SOURCES.values()
    )


def _release_input_status(
    *,
    profile: str,
    run_rows: list[sqlite3.Row],
    item_payloads: list[tuple[Any, ...]],
    health_payloads: list[tuple[Any, ...]],
) -> str:
    run_complete = all(str(row["status"]) == "complete" for row in run_rows)
    if not run_complete:
        return "partial"
    cluster_counts = Counter(str(payload[2]) for payload in item_payloads)
    expected_voices = _profile_expects_voice_cluster(profile) or any(
        str(payload[3]) == "voices" for payload in health_payloads
    )
    if (
        profile in {"broad", "ai-native"}
        and expected_voices
        and cluster_counts.get("voices", 0) == 0
    ):
        return "partial"
    # Some adapters record both an aggregate provider row (``reddit``) and
    # granular section rows (``reddit:LocalLLaMA``).  An aggregate zero is a
    # reporting artifact, not missing voice coverage, once at least one real
    # section from that provider succeeded in the immutable window.
    healthy_granular_providers = {
        str(payload[2])
        for payload in health_payloads
        if (
            str(payload[4]) == "ok"
            and int(payload[5] or 0) > 0
            and str(payload[1]) != str(payload[2])
        )
    }
    required_source_empty = any(
        str(payload[3]) == "voices"
        and str(payload[4]) in {"empty", "degraded"}
        and not (
            str(payload[1]) == str(payload[2]) and str(payload[2]) in healthy_granular_providers
        )
        for payload in health_payloads
    )
    return "partial" if required_source_empty else "complete"


def _observation_default(column: str) -> Any:
    return {
        "source_rank": None,
        "engagement_percentile": 0.0,
        "score_delta": None,
        "comments_delta": None,
    }.get(column, "")


def _health_default(column: str) -> Any:
    return {"count": 0, "duration_sec": 0.0, "error_code": None}.get(column, "")


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 8)
    return value


def _source_coverage(rows: list[sqlite3.Row]) -> dict[str, int]:
    coverage: dict[str, int] = defaultdict(int)
    for row in rows:
        if str(_row_value(row, "status", "")) == "ok":
            key = f"{_row_value(row, 'provider', '')}:{_row_value(row, 'source_id', '')}"
            coverage[key] += int(_row_value(row, "count", 0) or 0)
    return dict(sorted(coverage.items()))


def _source_coverage_from_payloads(payloads: list[tuple[Any, ...]]) -> dict[str, int]:
    coverage: dict[str, int] = defaultdict(int)
    for payload in payloads:
        if str(payload[4]) == "ok":
            key = f"{payload[2]}:{payload[1]}"
            coverage[key] += int(payload[5] or 0)
    return dict(sorted(coverage.items()))


def _release_checksum(
    item_payloads: list[tuple[Any, ...]],
    observation_payloads: list[tuple[Any, ...]],
    health_payloads: list[tuple[Any, ...]],
) -> str:
    return _hash_json(
        {
            "items": [list(payload) for payload in item_payloads],
            "observations": [list(payload) for payload in observation_payloads],
            "source_health": [list(payload) for payload in health_payloads],
        }
    )


def _row_checksum(payload: tuple[Any, ...]) -> str:
    return _hash_json(list(payload))


def _next_release_id(conn: sqlite3.Connection, profile: str, dates: list[str]) -> str:
    date_part = dates[-1] if len(dates) == 1 else f"{dates[0]}_{dates[-1]}"
    prefix = f"{date_part}-{profile}-r"
    rows = conn.execute(
        "SELECT release_id FROM data_releases WHERE release_id LIKE ?",
        (f"{prefix}%",),
    ).fetchall()
    versions = []
    for row in rows:
        suffix = str(row["release_id"]).removeprefix(prefix)
        if suffix.isdigit():
            versions.append(int(suffix))
    return f"{prefix}{max(versions, default=0) + 1}"


def _to_content_item(item: FrozenItem) -> ContentItem:
    return ContentItem(
        item_id=item.item_id,
        provider=item.provider,
        source_cluster=item.source_cluster,  # type: ignore[arg-type]
        external_id=item.item_id,
        canonical_url=item.canonical_url,
        title=item.title,
        excerpt=item.excerpt,
        published_at=item.published_at or None,
        observed_at=item.snapshot_date,
        snapshot_date=item.snapshot_date,
        content_scope=item.content_scope,  # type: ignore[arg-type]
        source_section=item.source_section,
        domain_ids=item.domain_ids,
        discussion_url=item.discussion_url,
        target_url=item.target_url,
        raw_engagement=item.raw_engagement,
        metadata=item.metadata,
    )


_GENERIC_ENTITIES = {
    "ai",
    "ceo",
    "cfo",
    "gop",
    "uk",
    "u.s",
    "u.s.",
    "us",
    "usa",
}


def _meaningful_entities(title: str) -> set[str]:
    return {
        entity
        for entity in extract_entities(title)
        if entity not in _GENERIC_ENTITIES and len(entity) >= 2
    }


def _event_frame(item: FrozenItem, entities: list[str]) -> dict[str, Any]:
    numbers = sorted(
        token
        for token in item.title.replace(",", "").split()
        if any(char.isdigit() for char in token)
    )
    return {
        "actors": entities[:8],
        "action": "",
        "object": "",
        "geography": [],
        "event_date": _item_date(item),
        "numbers": numbers[:8],
    }


def _sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _hash_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_list(raw: Any, fallback: list[str] | None = None) -> list[str]:
    value = _json_value(raw)
    if isinstance(value, list):
        return [str(item) for item in value]
    return list(fallback or [])


def _json_dict(raw: Any) -> dict[str, Any]:
    value = _json_value(raw)
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}


def _json_float_dict(raw: Any) -> dict[str, float]:
    return {
        key: float(value or 0)
        for key, value in _json_dict(raw).items()
        if isinstance(value, int | float)
    }


def _json_value(raw: Any) -> Any:
    if isinstance(raw, dict | list):
        return raw
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _row_value(row: sqlite3.Row, key: str, default: Any) -> Any:
    try:
        value = row[key]
    except IndexError:
        return default
    return value if value is not None else default


def _select_engine_items(
    items: list[FrozenItem],
    facets: dict[str, dict[str, Any]],
    limit: int,
) -> list[FrozenItem]:
    """Stratified seeds plus likely neighbours, so a small lab slice tests clustering."""
    item_by_id = {item.item_id: item for item in items}
    item_norms = {item.item_id: normalize_title(item.title, item.provider) for item in items}
    item_tokens = {item_id: extract_tokens(norm) for item_id, norm in item_norms.items()}
    item_urls = {item.item_id: _item_urls(item) for item in items}
    token_index: dict[str, set[str]] = defaultdict(set)
    url_index: dict[str, set[str]] = defaultdict(set)
    for item_id, tokens in item_tokens.items():
        for token in tokens:
            token_index[token].add(item_id)
    for item_id, urls in item_urls.items():
        for url in urls:
            url_index[url].add(item_id)

    strata: dict[str, list[FrozenItem]] = defaultdict(list)
    for item in items:
        domains = _json_list(
            facets.get(item.item_id, {}).get("domain_ids"),
            item.domain_ids or ["other"],
        )
        primary_domain = domains[0] if domains else "other"
        key = f"{primary_domain}|{item.source_cluster}|{item.snapshot_date}"
        strata[key].append(item)
    for values in strata.values():
        values.sort(key=lambda item: (item.provider, item.item_id))
    ordered: list[FrozenItem] = []
    offsets = {key: 0 for key in strata}
    keys = sorted(strata)
    while len(ordered) < len(items):
        progressed = False
        for key in keys:
            offset = offsets[key]
            if offset >= len(strata[key]):
                continue
            ordered.append(strata[key][offset])
            offsets[key] += 1
            progressed = True
        if not progressed:
            break
    seed_count = min(len(ordered), max(1, limit // 2))
    selected = {item.item_id: item for item in ordered[:seed_count]}
    for seed in ordered[:seed_count]:
        seed_norm = item_norms[seed.item_id]
        seed_tokens = item_tokens[seed.item_id]
        seed_urls = item_urls[seed.item_id]
        candidate_ids: set[str] = set()
        for url in seed_urls:
            candidate_ids.update(url_index.get(url, set()))
        token_hits: Counter[str] = Counter()
        for token in seed_tokens:
            token_hits.update(token_index.get(token, set()))
        candidate_ids.update(item_id for item_id, count in token_hits.items() if count >= 2)
        candidate_ids.discard(seed.item_id)
        neighbours: list[tuple[int, float, str, FrozenItem]] = []
        for candidate_id in sorted(candidate_ids):
            if candidate_id in selected:
                continue
            candidate = item_by_id[candidate_id]
            shared_url = bool(seed_urls & item_urls[candidate.item_id])
            shared_tokens = len(seed_tokens & item_tokens[candidate.item_id])
            if not shared_url and shared_tokens < 2:
                continue
            candidate_norm = item_norms[candidate.item_id]
            title_score = fuzz.token_set_ratio(seed_norm, candidate_norm) / 100.0
            if not shared_url and (shared_tokens < 2 or title_score < 0.65):
                continue
            if _date_distance_days(seed, candidate) > 14:
                continue
            neighbours.append(
                (
                    int(shared_url),
                    title_score,
                    candidate.item_id,
                    candidate,
                )
            )
        if neighbours and len(selected) < limit:
            best = max(
                neighbours,
                key=lambda value: (
                    value[0],
                    value[1],
                    seed.provider != value[3].provider,
                    value[2],
                ),
            )[3]
            selected[best.item_id] = best
    for item in ordered:
        if len(selected) >= limit:
            break
        selected.setdefault(item.item_id, item)
    return sorted(selected.values(), key=lambda item: item.item_id)


# Единый канонический набор дефолтов story-скоринга (Фаза 3). CLI и experiments
# могут переопределять отдельные ключи как явные экспериментальные ручки, но базовая
# точка всегда берётся отсюда, чтобы не держать три расходящихся набора констант.
DEFAULT_STORY_PARAMS: dict[str, Any] = {
    "auto_merge_threshold": 0.82,
    "review_threshold": 0.62,
    "max_token_df_ratio": 0.2,
    # Sparse inverted indexes are candidate retrieval, not an all-pairs
    # clustering engine.  Common actors/tokens (AI, OpenAI, US) must not
    # expand into millions of unrelated pair scores on a daily release.
    "max_sparse_bucket_size": 32,
    "max_candidate_pairs": 100_000,
    "embedding_model": DEFAULT_EMBEDDING_MODEL,
    "embedding_revision": "default",
    "dense_top_k": 12,
    "dense_candidate_threshold": 0.68,
    "dense_auto_threshold": 0.88,
    "dense_review_threshold": 0.72,
    "semantic_dedup_threshold": 0.92,
    "semantic_dedup_max_days": 7,
    "review_model": "qwen3.6-flash",
    "review_prompt_version": STORY_REVIEW_PROMPT_VERSION,
    "llm_merge_min_confidence": 0.85,
    "exclude_routine": True,
}


def _story_generation_params(params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {**DEFAULT_STORY_PARAMS, **(params or {})}


def create_story_release(
    conn: sqlite3.Connection,
    *,
    facet_release_id: str,
    method: str = DEFAULT_STORY_METHOD,
    params: dict[str, Any] | None = None,
    limit: int = 0,
    domain: str | None = None,
) -> StoryRelease:
    """Build a conservative, reproducible story version from frozen items."""
    facet_release = get_facet_release(conn, facet_release_id)
    if facet_release is None or facet_release.status not in {"evaluated", "published"}:
        raise ValueError(f"Facet release is not evaluated: {facet_release_id}")
    if not verify_data_release(conn, facet_release.data_release_id):
        raise ValueError(f"Data release checksum failed: {facet_release.data_release_id}")
    params = _story_generation_params(params)
    items = load_frozen_items(conn, facet_release.data_release_id)
    facets = _load_item_facets(conn, facet_release_id)
    if domain:
        items = [
            item
            for item in items
            if domain
            in set(item.domain_ids + _json_list(facets.get(item.item_id, {}).get("domain_ids"), []))
        ]
    # Фаза 6: рутина (счёта, травмы, депт-чарты) остаётся в /news, но не участвует в
    # story/trend-слоях. Флаг входит в params_hash → релиз воспроизводим.
    if params.get("exclude_routine", True):
        items = [item for item in items if not is_routine_beat(item.title, item.source_section)]
    if limit > 0:
        items = _select_engine_items(items, facets, limit)
    embeddings = load_release_embeddings(
        conn,
        data_release_id=facet_release.data_release_id,
        model_name=str(params["embedding_model"]),
        model_revision=str(params["embedding_revision"]),
        item_ids=(item.item_id for item in items),
    )
    params_hash = _hash_json({**params, "limit": limit, "domain": domain or ""})
    created_at = now_iso()
    story_release_id = _stable_id("stories", facet_release_id, method, params_hash, created_at)
    candidates = generate_story_candidates(
        items,
        facets,
        params=params,
        embeddings=embeddings,
    )
    candidates = apply_cached_story_reviews(
        conn,
        candidates=candidates,
        items=items,
        facets=facets,
        model=str(params["review_model"]),
        prompt_version=str(params["review_prompt_version"]),
        min_merge_confidence=float(params["llm_merge_min_confidence"]),
    )
    groups = _constrained_story_groups(items, candidates, params=params)
    stories = _build_engine_stories(groups, facets)
    stories, redirects = _reconcile_story_identity(
        conn,
        data_release_id=facet_release.data_release_id,
        stories=stories,
    )
    metrics = _story_release_metrics(stories, candidates, len(items))
    metrics["embedding_model"] = str(params["embedding_model"])
    metrics["embedding_coverage"] = round(
        len(embeddings) / max(len(items), 1),
        4,
    )

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO story_releases
               (story_release_id, facet_release_id, method, params_hash, status,
                metrics_json, git_sha, created_at)
               VALUES (?, ?, ?, ?, 'building', '{}', ?, ?)""",
            (
                story_release_id,
                facet_release_id,
                method,
                params_hash,
                _git_sha(),
                created_at,
            ),
        )
        conn.executemany(
            """INSERT INTO story_candidate_pairs
               (story_release_id, item_id_a, item_id_b, score, decision,
                features_json, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    story_release_id,
                    candidate.item_id_a,
                    candidate.item_id_b,
                    candidate.score,
                    candidate.decision,
                    _json(candidate.features),
                    candidate.reason,
                )
                for candidate in candidates
            ],
        )
        for story, memberships in stories:
            conn.execute(
                """INSERT INTO engine_stories
                   (story_release_id, story_id, canonical_key, title, summary_ru,
                    domain_ids, theme_ids, project_scores, first_seen, last_seen,
                    confidence, source_count, item_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story_release_id,
                    story["story_id"],
                    story["canonical_key"],
                    story["title"],
                    story["summary_ru"],
                    _json(story["domain_ids"]),
                    _json(story["theme_ids"]),
                    _json(story["project_scores"]),
                    story["first_seen"],
                    story["last_seen"],
                    story["confidence"],
                    story["source_count"],
                    story["item_count"],
                ),
            )
            conn.executemany(
                """INSERT INTO engine_story_items
                   (story_release_id, story_id, item_id, membership_score,
                    membership_reason)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        story_release_id,
                        story["story_id"],
                        item_id,
                        score,
                        reason,
                    )
                    for item_id, score, reason in memberships
                ],
            )
        conn.executemany(
            """
            INSERT INTO story_redirects (
                story_release_id, old_story_id, new_story_id, reason
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (story_release_id, old_story_id, new_story_id, reason)
                for old_story_id, new_story_id, reason in redirects
            ],
        )
        conn.execute(
            """UPDATE story_releases
               SET status = 'evaluated', metrics_json = ?
               WHERE story_release_id = ?""",
            (_json(metrics), story_release_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return StoryRelease(
        story_release_id=story_release_id,
        facet_release_id=facet_release_id,
        method=method,
        status="evaluated",
        metrics=metrics,
        created_at=created_at,
    )


def get_story_release(conn: sqlite3.Connection, story_release_id: str) -> StoryRelease | None:
    row = conn.execute(
        "SELECT * FROM story_releases WHERE story_release_id = ?",
        (story_release_id,),
    ).fetchone()
    if row is None:
        return None
    return StoryRelease(
        story_release_id=str(row["story_release_id"]),
        facet_release_id=str(row["facet_release_id"]),
        method=str(row["method"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        metrics=_json_dict(row["metrics_json"]),
        created_at=str(row["created_at"]),
    )


def compare_story_engine_variants(
    conn: sqlite3.Connection,
    *,
    facet_release_id: str,
    base_params: dict[str, Any] | None = None,
    limit: int = 300,
    domain: str | None = None,
    sample_limit: int = 5,
) -> dict[str, Any]:
    """Run reproducible Story Engine A/B attempts on one frozen FacetRelease."""
    base_params = dict(base_params or {})
    variants: list[tuple[str, dict[str, Any]]] = [
        (
            "baseline_sparse_dense",
            {
                "near_duplicate_enabled": False,
                "semantic_dedup_enabled": False,
            },
        ),
        (
            "minhash_simhash_near_duplicates",
            {
                "near_duplicate_enabled": True,
                "semantic_dedup_enabled": False,
            },
        ),
        (
            "semantic_dedup",
            {
                "near_duplicate_enabled": False,
                "semantic_dedup_enabled": True,
            },
        ),
        (
            "combined_near_and_semantic",
            {
                "near_duplicate_enabled": True,
                "semantic_dedup_enabled": True,
            },
        ),
    ]
    results: list[dict[str, Any]] = []
    baseline_metrics: dict[str, Any] | None = None
    for variant_name, variant_params in variants:
        story_release = create_story_release(
            conn,
            facet_release_id=facet_release_id,
            params={
                **base_params,
                **variant_params,
                "experiment_variant": variant_name,
            },
            limit=limit,
            domain=domain,
        )
        metrics = dict(story_release.metrics)
        if baseline_metrics is None:
            baseline_metrics = metrics
        results.append(
            {
                "variant": variant_name,
                "story_release_id": story_release.story_release_id,
                "params": {
                    **base_params,
                    **variant_params,
                },
                "metrics": metrics,
                "delta_vs_baseline": _metric_delta(metrics, baseline_metrics),
                "reason_counts": _story_release_reason_counts(
                    conn,
                    story_release.story_release_id,
                ),
                "cross_source_samples": _story_release_cross_source_samples(
                    conn,
                    story_release.story_release_id,
                    limit=sample_limit,
                ),
            }
        )
    return {
        "facet_release_id": facet_release_id,
        "limit": limit,
        "domain": domain or "",
        "variants": results,
    }


def export_story_candidates_for_release(
    conn: sqlite3.Connection,
    *,
    facet_release_id: str,
    params: dict[str, Any] | None = None,
    limit: int = 300,
    domain: str | None = None,
    candidate_limit: int = 0,
) -> dict[str, Any]:
    """Build scored pair candidates for a frozen FacetRelease without saving an attempt."""
    facet_release = get_facet_release(conn, facet_release_id)
    if facet_release is None or facet_release.status not in {"evaluated", "published"}:
        raise ValueError(f"Facet release is not evaluated: {facet_release_id}")
    if not verify_data_release(conn, facet_release.data_release_id):
        raise ValueError(f"Data release checksum failed: {facet_release.data_release_id}")
    params = _story_generation_params(params)
    items = load_frozen_items(conn, facet_release.data_release_id)
    facets = _load_item_facets(conn, facet_release_id)
    if domain:
        items = [
            item
            for item in items
            if domain
            in set(item.domain_ids + _json_list(facets.get(item.item_id, {}).get("domain_ids"), []))
        ]
    if limit > 0:
        items = _select_engine_items(items, facets, limit)
    embeddings = load_release_embeddings(
        conn,
        data_release_id=facet_release.data_release_id,
        model_name=str(params["embedding_model"]),
        model_revision=str(params["embedding_revision"]),
        item_ids=(item.item_id for item in items),
    )
    candidates = generate_story_candidates(
        items,
        facets,
        params=params,
        embeddings=embeddings,
    )
    if candidate_limit > 0:
        candidates = candidates[:candidate_limit]
    item_by_id = {item.item_id: item for item in items}
    serialized = [
        _serialize_story_candidate(candidate, item_by_id)
        for candidate in candidates
        if candidate.item_id_a in item_by_id and candidate.item_id_b in item_by_id
    ]
    return {
        "facet_release_id": facet_release_id,
        "data_release_id": facet_release.data_release_id,
        "limit": limit,
        "domain": domain or "",
        "item_count": len(items),
        "embedding_model": str(params["embedding_model"]),
        "embedding_coverage": round(len(embeddings) / max(len(items), 1), 4),
        "candidate_count": len(serialized),
        "decision_counts": dict(Counter(candidate["decision"] for candidate in serialized)),
        "reason_counts": dict(Counter(candidate["reason"] for candidate in serialized)),
        "candidates": serialized,
    }


def diagnose_engine_release(
    conn: sqlite3.Connection,
    *,
    data_release_id: str | None = None,
    story_release_id: str | None = None,
    trend_release_id: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Explain current Story/Trend Engine quality and likely undermerge causes."""
    if story_release_id:
        story_release = get_story_release(conn, story_release_id)
        if story_release is None:
            raise ValueError(f"Story release not found: {story_release_id}")
        facet_release = get_facet_release(conn, story_release.facet_release_id)
        if facet_release is None:
            raise ValueError(f"Facet release not found: {story_release.facet_release_id}")
        data_release_id = facet_release.data_release_id
    else:
        data_release_id = data_release_id or _latest_data_release_id(conn)
        if not data_release_id:
            raise ValueError("No finalized data release found")
        facet_release = _latest_facet_release(conn, data_release_id)
        story_release = (
            _latest_story_release(conn, facet_release.facet_release_id) if facet_release else None
        )
    data_release = get_data_release(conn, data_release_id)
    if data_release is None:
        raise ValueError(f"Data release not found: {data_release_id}")
    if trend_release_id:
        trend_release = get_trend_release(conn, trend_release_id)
        if trend_release is None:
            raise ValueError(f"Trend release not found: {trend_release_id}")
    elif story_release is not None:
        trend_release = _latest_trend_release(conn, story_release.story_release_id)
    else:
        trend_release = None

    items = load_frozen_items(conn, data_release.release_id)
    source_sections = _diagnose_source_sections(items)
    result: dict[str, Any] = {
        "data_release": asdict(data_release),
        "source_sections": source_sections[:limit],
        "provider_counts": dict(Counter(item.provider for item in items)),
        "source_cluster_counts": dict(Counter(item.source_cluster for item in items)),
        "source_health_issues": _source_health_issues(
            conn,
            data_release.release_id,
            limit=limit,
        ),
        "warnings": [],
    }
    if data_release.input_status != "complete":
        result["warnings"].append(f"input_status_{data_release.input_status}")
    expected_voices = _profile_expects_voice_cluster(data_release.profile) or any(
        issue.get("cluster") == "voices" for issue in result["source_health_issues"]
    )
    if (
        data_release.profile in {"broad", "ai-native"}
        and expected_voices
        and not any(item.source_cluster == "voices" for item in items)
    ):
        result["warnings"].append("dominant_cluster_empty:voices")
    if result["source_health_issues"]:
        result["warnings"].append("source_health_has_empty_or_degraded_sources")
    if facet_release is not None:
        result["facet_release"] = asdict(facet_release)
    if story_release is not None:
        story_metrics = dict(story_release.metrics)
        result["story_release"] = asdict(story_release)
        result["candidate_decision_counts"] = _candidate_decision_counts(
            conn,
            story_release.story_release_id,
        )
        result["candidate_reason_counts"] = _candidate_reason_counts(
            conn,
            story_release.story_release_id,
            limit=limit,
        )
        result["membership_reason_counts"] = _story_release_reason_counts(
            conn,
            story_release.story_release_id,
        )
        result["singleton_provider_counts"] = _singleton_provider_counts(
            conn,
            story_release.story_release_id,
            data_release.release_id,
        )
        result["possible_undermerge_pairs"] = _possible_undermerge_pairs(
            conn,
            story_release.story_release_id,
            data_release.release_id,
            limit=limit,
        )
        result["shared_url_split_groups"] = _shared_url_split_groups(
            items,
            _story_by_item(conn, story_release.story_release_id),
            limit=limit,
        )
        result["cross_source_samples"] = _story_release_cross_source_samples(
            conn,
            story_release.story_release_id,
            limit=limit,
        )
        if float(story_metrics.get("compression_ratio") or 0) >= 0.9:
            result["warnings"].append(
                "compression_ratio_high: clustering is mostly singleton stories"
            )
        if int(story_metrics.get("cross_source_story_count") or 0) < 20:
            result["warnings"].append(
                "cross_source_low: verify canonical URLs, Reddit target URLs "
                "and title/entity matching"
            )
        if not result["possible_undermerge_pairs"] and not result["shared_url_split_groups"]:
            result["warnings"].append(
                "no_obvious_undermerge_pairs: data may have low true cross-source overlap"
            )
    else:
        result["warnings"].append("missing_story_release")
    if trend_release is not None:
        result["trend_release"] = asdict(trend_release)
        trend_metrics = dict(trend_release.metrics)
        if int(trend_metrics.get("confirmed_trend_count") or 0) == 0:
            result["warnings"].append("no_confirmed_trends")
        if trend_release.history_status == "insufficient_history":
            result["warnings"].append("insufficient_history_for_lifecycle")
    else:
        result["warnings"].append("missing_trend_release")
    result["next_commands"] = _diagnose_next_commands(
        facet_release_id=facet_release.facet_release_id if facet_release else "",
        story_release_id=story_release.story_release_id if story_release else "",
        trend_release_id=trend_release.trend_release_id if trend_release else "",
    )
    return result


def _serialize_story_candidate(
    candidate: PairCandidate,
    item_by_id: dict[str, FrozenItem],
) -> dict[str, Any]:
    left = item_by_id[candidate.item_id_a]
    right = item_by_id[candidate.item_id_b]
    return {
        "left_item_id": candidate.item_id_a,
        "right_item_id": candidate.item_id_b,
        "score": candidate.score,
        "decision": candidate.decision,
        "reason": candidate.reason,
        "features": candidate.features,
        "left": _candidate_item_summary(left),
        "right": _candidate_item_summary(right),
    }


def _candidate_item_summary(item: FrozenItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "provider": item.provider,
        "source_cluster": item.source_cluster,
        "source_section": item.source_section,
        "title": item.title,
        "canonical_url": item.canonical_url,
        "target_url": item.target_url,
        "discussion_url": item.discussion_url,
        "published_at": item.published_at,
        "snapshot_date": item.snapshot_date,
        "content_scope": item.content_scope,
        "domain_ids": item.domain_ids,
    }


def _latest_data_release_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT release_id
        FROM data_releases
        WHERE status = 'finalized'
        ORDER BY finalized_at DESC, created_at DESC, release_id DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["release_id"]) if row else ""


def _latest_facet_release(
    conn: sqlite3.Connection,
    data_release_id: str,
) -> FacetRelease | None:
    row = conn.execute(
        """
        SELECT facet_release_id
        FROM facet_releases
        WHERE data_release_id = ? AND status IN ('evaluated', 'published')
        ORDER BY created_at DESC, facet_release_id DESC
        LIMIT 1
        """,
        (data_release_id,),
    ).fetchone()
    return get_facet_release(conn, str(row["facet_release_id"])) if row else None


def _latest_story_release(
    conn: sqlite3.Connection,
    facet_release_id: str,
) -> StoryRelease | None:
    row = conn.execute(
        """
        SELECT story_release_id
        FROM story_releases
        WHERE facet_release_id = ? AND status IN ('evaluated', 'published')
        ORDER BY created_at DESC, story_release_id DESC
        LIMIT 1
        """,
        (facet_release_id,),
    ).fetchone()
    return get_story_release(conn, str(row["story_release_id"])) if row else None


def _latest_trend_release(
    conn: sqlite3.Connection,
    story_release_id: str,
) -> TrendRelease | None:
    row = conn.execute(
        """
        SELECT trend_release_id
        FROM trend_releases
        WHERE story_release_id = ? AND status IN ('evaluated', 'published')
        ORDER BY created_at DESC, trend_release_id DESC
        LIMIT 1
        """,
        (story_release_id,),
    ).fetchone()
    return get_trend_release(conn, str(row["trend_release_id"])) if row else None


def _diagnose_source_sections(items: list[FrozenItem]) -> list[dict[str, Any]]:
    counts = Counter(
        (
            item.provider,
            item.source_cluster,
            item.source_section or item.source_cluster,
        )
        for item in items
    )
    return [
        {
            "provider": provider,
            "source_cluster": source_cluster,
            "source_section": source_section,
            "count": count,
        }
        for (provider, source_cluster, source_section), count in counts.most_common()
    ]


def _candidate_decision_counts(
    conn: sqlite3.Connection,
    story_release_id: str,
) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT decision, COUNT(*) AS count
        FROM story_candidate_pairs
        WHERE story_release_id = ?
        GROUP BY decision
        ORDER BY count DESC, decision
        """,
        (story_release_id,),
    ).fetchall()
    return {str(row["decision"]): int(row["count"]) for row in rows}


def _candidate_reason_counts(
    conn: sqlite3.Connection,
    story_release_id: str,
    *,
    limit: int,
) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT reason, COUNT(*) AS count
        FROM story_candidate_pairs
        WHERE story_release_id = ?
        GROUP BY reason
        ORDER BY count DESC, reason
        LIMIT ?
        """,
        (story_release_id, max(1, limit)),
    ).fetchall()
    return {str(row["reason"]): int(row["count"]) for row in rows}


def _singleton_provider_counts(
    conn: sqlite3.Connection,
    story_release_id: str,
    data_release_id: str,
) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT ri.provider, COUNT(*) AS count
        FROM engine_stories s
        JOIN engine_story_items si
          ON si.story_release_id = s.story_release_id
         AND si.story_id = s.story_id
        JOIN release_items ri
          ON ri.release_id = ?
         AND ri.item_id = si.item_id
        WHERE s.story_release_id = ? AND s.item_count = 1
        GROUP BY ri.provider
        ORDER BY count DESC, ri.provider
        """,
        (data_release_id, story_release_id),
    ).fetchall()
    return {str(row["provider"]): int(row["count"]) for row in rows}


def _source_health_issues(
    conn: sqlite3.Connection,
    data_release_id: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT run_id, source_id, provider, cluster, status, count, message
        FROM release_source_health
        WHERE release_id = ?
          AND status IN ('empty', 'degraded', 'error', 'not_configured')
        ORDER BY
          CASE status
            WHEN 'empty' THEN 0
            WHEN 'degraded' THEN 1
            WHEN 'error' THEN 2
            ELSE 3
          END,
          provider,
          source_id
        LIMIT ?
        """,
        (data_release_id, max(1, limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def _story_by_item(conn: sqlite3.Connection, story_release_id: str) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT item_id, story_id
        FROM engine_story_items
        WHERE story_release_id = ?
        """,
        (story_release_id,),
    ).fetchall()
    return {str(row["item_id"]): str(row["story_id"]) for row in rows}


def _possible_undermerge_pairs(
    conn: sqlite3.Connection,
    story_release_id: str,
    data_release_id: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    item_by_id = {
        str(row["item_id"]): row
        for row in conn.execute(
            "SELECT * FROM release_items WHERE release_id = ?",
            (data_release_id,),
        ).fetchall()
    }
    story_by_item = _story_by_item(conn, story_release_id)
    rows = conn.execute(
        """
        SELECT *
        FROM story_candidate_pairs
        WHERE story_release_id = ?
          AND decision != 'auto_merge'
          AND score >= 0.58
        ORDER BY score DESC, item_id_a, item_id_b
        LIMIT ?
        """,
        (story_release_id, max(1, limit * 4)),
    ).fetchall()
    examples: list[dict[str, Any]] = []
    for row in rows:
        left_id = str(row["item_id_a"])
        right_id = str(row["item_id_b"])
        left = item_by_id.get(left_id)
        right = item_by_id.get(right_id)
        if left is None or right is None:
            continue
        if story_by_item.get(left_id) == story_by_item.get(right_id):
            continue
        features = _json_dict(row["features_json"])
        source_independent = (
            bool(features.get("source_independent")) or left["provider"] != right["provider"]
        )
        if not source_independent:
            continue
        examples.append(
            {
                "left_item_id": left_id,
                "right_item_id": right_id,
                "left_story_id": story_by_item.get(left_id, ""),
                "right_story_id": story_by_item.get(right_id, ""),
                "score": float(row["score"]),
                "decision": str(row["decision"]),
                "reason": str(row["reason"]),
                "features": features,
                "left": {
                    "provider": left["provider"],
                    "source_cluster": left["source_cluster"],
                    "title": left["title"],
                    "canonical_url": left["canonical_url"],
                    "target_url": left["target_url"],
                },
                "right": {
                    "provider": right["provider"],
                    "source_cluster": right["source_cluster"],
                    "title": right["title"],
                    "canonical_url": right["canonical_url"],
                    "target_url": right["target_url"],
                },
            }
        )
        if len(examples) >= limit:
            break
    return examples


def _shared_url_split_groups(
    items: list[FrozenItem],
    story_by_item: dict[str, str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    url_to_items: dict[str, list[FrozenItem]] = defaultdict(list)
    for item in items:
        for url in _item_urls(item):
            if _is_stable_landing_url(url):
                continue
            url_to_items[url].append(item)
    groups: list[dict[str, Any]] = []
    for url, url_items in url_to_items.items():
        story_ids = sorted({story_by_item.get(item.item_id, "") for item in url_items})
        story_ids = [story_id for story_id in story_ids if story_id]
        if len(story_ids) <= 1:
            continue
        groups.append(
            {
                "url": url,
                "story_ids": story_ids,
                "items": [_candidate_item_summary(item) for item in url_items[:6]],
            }
        )
    groups.sort(key=lambda group: (-len(group["story_ids"]), group["url"]))
    return groups[:limit]


def _diagnose_next_commands(
    *,
    facet_release_id: str,
    story_release_id: str,
    trend_release_id: str,
) -> list[str]:
    commands = []
    if facet_release_id:
        commands.append(
            "reddit-compass engine stories candidates "
            f"--facet-release {facet_release_id} --limit 300 --candidate-limit 50"
        )
        commands.append(
            "reddit-compass engine experiments compare "
            f"--facet-release {facet_release_id} --limit 300 --sample-limit 5"
        )
    if story_release_id:
        commands.append(f"reddit-compass engine stories eval --story-release {story_release_id}")
        commands.append(
            f"reddit-compass engine golden export --story-release {story_release_id} "
            "--output data/golden/story-golden.json"
        )
    if trend_release_id:
        commands.append(f"reddit-compass engine trends eval --trend-release {trend_release_id}")
    return commands


def generate_story_candidates(
    items: list[FrozenItem],
    facets: dict[str, dict[str, Any]],
    *,
    params: dict[str, Any] | None = None,
    embeddings: dict[str, list[float]] | None = None,
) -> list[PairCandidate]:
    """Generate URL/sparse/dense top-K candidates without materializing all pairs."""
    params = params or {}
    max_df = max(8, int(len(items) * float(params.get("max_token_df_ratio", 0.2))))
    sparse_bucket_limit = max(2, int(params.get("max_sparse_bucket_size", 32)))
    max_candidate_pairs = max(1, int(params.get("max_candidate_pairs", 100_000)))
    item_by_id = {item.item_id: item for item in items}
    url_index: dict[str, list[str]] = defaultdict(list)
    token_index: dict[str, list[str]] = defaultdict(list)
    entity_index: dict[str, list[str]] = defaultdict(list)
    near_duplicate_index: dict[str, list[str]] = defaultdict(list)
    near_duplicate_enabled = bool(params.get("near_duplicate_enabled", True))
    for item in items:
        for url in _item_urls(item):
            url_index[url].append(item.item_id)
        normalized = normalize_title(item.title, item.provider)
        if is_generic_title(normalized) or is_low_signal_title(item.title):
            continue
        for token in extract_tokens(normalized):
            token_index[token].append(item.item_id)
        if near_duplicate_enabled:
            for bucket_key in _near_duplicate_bucket_keys(normalized):
                near_duplicate_index[bucket_key].append(item.item_id)
        entities = _facet_entities(facets.get(item.item_id, {})) or _meaningful_entities(item.title)
        for entity in entities:
            entity_index[entity].append(item.item_id)

    pair_reasons: dict[tuple[str, str], set[str]] = defaultdict(set)
    near_duplicate_max_bucket_size = int(params.get("near_duplicate_max_bucket_size", 40))
    # Process discriminative, small buckets first.  A shared common actor or
    # generic term is not enough evidence of one event; dense/near-duplicate
    # retrieval handles semantic neighbours without an O(n²) expansion.
    indexes: tuple[tuple[str, dict[str, list[str]], int], ...] = (
        ("url", url_index, max_candidate_pairs),
        ("near_duplicate", near_duplicate_index, near_duplicate_max_bucket_size)
        if near_duplicate_enabled
        else ("near_duplicate", {}, 0),
        ("token", token_index, min(max_df, sparse_bucket_limit)),
        ("entity", entity_index, min(max_df, sparse_bucket_limit)),
    )
    for reason, index, bucket_limit in indexes:
        if bucket_limit < 2:
            continue
        for _key, ids in sorted(index.items(), key=lambda pair: (len(pair[1]), pair[0])):
            if not 1 < len(ids) <= bucket_limit:
                continue
            if _add_index_pairs(
                pair_reasons,
                ids,
                reason,
                max_candidate_pairs=max_candidate_pairs,
            ):
                break
        if len(pair_reasons) >= max_candidate_pairs:
            break
    dense_scores = top_k_cosine_pairs(
        embeddings or {},
        top_k=int(params.get("dense_top_k", 12)),
        min_similarity=float(params.get("dense_candidate_threshold", 0.68)),
        chunk_size=int(params.get("dense_chunk_size", 256)),
    )
    for pair in sorted(dense_scores, key=lambda value: (-dense_scores[value], value)):
        if pair not in pair_reasons and len(pair_reasons) >= max_candidate_pairs:
            break
        pair_reasons[pair].add("dense")

    result: list[PairCandidate] = []
    for item_id_a, item_id_b in sorted(pair_reasons):
        left = item_by_id[item_id_a]
        right = item_by_id[item_id_b]
        candidate = _score_story_pair(
            left,
            right,
            facets.get(item_id_a, {}),
            facets.get(item_id_b, {}),
            generated_by=pair_reasons[(item_id_a, item_id_b)],
            params=params,
            dense_similarity=dense_scores.get((item_id_a, item_id_b)),
        )
        if candidate is not None:
            result.append(candidate)
    return sorted(result, key=lambda candidate: (-candidate.score, candidate.item_id_a))


def prepare_story_review_jobs(
    conn: sqlite3.Connection,
    story_release_id: str,
    *,
    limit: int = 100,
    model: str = "qwen3.6-flash",
    prompt_version: str = STORY_REVIEW_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    """Prepare bounded Qwen jobs for ambiguous pairs in an existing attempt."""
    story_release = get_story_release(conn, story_release_id)
    if story_release is None:
        raise ValueError(f"Unknown story release: {story_release_id}")
    facet_release = get_facet_release(conn, story_release.facet_release_id)
    if facet_release is None:
        raise ValueError(f"Unknown facet release: {story_release.facet_release_id}")
    items = {item.item_id: item for item in load_frozen_items(conn, facet_release.data_release_id)}
    facets = _load_item_facets(conn, facet_release.facet_release_id)
    rows = conn.execute(
        """
        SELECT item_id_a, item_id_b
        FROM story_candidate_pairs
        WHERE story_release_id = ? AND decision = 'review'
        ORDER BY score DESC, item_id_a, item_id_b
        LIMIT ?
        """,
        (story_release_id, max(0, limit)),
    ).fetchall()
    jobs: list[dict[str, Any]] = []
    for row in rows:
        item_ids = [str(row["item_id_a"]), str(row["item_id_b"])]
        if any(item_id not in items for item_id in item_ids):
            continue
        payload = [
            _story_review_item(items[item_id], facets.get(item_id, {})) for item_id in item_ids
        ]
        input_hash = _hash_json(payload)
        cached = conn.execute(
            """
            SELECT valid
            FROM llm_reviews
            WHERE model = ? AND prompt_version = ? AND input_hash = ?
            """,
            (model, prompt_version, input_hash),
        ).fetchone()
        if cached is not None and bool(cached["valid"]):
            continue
        jobs.append(
            {
                # ``engine_labels`` and the learned merge model address a pair
                # by this canonical key.  Using a different hash here used to
                # make Qwen labels invisible to training.
                "target_id": "|".join(_pair_key(*item_ids)),
                "item_ids": item_ids,
                "model": model,
                "prompt_version": prompt_version,
                "input_hash": input_hash,
                "prompt": build_story_review_prompt(payload),
            }
        )
    return jobs


def store_story_review_response(
    conn: sqlite3.Connection,
    *,
    target_id: str,
    input_hash: str,
    raw_response: str,
    allowed_item_ids: set[str],
    model: str = "qwen3.6-flash",
    prompt_version: str = STORY_REVIEW_PROMPT_VERSION,
) -> dict[str, Any]:
    review, errors = validate_story_review(
        raw_response,
        allowed_item_ids=allowed_item_ids,
    )
    decision = review.decision if review else "invalid"
    review_id = _stable_id("llm_review", model, prompt_version, input_hash)
    conn.execute(
        """
        INSERT OR REPLACE INTO llm_reviews (
            review_id, target_kind, target_id, model, prompt_version,
            input_hash, decision, response_json, valid, created_at
        ) VALUES (?, 'story_pair', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            target_id,
            model,
            prompt_version,
            input_hash,
            decision,
            raw_response,
            int(review is not None),
            now_iso(),
        ),
    )
    conn.commit()
    return {
        "review_id": review_id,
        "decision": decision,
        "valid": review is not None,
        "errors": errors,
    }


def apply_cached_story_reviews(
    conn: sqlite3.Connection,
    *,
    candidates: list[PairCandidate],
    items: list[FrozenItem],
    facets: dict[str, dict[str, Any]],
    model: str,
    prompt_version: str,
    min_merge_confidence: float,
) -> list[PairCandidate]:
    item_by_id = {item.item_id: item for item in items}
    resolved: list[PairCandidate] = []
    for candidate in candidates:
        if candidate.decision != "review":
            resolved.append(candidate)
            continue
        item_ids = [candidate.item_id_a, candidate.item_id_b]
        if any(item_id not in item_by_id for item_id in item_ids):
            resolved.append(candidate)
            continue
        payload = [
            _story_review_item(item_by_id[item_id], facets.get(item_id, {})) for item_id in item_ids
        ]
        input_hash = _hash_json(payload)
        row = conn.execute(
            """
            SELECT response_json
            FROM llm_reviews
            WHERE model = ? AND prompt_version = ? AND input_hash = ? AND valid = 1
            """,
            (model, prompt_version, input_hash),
        ).fetchone()
        if row is None:
            resolved.append(candidate)
            continue
        review, _ = validate_story_review(
            str(row["response_json"]),
            allowed_item_ids=set(item_ids),
        )
        if review is None:
            resolved.append(candidate)
            continue
        decision = candidate.decision
        reason = candidate.reason
        if review.decision == "same_story" and review.confidence >= min_merge_confidence:
            decision = "auto_merge"
            reason = "validated by cached Qwen story review"
        elif review.decision == "different_story":
            decision = "reject"
            reason = "rejected by cached Qwen story review"
        resolved.append(
            PairCandidate(
                item_id_a=candidate.item_id_a,
                item_id_b=candidate.item_id_b,
                score=candidate.score,
                decision=decision,
                reason=reason,
                features={
                    **candidate.features,
                    "llm_review": {
                        "decision": review.decision,
                        "confidence": review.confidence,
                        "conflicts": review.conflicts,
                        "reason": review.reason,
                    },
                },
            )
        )
    return resolved


def _story_review_item(
    item: FrozenItem,
    facets: dict[str, Any],
) -> dict[str, object]:
    return {
        "item_id": item.item_id,
        "provider": item.provider,
        "source_cluster": item.source_cluster,
        "title": item.title,
        "excerpt": item.excerpt[:1200],
        "content_scope": item.content_scope,
        "published_at": item.published_at,
        "canonical_url": item.canonical_url,
        "target_url": item.target_url,
        "event_frame": _json_dict(facets.get("event_frame_json")),
        "entities": sorted(_facet_entities(facets)),
    }


def _score_story_pair(
    left: FrozenItem,
    right: FrozenItem,
    left_facets: dict[str, Any],
    right_facets: dict[str, Any],
    *,
    generated_by: set[str],
    params: dict[str, Any],
    dense_similarity: float | None = None,
) -> PairCandidate | None:
    left_norm = normalize_title(left.title, left.provider)
    right_norm = normalize_title(right.title, right.provider)
    shared_urls = _item_urls(left) & _item_urls(right)
    event_specific_urls = {url for url in shared_urls if not _is_stable_landing_url(url)}
    if event_specific_urls:
        item_id_a, item_id_b = _pair_key(left.item_id, right.item_id)
        features = {
            "url_match": True,
            "shared_urls": sorted(event_specific_urls),
            "source_independent": left.provider != right.provider,
            "generated_by": sorted(generated_by),
        }
        return PairCandidate(
            item_id_a=item_id_a,
            item_id_b=item_id_b,
            score=1.0,
            decision="auto_merge",
            reason="shared canonical/target URL",
            features=features,
        )
    huggingface_model_urls = sorted(url for url in shared_urls if _is_huggingface_model_url(url))
    if huggingface_model_urls:
        left_tokens_preview = extract_tokens(left_norm)
        right_tokens_preview = extract_tokens(right_norm)
        shared_tokens_preview = left_tokens_preview & right_tokens_preview
        token_jaccard_preview = len(shared_tokens_preview) / max(
            len(left_tokens_preview | right_tokens_preview),
            1,
        )
        date_distance_preview = _date_distance_days(left, right)
        if (
            len(shared_tokens_preview) >= 1
            and token_jaccard_preview >= 0.25
            and date_distance_preview <= 7
        ):
            item_id_a, item_id_b = _pair_key(left.item_id, right.item_id)
            return PairCandidate(
                item_id_a=item_id_a,
                item_id_b=item_id_b,
                score=0.86,
                decision="auto_merge",
                reason="shared HuggingFace model release URL",
                features={
                    "url_match": True,
                    "shared_urls": huggingface_model_urls,
                    "token_jaccard": round(token_jaccard_preview, 4),
                    "shared_tokens": sorted(shared_tokens_preview),
                    "date_distance_days": date_distance_preview,
                    "source_independent": left.provider != right.provider,
                    "generated_by": sorted(generated_by),
                    "stable_landing_url_match": True,
                },
            )
    if (
        is_generic_title(left_norm)
        or is_generic_title(right_norm)
        or is_low_signal_title(left.title)
        or is_low_signal_title(right.title)
    ):
        return None
    left_tokens = extract_tokens(left_norm)
    right_tokens = extract_tokens(right_norm)
    if not left_tokens or not right_tokens:
        return None
    shared_tokens = left_tokens & right_tokens
    token_jaccard = len(shared_tokens) / max(len(left_tokens | right_tokens), 1)
    title_score = fuzz.token_set_ratio(left_norm, right_norm) / 100.0
    left_entities = _facet_entities(left_facets) or _meaningful_entities(left.title)
    right_entities = _facet_entities(right_facets) or _meaningful_entities(right.title)
    shared_entities = left_entities & right_entities
    # Hard guard: generic anchors do not count as entity anchors
    from .verified_stories import GENERIC_ANCHORS

    shared_entities = {e for e in shared_entities if e.lower() not in GENERIC_ANCHORS}
    entity_score = len(shared_entities) / max(min(len(left_entities), len(right_entities)), 1)
    left_numbers = set(_event_numbers(left_facets))
    right_numbers = set(_event_numbers(right_facets))
    number_conflict = bool(left_numbers and right_numbers and not left_numbers & right_numbers)
    left_frame = _json_dict(left_facets.get("event_frame_json"))
    right_frame = _json_dict(right_facets.get("event_frame_json"))
    left_locations = set(_json_list(left_frame.get("geography")))
    right_locations = set(_json_list(right_frame.get("geography")))
    location_conflict = bool(
        left_locations and right_locations and not left_locations & right_locations
    )
    left_people = set(_json_list(left_frame.get("people")))
    right_people = set(_json_list(right_frame.get("people")))
    person_conflict = bool(
        left_people and right_people and not left_people & right_people and title_score < 0.88
    )
    left_action = str(left_frame.get("action") or "")
    right_action = str(right_frame.get("action") or "")
    action_match = bool(left_action and right_action and left_action == right_action)
    date_distance = _date_distance_days(left, right)
    near_duplicate_features = (
        _near_duplicate_similarity_features(left_norm, right_norm)
        if "near_duplicate" in generated_by
        else {}
    )
    time_score = 1.0 if date_distance <= 2 else 0.7 if date_distance <= 7 else 0.3
    source_independent = left.provider != right.provider
    shared_themes = set(_json_list(left_facets.get("candidate_themes"))) & set(
        _json_list(right_facets.get("candidate_themes"))
    )
    if dense_similarity is None:
        score = (
            0.46 * title_score
            + 0.18 * token_jaccard
            + 0.18 * entity_score
            + 0.08 * time_score
            + (0.06 if source_independent else 0.0)
            + (0.04 if shared_themes else 0.0)
            + (0.04 if action_match else 0.0)
            - (0.16 if number_conflict else 0.0)
            - (0.18 if location_conflict else 0.0)
            - (0.18 if person_conflict else 0.0)
        )
    else:
        score = (
            0.34 * title_score
            + 0.12 * token_jaccard
            + 0.14 * entity_score
            + 0.22 * dense_similarity
            + 0.08 * time_score
            + (0.06 if source_independent else 0.0)
            + (0.04 if shared_themes else 0.0)
            + (0.04 if action_match else 0.0)
            - (0.16 if number_conflict else 0.0)
            - (0.18 if location_conflict else 0.0)
            - (0.18 if person_conflict else 0.0)
        )
    score = round(max(0.0, min(score, 0.99)), 4)
    auto_threshold = float(params.get("auto_merge_threshold", 0.82))
    review_threshold = float(params.get("review_threshold", 0.62))
    dense_auto_threshold = float(params.get("dense_auto_threshold", 0.88))
    dense_review_threshold = float(params.get("dense_review_threshold", 0.72))
    hard_conflict = (
        (number_conflict and len(shared_entities) < 2 and title_score < 0.9)
        or location_conflict
        or person_conflict
    )
    huggingface_model_url_match = bool(
        any(_is_huggingface_model_url(url) for url in shared_urls)
        and len(shared_tokens) >= 1
        and token_jaccard >= 0.25
        and date_distance <= 7
    )
    near_duplicate_title_match = bool(
        near_duplicate_features
        and date_distance <= 7
        and near_duplicate_features["near_duplicate_simhash_distance"]
        <= int(params.get("near_duplicate_simhash_distance", 18))
        and (
            near_duplicate_features["near_duplicate_shingle_jaccard"]
            >= float(params.get("near_duplicate_shingle_jaccard", 0.34))
            or token_jaccard >= 0.45
            or title_score >= 0.86
        )
    )
    _safe_e5 = bool(params.get("safe_e5_mode", False))
    _semantic_threshold = 0.94 if _safe_e5 else float(params.get("semantic_dedup_threshold", 0.92))
    _semantic_max_days = 3 if _safe_e5 else int(params.get("semantic_dedup_max_days", 7))
    _semantic_provenance = (
        (title_score >= 0.75 or token_jaccard >= 0.45 or bool(shared_urls))
        if _safe_e5
        else (
            shared_entities
            or token_jaccard >= 0.34
            or title_score >= 0.78
            or bool(left_numbers & right_numbers)
        )
    )
    semantic_review_match = bool(
        params.get("semantic_dedup_enabled", False)
        and dense_similarity is not None
        and dense_similarity >= _semantic_threshold
        and date_distance <= _semantic_max_days
        and _semantic_provenance
        and (not _safe_e5 or bool(shared_entities))
    )
    # Safe E5: semantic pairs that don't meet strict auto-merge go to review
    _semantic_review_fallback = bool(
        _safe_e5
        and params.get("semantic_dedup_enabled", False)
        and dense_similarity is not None
        and dense_similarity >= 0.88
        and not semantic_review_match
    )
    dense_event_match = bool(
        dense_similarity is not None
        and dense_similarity >= dense_auto_threshold
        and (title_score >= 0.68 or token_jaccard >= 0.32 or bool(left_numbers & right_numbers))
        and (shared_entities or title_score >= 0.82)
    )
    dense_review_match = bool(
        dense_similarity is not None
        and dense_similarity >= dense_review_threshold
        and (
            title_score >= 0.5
            or token_jaccard >= 0.2
            or bool(shared_entities)
            or bool(left_numbers & right_numbers)
        )
    )
    exact_title_event_match = bool(
        title_score >= 0.98
        and token_jaccard >= 0.72
        and date_distance <= 3
        and (
            source_independent
            or left.source_section != right.source_section
            or left.canonical_url != right.canonical_url
        )
    )
    event_action_tokens = {
        "acquire",
        "acquires",
        "ban",
        "bans",
        "block",
        "blocks",
        "buy",
        "buys",
        "charge",
        "charges",
        "crash",
        "cuts",
        "delay",
        "delays",
        "drop",
        "drops",
        "fall",
        "falls",
        "hire",
        "hiring",
        "invest",
        "invests",
        "launch",
        "launches",
        "layoff",
        "lawsuit",
        "leak",
        "leaks",
        "pause",
        "pauses",
        "raise",
        "raises",
        "release",
        "releases",
        "resign",
        "resigns",
        "sell",
        "sells",
        "strike",
        "strikes",
        "sue",
        "sues",
        "tumble",
        "tumbles",
    }
    shared_action_tokens = shared_tokens & event_action_tokens
    strong_cross_source_overlap = bool(
        token_jaccard >= float(params.get("cross_source_event_token_jaccard", 0.42))
        and title_score >= float(params.get("cross_source_event_title_score", 0.83))
        and (len(shared_entities) >= 2 or title_score >= 0.88)
        and (shared_action_tokens or (title_score >= 0.86 and len(shared_tokens) >= 5))
    )
    short_title_action_overlap = bool(
        title_score >= 0.86
        and token_jaccard >= 0.18
        and shared_action_tokens
        and (shared_entities or len(shared_tokens) >= 3)
    )
    cross_source_event_title_match = bool(
        params.get("cross_source_event_title_enabled", True)
        and source_independent
        and date_distance <= int(params.get("cross_source_event_title_max_days", 3))
        and (strong_cross_source_overlap or short_title_action_overlap)
        and ("token" in generated_by or "entity" in generated_by or "dense" in generated_by)
    )
    # --- Hard guards: block semantic-only auto-merge for risky patterns ---
    _show_hn_left = "show hn" in left_norm.lower()
    _show_hn_right = "show hn" in right_norm.lower()
    _both_show_hn = _show_hn_left and _show_hn_right
    _same_provider_hn = (
        left.provider == right.provider
        and left.provider in ("hackernews", "hn")
        and not shared_urls
        and not near_duplicate_title_match
    )
    if _both_show_hn or _same_provider_hn:
        # Show HN / same-provider HN: semantic-only merge blocked
        dense_event_match = False
    if hard_conflict:
        decision = "reject"
        reason = "number/date event conflict"
    elif huggingface_model_url_match:
        decision = "auto_merge"
        reason = "shared HuggingFace model release URL"
    elif near_duplicate_title_match:
        decision = "auto_merge"
        reason = "near-duplicate title fingerprint"
        score = max(score, 0.86)
    elif cross_source_event_title_match:
        decision = "auto_merge"
        reason = "cross-source event title/entity match"
        score = max(score, 0.84)
    elif exact_title_event_match:
        decision = "auto_merge"
        reason = "exact event title match without hard conflict"
    elif score >= auto_threshold and shared_entities and bool(left_numbers & right_numbers):
        decision = "auto_merge"
        reason = "high event similarity with shared entity/number provenance"
    elif (
        score >= review_threshold
        or dense_review_match
        or dense_event_match
        or semantic_review_match
        or _semantic_review_fallback
    ):
        decision = "review"
        reason = "ambiguous event similarity; LLM/manual review required"
    else:
        return None
    item_id_a, item_id_b = _pair_key(left.item_id, right.item_id)
    candidate_features: dict[str, Any] = {
        "title_score": round(title_score, 4),
        "token_jaccard": round(token_jaccard, 4),
        "entity_score": round(entity_score, 4),
        "dense_similarity": round(dense_similarity, 4) if dense_similarity is not None else None,
        "shared_entities": sorted(shared_entities),
        "shared_tokens": sorted(shared_tokens)[:20],
        "date_distance_days": date_distance,
        "number_conflict": number_conflict,
        "location_conflict": location_conflict,
        "person_conflict": person_conflict,
        "action_match": action_match,
        "cross_source_event_title_match": cross_source_event_title_match,
        "semantic_review_match": semantic_review_match,
        "dense_event_match": dense_event_match,
        "shared_action_tokens": sorted(shared_action_tokens),
        "source_independent": source_independent,
        "generated_by": sorted(generated_by),
        "stable_landing_url_match": bool(shared_urls),
    }
    candidate_features.update(near_duplicate_features)
    # Фаза 3: обученная модель решает исход серой зоны. Жёсткие правила (auto_merge по
    # provenance-якорям и reject по hard conflicts) остаются детерминированными — модель
    # применяется только к парам, которые лестница отправила в review.
    merge_model_params = params.get("merge_model")
    if decision == "review" and isinstance(merge_model_params, dict):
        model = MergeModel.from_params(merge_model_params)
        model_score = model.score(candidate_features)
        if model.predict(candidate_features):
            decision = "auto_merge"
            reason = "learned merge model"
            score = max(score, round(model_score, 4))
            candidate_features["merge_model_score"] = round(model_score, 4)
            candidate_features["merge_model_hash"] = model.model_hash
        else:
            candidate_features["merge_model_score"] = round(model_score, 4)
            candidate_features["merge_model_hash"] = model.model_hash
            return None
    return PairCandidate(
        item_id_a=item_id_a,
        item_id_b=item_id_b,
        score=score,
        decision=decision,
        reason=reason,
        features=candidate_features,
    )


def _constrained_story_groups(
    items: list[FrozenItem],
    candidates: list[PairCandidate],
    *,
    params: dict[str, Any],
) -> list[list[tuple[FrozenItem, float, str]]]:
    """Merge only auto edges while validating every new member against a medoid."""
    item_by_id = {item.item_id: item for item in items}
    groups: dict[str, list[str]] = {item.item_id: [item.item_id] for item in items}
    owner = {item.item_id: item.item_id for item in items}
    pair_by_ids = {
        (candidate.item_id_a, candidate.item_id_b): candidate for candidate in candidates
    }

    for candidate in candidates:
        if candidate.decision != "auto_merge":
            continue
        left_owner = owner[candidate.item_id_a]
        right_owner = owner[candidate.item_id_b]
        if left_owner == right_owner:
            continue
        left_group = groups[left_owner]
        right_group = groups[right_owner]
        merged_ids = sorted(set(left_group + right_group))
        if _single_provider_large_group_without_event_url(
            merged_ids,
            item_by_id,
            pair_by_ids,
            max_size=int(params.get("single_provider_without_event_url_max_items", 3)),
        ):
            continue
        medoid_id = _choose_medoid(merged_ids, pair_by_ids)
        if not _valid_group_against_medoid(merged_ids, medoid_id, pair_by_ids):
            continue
        groups[left_owner] = merged_ids
        del groups[right_owner]
        for item_id in merged_ids:
            owner[item_id] = left_owner

    result: list[list[tuple[FrozenItem, float, str]]] = []
    for item_ids in groups.values():
        medoid = _choose_medoid(item_ids, pair_by_ids)
        group: list[tuple[FrozenItem, float, str]] = []
        for item_id in item_ids:
            if item_id == medoid:
                group.append((item_by_id[item_id], 1.0, "story medoid"))
                continue
            membership_candidate = pair_by_ids.get(_pair_key(item_id, medoid))
            group.append(
                (
                    item_by_id[item_id],
                    membership_candidate.score if membership_candidate else 0.0,
                    membership_candidate.reason
                    if membership_candidate
                    else "constrained membership",
                )
            )
        result.append(group)
    return sorted(result, key=lambda group: (-len(group), group[0][0].item_id))


def _choose_medoid(
    item_ids: list[str],
    pair_by_ids: dict[tuple[str, str], PairCandidate],
) -> str:
    return max(
        sorted(item_ids),
        key=lambda candidate_id: _candidate_centrality(candidate_id, item_ids, pair_by_ids),
    )


def _valid_group_against_medoid(
    item_ids: list[str],
    medoid_id: str,
    pair_by_ids: dict[tuple[str, str], PairCandidate],
) -> bool:
    for item_id in item_ids:
        if item_id == medoid_id:
            continue
        pair = pair_by_ids.get(_pair_key(item_id, medoid_id))
        if pair is None or pair.decision != "auto_merge" or pair.score < 0.72:
            return False
        if any(
            bool(pair.features.get(conflict))
            for conflict in ("number_conflict", "location_conflict", "person_conflict")
        ):
            return False
    return True


def _single_provider_large_group_without_event_url(
    item_ids: list[str],
    item_by_id: dict[str, FrozenItem],
    pair_by_ids: dict[tuple[str, str], PairCandidate],
    *,
    max_size: int,
) -> bool:
    if len(item_ids) <= max_size:
        return False
    providers = {item_by_id[item_id].provider for item_id in item_ids if item_id in item_by_id}
    if len(providers) != 1:
        return False
    for index, left_id in enumerate(item_ids):
        for right_id in item_ids[index + 1 :]:
            pair = pair_by_ids.get(_pair_key(left_id, right_id))
            if pair and pair.reason == "shared canonical/target URL":
                return False
    return True


def _candidate_centrality(
    candidate_id: str,
    item_ids: list[str],
    pair_by_ids: dict[tuple[str, str], PairCandidate],
) -> float:
    total = 0.0
    for other_id in item_ids:
        if other_id == candidate_id:
            continue
        pair = pair_by_ids.get(_pair_key(candidate_id, other_id))
        if pair is not None:
            total += pair.score
    return total


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left <= right else (right, left)


def _build_engine_stories(
    groups: list[list[tuple[FrozenItem, float, str]]],
    facets: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], list[tuple[str, float, str]]]]:
    result = []
    for group in groups:
        items = [entry[0] for entry in group]
        medoid = next((entry[0] for entry in group if entry[1] == 1.0), items[0])
        story_id = _stable_id("story", medoid.item_id)
        domains = normalize_domain_ids(
            [
                domain
                for item in items
                for domain in (
                    _json_list(facets.get(item.item_id, {}).get("domain_ids")) or item.domain_ids
                )
            ]
        )
        themes = sorted(
            {
                theme
                for item in items
                for theme in _json_list(facets.get(item.item_id, {}).get("theme_ids"))
            }
        )
        dates = sorted({date for item in items if (date := _item_date(item))})
        providers = {item.provider for item in items}
        confidence = (
            "high"
            if len(providers) >= 2 and len(items) >= 2
            else "medium"
            if len(items) >= 2
            else "low"
        )
        summary = next(
            (
                str(facets[item.item_id].get("summary_ru") or "")
                for item in items
                if facets.get(item.item_id, {}).get("summary_ru")
            ),
            "",
        )
        story = {
            "story_id": story_id,
            "canonical_key": medoid.canonical_url or normalize_title(medoid.title, medoid.provider),
            "title": medoid.title,
            "summary_ru": summary,
            "domain_ids": domains,
            "theme_ids": themes,
            "project_scores": compute_project_scores(domains, medoid.title, medoid.excerpt),
            "first_seen": dates[0] if dates else "",
            "last_seen": dates[-1] if dates else "",
            "confidence": confidence,
            "source_count": len(providers),
            "item_count": len(items),
        }
        result.append(
            (
                story,
                [(item.item_id, score, reason) for item, score, reason in group],
            )
        )
    return result


def _reconcile_story_identity(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    stories: list[tuple[dict[str, Any], list[tuple[str, float, str]]]],
) -> tuple[
    list[tuple[dict[str, Any], list[tuple[str, float, str]]]],
    list[tuple[str, str, str]],
]:
    data_release = get_data_release(conn, data_release_id)
    if data_release is None:
        return stories, []
    previous_row = conn.execute(
        """
        SELECT sr.story_release_id
        FROM story_releases AS sr
        JOIN facet_releases AS fr
          ON fr.facet_release_id = sr.facet_release_id
        JOIN data_releases AS dr
          ON dr.release_id = fr.data_release_id
        WHERE dr.profile = ? AND sr.status IN ('evaluated', 'published')
        ORDER BY sr.created_at DESC
        LIMIT 1
        """,
        (data_release.profile,),
    ).fetchone()
    if previous_row is None:
        return stories, []
    previous_release_id = str(previous_row["story_release_id"])
    previous_items = _load_story_item_ids(conn, previous_release_id)
    current_items = [{item_id for item_id, _, _ in memberships} for _, memberships in stories]
    candidates: list[tuple[int, float, str, int]] = []
    for old_story_id, old_items in previous_items.items():
        old_set = set(old_items)
        for index, new_items in enumerate(current_items):
            overlap = len(old_set & new_items)
            if not overlap:
                continue
            jaccard = overlap / max(len(old_set | new_items), 1)
            if jaccard >= 0.4 or overlap >= 2:
                candidates.append((overlap, jaccard, old_story_id, index))
    assigned_old: set[str] = set()
    assigned_new: set[int] = set()
    inherited: dict[int, str] = {}
    for _, _, old_story_id, index in sorted(
        candidates,
        key=lambda value: (-value[0], -value[1], value[2], value[3]),
    ):
        if old_story_id in assigned_old or index in assigned_new:
            continue
        assigned_old.add(old_story_id)
        assigned_new.add(index)
        inherited[index] = old_story_id

    reconciled = []
    for index, (story, memberships) in enumerate(stories):
        inherited_id = inherited.get(index)
        reconciled.append(
            (
                {**story, "story_id": inherited_id or story["story_id"]},
                memberships,
            )
        )
    reconciled = _deduplicate_story_ids(reconciled)
    redirects: list[tuple[str, str, str]] = []
    for old_story_id, old_items in previous_items.items():
        if old_story_id in assigned_old:
            continue
        overlaps = [
            (len(set(old_items) & new_items), index)
            for index, new_items in enumerate(current_items)
            if set(old_items) & new_items
        ]
        if not overlaps:
            continue
        _, best_index = max(overlaps, key=lambda value: (value[0], -value[1]))
        new_story_id = str(reconciled[best_index][0]["story_id"])
        if new_story_id != old_story_id:
            redirects.append((old_story_id, new_story_id, "story merge/split continuity"))
    return reconciled, redirects


def _deduplicate_story_ids(
    stories: list[tuple[dict[str, Any], list[tuple[str, float, str]]]],
) -> list[tuple[dict[str, Any], list[tuple[str, float, str]]]]:
    seen: set[str] = set()
    result: list[tuple[dict[str, Any], list[tuple[str, float, str]]]] = []
    for story, memberships in stories:
        story_id = str(story["story_id"])
        if story_id in seen:
            member_key = ",".join(sorted(item_id for item_id, _, _ in memberships))
            story_id = _stable_id(story_id, "split", member_key)
            suffix = 1
            while story_id in seen:
                suffix += 1
                story_id = _stable_id(story_id, "split", member_key, str(suffix))
            story = {**story, "story_id": story_id}
        seen.add(story_id)
        result.append((story, memberships))
    return result


def _story_release_metrics(
    stories: list[tuple[dict[str, Any], list[tuple[str, float, str]]]],
    candidates: list[PairCandidate],
    item_count: int,
) -> dict[str, Any]:
    multi = [story for story, _ in stories if int(story["item_count"]) >= 2]
    cross_source = [story for story in multi if int(story["source_count"]) >= 2]
    return {
        "item_count": item_count,
        "story_count": len(stories),
        "singleton_count": len(stories) - len(multi),
        "multi_item_story_count": len(multi),
        "cross_source_story_count": len(cross_source),
        "candidate_pair_count": len(candidates),
        "review_pair_count": sum(1 for candidate in candidates if candidate.decision == "review"),
        "compression_ratio": round(len(stories) / max(item_count, 1), 4),
    }


def _discover_trends_embedding_v2(
    conn: sqlite3.Connection,
    stories: list[dict[str, Any]],
    story_items: dict[str, list[str]],
    frozen_items: dict[str, FrozenItem],
    *,
    data_release_id: str,
    story_release: StoryRelease,
    params: dict[str, Any],
) -> list[tuple[dict[str, Any], list[tuple[str, float, str]]]]:
    """Адаптер слоя Trends v2 (Фаза 5) к контракту create_trend_release."""

    from .trend_discovery import discover_trends

    provider_by_item = {item_id: item.provider for item_id, item in frozen_items.items()}
    model_name = str(story_release.metrics.get("embedding_model", DEFAULT_EMBEDDING_MODEL))
    model_revision = str(story_release.metrics.get("embedding_revision", "default"))
    vectors_by_item = load_release_embeddings(
        conn,
        data_release_id=data_release_id,
        model_name=model_name,
        model_revision=model_revision,
        item_ids=iter(frozen_items.keys()),
    )
    raw_trends = discover_trends(
        stories,
        story_items,
        provider_by_item,
        vectors_by_item=vectors_by_item or None,
        min_stories=int(params.get("min_stories", 3)),
        min_dates=int(params.get("min_dates", 2)),
        cluster_threshold=float(params.get("trend_cluster_threshold", 0.55)),
    )
    adapted: list[tuple[dict[str, Any], list[tuple[str, float, str]]]] = []
    for trend in raw_trends:
        memberships = trend.pop("memberships", [])
        adapted.append((trend, memberships))
    return adapted


def create_trend_release(
    conn: sqlite3.Connection,
    *,
    story_release_id: str,
    window: str = "30d",
    method: str = DEFAULT_TREND_METHOD,
    params: dict[str, Any] | None = None,
    verified_only: bool = False,
    signal_release_id: str | None = None,
) -> TrendRelease:
    """Discover recurring patterns over distinct stories, never raw duplicates."""
    story_release = get_story_release(conn, story_release_id)
    if story_release is None or story_release.status not in {"evaluated", "published"}:
        raise ValueError(f"Story release is not evaluated: {story_release_id}")
    facet_release = get_facet_release(conn, story_release.facet_release_id)
    if facet_release is None:
        raise ValueError(f"Facet release not found: {story_release.facet_release_id}")
    if not verify_data_release(conn, facet_release.data_release_id):
        raise ValueError(f"Data release checksum failed: {facet_release.data_release_id}")
    params = {
        "min_stories": 3,
        "min_dates": 2,
        "review_model": "qwen3.8-max-preview",
        "review_prompt_version": TREND_REVIEW_PROMPT_VERSION,
        "verified_only": verified_only,
        **(params or {}),
    }
    params_hash = _hash_json(params)
    created_at = now_iso()
    trend_release_id = _stable_id(
        "trends", story_release_id, window, method, params_hash, created_at
    )
    stories = _load_engine_stories(conn, story_release_id)
    # Verified-only: filter stories to provenance-verified set
    if verified_only:
        from .verified_stories import get_verified_story_ids

        verified_ids = get_verified_story_ids(
            conn, story_release_id, signal_release_id=signal_release_id
        )
        stories = [s for s in stories if s["story_id"] in verified_ids]
    story_items = _load_story_item_ids(conn, story_release_id)
    facets = _load_item_facets(conn, facet_release.facet_release_id)
    frozen_items = {
        item.item_id: item for item in load_frozen_items(conn, facet_release.data_release_id)
    }
    if method == "embedding_v2":
        trends = _discover_trends_embedding_v2(
            conn,
            stories,
            story_items,
            frozen_items,
            data_release_id=facet_release.data_release_id,
            story_release=story_release,
            params=params,
        )
    else:
        # history_status из графа игнорируем: авторитетный статус считается ниже по
        # числу finalized-релизов (мёртвый код из ревью v3 §3.5).
        trends, _ = _discover_trends_graph(
            stories,
            story_items,
            facets,
            frozen_items,
            params=params,
        )
    data_release = get_data_release(conn, facet_release.data_release_id)
    history_release_count = 0
    if data_release is not None:
        history_release_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM data_releases
                WHERE profile = ? AND status = 'finalized' AND created_at <= ?
                """,
                (data_release.profile, data_release.created_at),
            ).fetchone()[0]
        )
    history_status = "ready" if history_release_count >= 7 else "insufficient_history"
    trends = _apply_trend_lifecycle_history(
        conn,
        trends,
        history_status=history_status,
    )
    trends = apply_cached_trend_reviews(
        conn,
        trends=trends,
        stories=stories,
        model=str(params["review_model"]),
        prompt_version=str(params["review_prompt_version"]),
    )
    confirmed_trend_count = sum(1 for trend, _ in trends if trend["review_status"] == "confirmed")
    metrics = {
        "story_count": len(stories),
        "trend_count": len(trends),
        "history_status": history_status,
        "history_release_count": history_release_count,
        "evidence_coverage": 1.0 if trends else 0.0,
        "confirmed_trend_count": confirmed_trend_count,
        "pending_review_count": len(trends) - confirmed_trend_count,
    }
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO trend_releases
               (trend_release_id, story_release_id, window, method, params_hash,
                status, history_status, metrics_json, git_sha, created_at)
               VALUES (?, ?, ?, ?, ?, 'building', ?, '{}', ?, ?)""",
            (
                trend_release_id,
                story_release_id,
                window,
                method,
                params_hash,
                history_status,
                _git_sha(),
                created_at,
            ),
        )
        for trend, memberships in trends:
            conn.execute(
                """INSERT INTO engine_trends
                   (trend_release_id, trend_id, name_ru, pattern, domain_ids,
                    confidence, lifecycle, source_scope, first_seen, last_seen,
                    story_count, source_count, project_scores, evidence_story_ids,
                    counterpoints, review_status, review_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trend_release_id,
                    trend["trend_id"],
                    trend["name_ru"],
                    trend["pattern"],
                    _json(trend["domain_ids"]),
                    trend["confidence"],
                    trend["lifecycle"],
                    trend["source_scope"],
                    trend["first_seen"],
                    trend["last_seen"],
                    trend["story_count"],
                    trend["source_count"],
                    _json(trend["project_scores"]),
                    _json(trend["evidence_story_ids"]),
                    _json(trend["counterpoints"]),
                    trend["review_status"],
                    trend["review_id"],
                ),
            )
            conn.executemany(
                """INSERT INTO engine_trend_stories
                   (trend_release_id, trend_id, story_id, membership_score, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        trend_release_id,
                        trend["trend_id"],
                        story_id,
                        score,
                        reason,
                    )
                    for story_id, score, reason in memberships
                ],
            )
        conn.execute(
            """UPDATE trend_releases
               SET status = 'evaluated', metrics_json = ?
               WHERE trend_release_id = ?""",
            (_json(metrics), trend_release_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return TrendRelease(
        trend_release_id=trend_release_id,
        story_release_id=story_release_id,
        method=method,
        window=window,
        status="evaluated",
        history_status=history_status,
        metrics=metrics,
        created_at=created_at,
    )


def get_trend_release(conn: sqlite3.Connection, trend_release_id: str) -> TrendRelease | None:
    row = conn.execute(
        "SELECT * FROM trend_releases WHERE trend_release_id = ?",
        (trend_release_id,),
    ).fetchone()
    if row is None:
        return None
    return TrendRelease(
        trend_release_id=str(row["trend_release_id"]),
        story_release_id=str(row["story_release_id"]),
        method=str(row["method"]),
        window=str(row["window"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        history_status=str(row["history_status"]),
        metrics=_json_dict(row["metrics_json"]),
        created_at=str(row["created_at"]),
    )


def prepare_trend_review_jobs(
    conn: sqlite3.Connection,
    trend_release_id: str,
    *,
    limit: int = 50,
    model: str = "qwen3.8-max-preview",
    prompt_version: str = TREND_REVIEW_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    release = get_trend_release(conn, trend_release_id)
    if release is None:
        raise ValueError(f"Unknown trend release: {trend_release_id}")
    stories = {
        str(story["story_id"]): story
        for story in _load_engine_stories(conn, release.story_release_id)
    }
    rows = conn.execute(
        """
        SELECT trend_id
        FROM engine_trends
        WHERE trend_release_id = ?
        ORDER BY confidence DESC, trend_id
        LIMIT ?
        """,
        (trend_release_id, max(0, limit)),
    ).fetchall()
    jobs: list[dict[str, Any]] = []
    for row in rows:
        trend_id = str(row["trend_id"])
        story_ids = [
            str(membership["story_id"])
            for membership in conn.execute(
                """
                SELECT story_id
                FROM engine_trend_stories
                WHERE trend_release_id = ? AND trend_id = ?
                ORDER BY story_id
                """,
                (trend_release_id, trend_id),
            ).fetchall()
        ]
        payload = _trend_review_payload(story_ids, stories)
        input_hash = _hash_json(payload)
        cached = conn.execute(
            """
            SELECT valid
            FROM llm_reviews
            WHERE model = ? AND prompt_version = ? AND input_hash = ?
            """,
            (model, prompt_version, input_hash),
        ).fetchone()
        if cached is not None and bool(cached["valid"]):
            continue
        jobs.append(
            {
                "target_id": trend_id,
                "story_ids": story_ids,
                "model": model,
                "prompt_version": prompt_version,
                "input_hash": input_hash,
                "prompt": build_trend_review_prompt(payload),
            }
        )
    return jobs


def store_trend_review_response(
    conn: sqlite3.Connection,
    *,
    target_id: str,
    input_hash: str,
    raw_response: str,
    allowed_story_ids: set[str],
    model: str = "qwen3.8-max-preview",
    prompt_version: str = TREND_REVIEW_PROMPT_VERSION,
) -> dict[str, Any]:
    review, errors = validate_trend_review(
        raw_response,
        allowed_story_ids=allowed_story_ids,
    )
    decision = review.decision if review else "invalid"
    review_id = _stable_id("llm_review", model, prompt_version, input_hash)
    conn.execute(
        """
        INSERT OR REPLACE INTO llm_reviews (
            review_id, target_kind, target_id, model, prompt_version,
            input_hash, decision, response_json, valid, created_at
        ) VALUES (?, 'trend', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            target_id,
            model,
            prompt_version,
            input_hash,
            decision,
            raw_response,
            int(review is not None),
            now_iso(),
        ),
    )
    conn.commit()
    return {
        "review_id": review_id,
        "decision": decision,
        "valid": review is not None,
        "errors": errors,
    }


def apply_cached_trend_reviews(
    conn: sqlite3.Connection,
    *,
    trends: list[tuple[dict[str, Any], list[tuple[str, float, str]]]],
    stories: list[dict[str, Any]],
    model: str,
    prompt_version: str,
) -> list[tuple[dict[str, Any], list[tuple[str, float, str]]]]:
    story_by_id = {str(story["story_id"]): story for story in stories}
    resolved: list[tuple[dict[str, Any], list[tuple[str, float, str]]]] = []
    for trend, memberships in trends:
        story_ids = [story_id for story_id, _, _ in memberships]
        payload = _trend_review_payload(story_ids, story_by_id)
        input_hash = _hash_json(payload)
        row = conn.execute(
            """
            SELECT review_id, response_json
            FROM llm_reviews
            WHERE model = ? AND prompt_version = ? AND input_hash = ? AND valid = 1
            """,
            (model, prompt_version, input_hash),
        ).fetchone()
        if row is None:
            resolved.append(
                (
                    {
                        **trend,
                        "review_status": "pending",
                        "review_id": "",
                    },
                    memberships,
                )
            )
            continue
        review, _ = validate_trend_review(
            str(row["response_json"]),
            allowed_story_ids=set(story_ids),
        )
        if review is None:
            resolved.append(
                (
                    {
                        **trend,
                        "review_status": "pending",
                        "review_id": "",
                    },
                    memberships,
                )
            )
            continue
        if review.decision == "reject":
            continue
        accepted_ids = set(review.story_ids)
        accepted_memberships = [
            membership for membership in memberships if membership[0] in accepted_ids
        ]
        if len(accepted_memberships) < 3:
            continue
        resolved.append(
            (
                {
                    **trend,
                    "name_ru": review.trend_name_ru or trend["name_ru"],
                    "pattern": review.pattern or trend["pattern"],
                    "domain_ids": review.domains or trend["domain_ids"],
                    "confidence": review.confidence,
                    "story_count": len(accepted_memberships),
                    "evidence_story_ids": review.evidence_story_ids,
                    "counterpoints": review.counterpoints,
                    "review_status": "confirmed",
                    "review_id": str(row["review_id"]),
                },
                accepted_memberships,
            )
        )
    return resolved


def _trend_review_payload(
    story_ids: list[str],
    stories: dict[str, dict[str, Any]],
) -> list[dict[str, object]]:
    return [
        {
            "story_id": story_id,
            "title": str(stories[story_id]["title"]),
            "summary_ru": str(stories[story_id].get("summary_ru", "")),
            "domain_ids": _json_list(stories[story_id].get("domain_ids"), ["other"]),
            "theme_ids": _json_list(stories[story_id].get("theme_ids")),
            "first_seen": str(stories[story_id].get("first_seen", "")),
            "last_seen": str(stories[story_id].get("last_seen", "")),
            "source_count": int(stories[story_id].get("source_count", 0)),
        }
        for story_id in sorted(story_ids)
        if story_id in stories
    ]


def _apply_trend_lifecycle_history(
    conn: sqlite3.Connection,
    trends: list[tuple[dict[str, Any], list[tuple[str, float, str]]]],
    *,
    history_status: str,
) -> list[tuple[dict[str, Any], list[tuple[str, float, str]]]]:
    resolved = []
    for trend, memberships in trends:
        lifecycle = "insufficient_history"
        previous = conn.execute(
            """
            SELECT t.*, r.created_at
            FROM engine_trends AS t
            JOIN trend_releases AS r
              ON r.trend_release_id = t.trend_release_id
            WHERE t.trend_id = ?
            ORDER BY r.created_at DESC
            LIMIT 1
            """,
            (trend["trend_id"],),
        ).fetchone()
        if history_status == "ready":
            if previous is None:
                lifecycle = "new"
            else:
                current_count = max(int(trend["story_count"]), 1)
                previous_count = max(int(previous["story_count"] or 0), 1)
                velocity_ratio = current_count / previous_count
                gap_days = _date_gap(
                    str(previous["last_seen"] or ""),
                    str(trend["last_seen"]),
                )
                if gap_days >= 7 and current_count >= 2:
                    lifecycle = "resurfacing"
                elif velocity_ratio >= 1.5:
                    lifecycle = "growing"
                elif velocity_ratio < 0.75 and gap_days >= 2:
                    lifecycle = "fading"
                else:
                    lifecycle = "stable"
        resolved.append(
            (
                {
                    **trend,
                    "lifecycle": lifecycle,
                },
                memberships,
            )
        )
    return resolved


def _date_gap(left: str, right: str) -> int:
    if not left or not right:
        return 0
    try:
        return abs(
            (datetime.fromisoformat(right).date() - datetime.fromisoformat(left).date()).days
        )
    except ValueError:
        return 0


def _discover_trends_graph(
    stories: list[dict[str, Any]],
    story_items: dict[str, list[str]],
    facets: dict[str, dict[str, Any]],
    frozen_items: dict[str, FrozenItem],
    *,
    params: dict[str, Any],
) -> tuple[
    list[tuple[dict[str, Any], list[tuple[str, float, str]]]],
    str,
]:
    """Find constrained communities of distinct events sharing a repeated pattern."""
    min_stories = int(params.get("min_stories", 3))
    min_dates = int(params.get("min_dates", 2))
    top_k = int(params.get("trend_top_k", 12))
    edge_threshold = float(params.get("trend_edge_threshold", 0.45))
    medoid_threshold = float(params.get("trend_medoid_threshold", 0.4))
    default_max_feature_df = min(500, max(20, int(len(stories) * 0.08)))
    max_feature_df = int(params.get("trend_max_feature_df") or default_max_feature_df)
    max_candidate_pairs = int(params.get("trend_max_candidate_pairs", 150_000))
    stories_by_id = {str(story["story_id"]): story for story in stories}
    feature_sets = {
        story_id: _story_trend_features(
            story,
            story_items.get(story_id, []),
            facets,
        )
        for story_id, story in stories_by_id.items()
    }
    feature_index: dict[str, list[str]] = defaultdict(list)
    for story_id, features in feature_sets.items():
        for kind in ("theme", "pain", "topic", "action"):
            for value in features[kind]:
                feature_index[f"{kind}:{value}"].append(story_id)

    shared_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    candidate_pair_total = 0
    for feature, story_ids in feature_index.items():
        unique_story_ids = sorted(set(story_ids))
        if len(unique_story_ids) <= 1 or len(unique_story_ids) > max_feature_df:
            continue
        pair_count = len(unique_story_ids) * (len(unique_story_ids) - 1) // 2
        if candidate_pair_total + pair_count > max_candidate_pairs:
            continue
        candidate_pair_total += pair_count
        for left_index, left_id in enumerate(unique_story_ids):
            for right_id in unique_story_ids[left_index + 1 :]:
                shared_by_pair[_pair_key(left_id, right_id)].add(feature)

    scored_edges: list[tuple[float, str, str, set[str]]] = []
    for (left_id, right_id), shared in shared_by_pair.items():
        specific_shared = {feature for feature in shared if not _is_generic_trend_pattern(feature)}
        shared_kinds = {feature.split(":", 1)[0] for feature in specific_shared}
        has_specific_pattern = (
            "topic" in shared_kinds and bool(shared_kinds & {"theme", "pain", "action"})
        ) or ("action" in shared_kinds and bool(shared_kinds & {"theme", "pain"}))
        if not has_specific_pattern:
            continue
        score = sum(_trend_feature_weight(feature) for feature in specific_shared)
        shared_domains = feature_sets[left_id]["domain"] & feature_sets[right_id]["domain"]
        score += min(0.1, len(shared_domains) * 0.05)
        date_distance = _story_date_distance(
            stories_by_id[left_id],
            stories_by_id[right_id],
        )
        if date_distance > 45:
            score -= 0.2
        elif date_distance > 30:
            score -= 0.08
        score = round(min(1.0, max(0.0, score)), 4)
        if score >= edge_threshold:
            scored_edges.append((score, left_id, right_id, specific_shared))

    nearest: dict[str, list[tuple[float, str, set[str]]]] = defaultdict(list)
    for score, left_id, right_id, shared in scored_edges:
        nearest[left_id].append((score, right_id, shared))
        nearest[right_id].append((score, left_id, shared))
    kept_pairs: set[tuple[str, str]] = set()
    for story_id, neighbours in nearest.items():
        for _, other_id, _ in sorted(
            neighbours,
            key=lambda value: (-value[0], value[1]),
        )[:top_k]:
            kept_pairs.add(_pair_key(story_id, other_id))
    edges = [edge for edge in scored_edges if _pair_key(edge[1], edge[2]) in kept_pairs]
    edge_by_pair = {
        _pair_key(left_id, right_id): (score, shared) for score, left_id, right_id, shared in edges
    }
    groups: dict[str, list[str]] = {story_id: [story_id] for story_id in stories_by_id}
    owner = {story_id: story_id for story_id in stories_by_id}
    for _score, left_id, right_id, _ in sorted(
        edges,
        key=lambda value: (-value[0], value[1], value[2]),
    ):
        left_owner = owner[left_id]
        right_owner = owner[right_id]
        if left_owner == right_owner:
            continue
        merged = sorted(set(groups[left_owner] + groups[right_owner]))
        medoid = _trend_medoid(merged, edge_by_pair)
        if any(
            story_id != medoid
            and (
                _pair_key(story_id, medoid) not in edge_by_pair
                or edge_by_pair[_pair_key(story_id, medoid)][0] < medoid_threshold
            )
            for story_id in merged
        ):
            continue
        groups[left_owner] = merged
        del groups[right_owner]
        for story_id in merged:
            owner[story_id] = left_owner

    unique_dates = {
        str(story["first_seen"]) for story in stories if str(story.get("first_seen") or "")
    }
    history_status = "ready" if len(unique_dates) >= 7 else "insufficient_history"
    result: list[tuple[dict[str, Any], list[tuple[str, float, str]]]] = []
    used_trend_ids: set[str] = set()
    for story_ids in sorted(groups.values(), key=lambda ids: (-len(ids), ids)):
        if len(story_ids) < min_stories:
            continue
        selected_stories = [stories_by_id[story_id] for story_id in story_ids]
        dates = {
            str(story["first_seen"])
            for story in selected_stories
            if str(story.get("first_seen") or "")
        }
        if len(dates) < min_dates:
            continue
        feature_counts: dict[str, int] = defaultdict(int)
        for story_id in story_ids:
            for kind in ("theme", "pain", "topic", "action"):
                for value in feature_sets[story_id][kind]:
                    feature_counts[f"{kind}:{value}"] += 1
        pattern_features = [
            (count, feature)
            for feature, count in feature_counts.items()
            if count >= min_stories
            and feature.split(":", 1)[0] in {"topic", "action", "pain"}
            and not _is_generic_trend_pattern(feature)
        ]
        if not pattern_features:
            continue
        _, pattern_key = max(
            pattern_features,
            key=lambda value: (
                value[0],
                _trend_feature_weight(value[1]),
                value[1],
            ),
        )
        domain_ids = normalize_domain_ids(
            [
                domain
                for story in selected_stories
                for domain in _json_list(story.get("domain_ids"), ["other"])
            ]
        )
        trend_id = _stable_id("trend", pattern_key, ",".join(domain_ids))
        if trend_id in used_trend_ids:
            continue
        used_trend_ids.add(trend_id)
        source_clusters = {
            frozen_items[item_id].source_cluster
            for story_id in story_ids
            for item_id in story_items.get(story_id, [])
            if item_id in frozen_items
        }
        providers = {
            frozen_items[item_id].provider
            for story_id in story_ids
            for item_id in story_items.get(story_id, [])
            if item_id in frozen_items
        }
        source_scope = (
            "cross_source"
            if len(source_clusters) >= 2
            else "community_only"
            if source_clusters <= {"voices", "developers"}
            else "mainstream_only"
        )
        first_seen = min(dates)
        last_seen = max(str(story.get("last_seen") or first_seen) for story in selected_stories)
        confidence = round(
            min(
                0.95,
                0.42 + len(story_ids) * 0.06 + len(source_clusters) * 0.05 + len(dates) * 0.03,
            ),
            4,
        )
        trend = {
            "trend_id": trend_id,
            "name_ru": _trend_display_name(pattern_key),
            "pattern": pattern_key.split(":", 1)[1],
            "domain_ids": domain_ids,
            "confidence": confidence,
            "lifecycle": _trend_lifecycle(
                first_seen,
                last_seen,
                len(dates),
                history_status,
            ),
            "source_scope": source_scope,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "story_count": len(story_ids),
            "source_count": len(providers),
            "project_scores": {
                project: max(
                    (
                        _json_int_dict(story.get("project_scores")).get(project, 0)
                        for story in selected_stories
                    ),
                    default=0,
                )
                for project in ("book", "rbc", "business")
            },
            "evidence_story_ids": story_ids[:5],
            "counterpoints": [],
        }
        memberships = []
        medoid = _trend_medoid(story_ids, edge_by_pair)
        for story_id in story_ids:
            if story_id == medoid:
                memberships.append((story_id, 1.0, "trend medoid"))
                continue
            edge_score, shared = edge_by_pair[_pair_key(story_id, medoid)]
            memberships.append(
                (
                    story_id,
                    edge_score,
                    f"shared pattern features: {', '.join(sorted(shared))}",
                )
            )
        result.append((trend, memberships))
    return result[:50], history_status


def _story_trend_features(
    story: dict[str, Any],
    item_ids: list[str],
    facets: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    themes = {
        theme
        for item_id in item_ids
        for theme in (
            _json_list(facets.get(item_id, {}).get("candidate_themes"))
            + _json_list(facets.get(item_id, {}).get("theme_ids"))
        )
    }
    pains = {
        pain
        for item_id in item_ids
        for pain in _json_list(facets.get(item_id, {}).get("pain_points"))
    }
    actions = {
        action
        for item_id in item_ids
        if (
            action := str(
                _json_dict(facets.get(item_id, {}).get("event_frame_json")).get("action") or ""
            )
        )
    }
    return {
        "theme": themes,
        "pain": pains,
        "topic": set(_story_topic_keys(story)),
        "action": actions,
        "domain": set(_json_list(story.get("domain_ids"), ["other"])),
    }


def _trend_feature_weight(feature: str) -> float:
    kind = feature.split(":", 1)[0]
    return {
        "theme": 0.55,
        "pain": 0.5,
        "topic": 0.45,
        "action": 0.12,
    }.get(kind, 0.0)


def _trend_medoid(
    story_ids: list[str],
    edges: dict[tuple[str, str], tuple[float, set[str]]],
) -> str:
    return max(
        sorted(story_ids),
        key=lambda candidate: sum(
            edges.get(_pair_key(candidate, other), (0.0, set()))[0]
            for other in story_ids
            if other != candidate
        ),
    )


def _story_date_distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    left_date = str(left.get("first_seen") or "")
    right_date = str(right.get("first_seen") or "")
    if not left_date or not right_date:
        return 0
    try:
        return abs(
            (
                datetime.fromisoformat(left_date).date() - datetime.fromisoformat(right_date).date()
            ).days
        )
    except ValueError:
        return 0


def _discover_trends(
    stories: list[dict[str, Any]],
    story_items: dict[str, list[str]],
    facets: dict[str, dict[str, Any]],
    frozen_items: dict[str, FrozenItem],
    *,
    params: dict[str, Any],
) -> tuple[
    list[tuple[dict[str, Any], list[tuple[str, float, str]]]],
    str,
]:
    min_stories = int(params.get("min_stories", 3))
    min_dates = int(params.get("min_dates", 2))
    buckets: dict[str, set[str]] = defaultdict(set)
    for story in stories:
        story_id = str(story["story_id"])
        item_ids = story_items.get(story_id, [])
        themes = {
            theme
            for item_id in item_ids
            for theme in _json_list(facets.get(item_id, {}).get("candidate_themes"))
        }
        pains = {
            pain
            for item_id in item_ids
            for pain in _json_list(facets.get(item_id, {}).get("pain_points"))
        }
        for theme in themes:
            buckets[f"theme:{theme}"].add(story_id)
        for pain in pains:
            buckets[f"pain:{pain}"].add(story_id)
        for topic in _story_topic_keys(story):
            buckets[f"pattern:{topic}"].add(story_id)

    unique_dates = {
        str(story["first_seen"]) for story in stories if str(story.get("first_seen") or "")
    }
    history_status = "ready" if len(unique_dates) >= 7 else "insufficient_history"
    stories_by_id = {str(story["story_id"]): story for story in stories}
    seen_story_sets: set[tuple[str, ...]] = set()
    result = []
    for key, story_id_set in sorted(buckets.items(), key=lambda entry: (-len(entry[1]), entry[0])):
        story_ids = tuple(sorted(story_id_set))
        if len(story_ids) < min_stories or story_ids in seen_story_sets:
            continue
        selected_stories = [stories_by_id[story_id] for story_id in story_ids]
        dates = {
            str(story["first_seen"])
            for story in selected_stories
            if str(story.get("first_seen") or "")
        }
        if len(dates) < min_dates:
            continue
        source_clusters = {
            frozen_items[item_id].source_cluster
            for story_id in story_ids
            for item_id in story_items.get(story_id, [])
            if item_id in frozen_items
        }
        providers = {
            frozen_items[item_id].provider
            for story_id in story_ids
            for item_id in story_items.get(story_id, [])
            if item_id in frozen_items
        }
        source_scope = (
            "cross_source"
            if len(source_clusters) >= 2
            else "community_only"
            if source_clusters <= {"voices", "developers"}
            else "mainstream_only"
        )
        domain_ids = normalize_domain_ids(
            [
                domain
                for story in selected_stories
                for domain in _json_list(story.get("domain_ids"), ["other"])
            ]
        )
        trend_id = _stable_id("trend", key)
        first_seen = min(dates)
        last_seen = max(str(story.get("last_seen") or first_seen) for story in selected_stories)
        lifecycle = _trend_lifecycle(first_seen, last_seen, len(dates), history_status)
        confidence = round(
            min(
                0.95,
                0.45 + len(story_ids) * 0.05 + len(source_clusters) * 0.06 + len(dates) * 0.03,
            ),
            4,
        )
        pattern = key.split(":", 1)[1]
        name_ru = _trend_display_name(key)
        project_scores = {
            project: max(
                (
                    _json_int_dict(story.get("project_scores")).get(project, 0)
                    for story in selected_stories
                ),
                default=0,
            )
            for project in ("book", "rbc", "business")
        }
        trend = {
            "trend_id": trend_id,
            "name_ru": name_ru,
            "pattern": pattern,
            "domain_ids": domain_ids,
            "confidence": confidence,
            "lifecycle": lifecycle,
            "source_scope": source_scope,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "story_count": len(story_ids),
            "source_count": len(providers),
            "project_scores": project_scores,
            "evidence_story_ids": list(story_ids[:5]),
        }
        memberships = [(story_id, confidence, f"shared {key}") for story_id in story_ids]
        result.append((trend, memberships))
        seen_story_sets.add(story_ids)
    return result[:50], history_status


def _release_passes_publication_gate(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    story_release: StoryRelease,
    trend_release: TrendRelease,
) -> bool:
    """Allow production publish through legacy gates or the newer Engine quality floors."""

    legacy_gate = bool(story_release.metrics.get("publication_gate")) and bool(
        trend_release.metrics.get("publication_gate")
    )
    if legacy_gate:
        return True
    from .quality import compute_quality, evaluate_floors

    metrics = compute_quality(
        conn,
        data_release_id=data_release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
    )
    floors = evaluate_floors(metrics)
    return bool(floors) and all(floor.passed for floor in floors)


def publish_radar(
    conn: sqlite3.Connection,
    *,
    story_release_id: str,
    trend_release_id: str,
    channel: str = "broad",
    allow_partial: bool = False,
) -> Publication:
    story_release = get_story_release(conn, story_release_id)
    trend_release = get_trend_release(conn, trend_release_id)
    if story_release is None or trend_release is None:
        raise ValueError("Story or trend release not found")
    if trend_release.story_release_id != story_release_id:
        raise ValueError("Trend release does not belong to story release")
    publishable_statuses = {"evaluated", "published"}
    if (
        story_release.status not in publishable_statuses
        or trend_release.status not in publishable_statuses
    ):
        raise ValueError("Only evaluated releases can be published")
    facet_release = get_facet_release(conn, story_release.facet_release_id)
    if facet_release is None:
        raise ValueError("Facet release not found")
    data_release = get_data_release(conn, facet_release.data_release_id)
    if data_release is None or not verify_data_release(conn, data_release.release_id):
        raise ValueError("Data release not found or checksum failed")
    if data_release.input_status != "complete":
        if channel in {"broad", "ai-native"}:
            raise ValueError(
                "Production channel requires a complete Data Release; "
                "partial input may be inspected or published only to shadow"
            )
        if not allow_partial:
            raise ValueError("Partial data release requires allow_partial=True")
    if channel in {"broad", "ai-native"} and not _release_passes_publication_gate(
        conn,
        data_release_id=data_release.release_id,
        story_release=story_release,
        trend_release=trend_release,
    ):
        raise ValueError(
            "Production channel requires passed Story/Trend publication gates "
            "or passed Engine quality floors; use a shadow channel while evaluating"
        )
    current = get_current_publication(conn, channel)
    previous_id = current.publication_id if current else ""
    created_at = now_iso()
    publication_id = _stable_id(
        "publication", channel, story_release_id, trend_release_id, created_at
    )
    event_id = _stable_id("publication_event", publication_id, "publish")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO radar_publications
               (publication_id, channel, data_release_id, story_release_id,
                trend_release_id, input_status, allow_partial,
                previous_publication_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                publication_id,
                channel,
                data_release.release_id,
                story_release_id,
                trend_release_id,
                data_release.input_status,
                int(allow_partial),
                previous_id,
                created_at,
            ),
        )
        conn.execute(
            """INSERT INTO published_channels
               (channel, current_publication_id, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(channel) DO UPDATE SET
                 current_publication_id = excluded.current_publication_id,
                 updated_at = excluded.updated_at""",
            (channel, publication_id, created_at),
        )
        conn.execute(
            """INSERT INTO publication_history
               (event_id, channel, action, from_publication_id,
                to_publication_id, created_at)
               VALUES (?, ?, 'publish', ?, ?, ?)""",
            (event_id, channel, previous_id, publication_id, created_at),
        )
        conn.execute(
            "UPDATE story_releases SET status = 'published' WHERE story_release_id = ?",
            (story_release_id,),
        )
        conn.execute(
            "UPDATE trend_releases SET status = 'published' WHERE trend_release_id = ?",
            (trend_release_id,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return Publication(
        publication_id=publication_id,
        channel=channel,
        data_release_id=data_release.release_id,
        story_release_id=story_release_id,
        trend_release_id=trend_release_id,
        input_status=data_release.input_status,
        previous_publication_id=previous_id,
        created_at=created_at,
    )


def rollback_publication(
    conn: sqlite3.Connection,
    *,
    channel: str,
    to_publication_id: str,
) -> Publication:
    target = get_publication(conn, to_publication_id)
    if target is None or target.channel != channel:
        raise ValueError("Rollback target is not a publication for this channel")
    current = get_current_publication(conn, channel)
    from_id = current.publication_id if current else ""
    created_at = now_iso()
    event_id = _stable_id("publication_event", channel, from_id, to_publication_id, created_at)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO published_channels
               (channel, current_publication_id, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(channel) DO UPDATE SET
                 current_publication_id = excluded.current_publication_id,
                 updated_at = excluded.updated_at""",
            (channel, to_publication_id, created_at),
        )
        conn.execute(
            """INSERT INTO publication_history
               (event_id, channel, action, from_publication_id,
                to_publication_id, created_at)
               VALUES (?, ?, 'rollback', ?, ?, ?)""",
            (event_id, channel, from_id, to_publication_id, created_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return target


def get_publication(conn: sqlite3.Connection, publication_id: str) -> Publication | None:
    row = conn.execute(
        "SELECT * FROM radar_publications WHERE publication_id = ?",
        (publication_id,),
    ).fetchone()
    if row is None:
        return None
    return Publication(
        publication_id=str(row["publication_id"]),
        channel=str(row["channel"]),
        data_release_id=str(row["data_release_id"]),
        story_release_id=str(row["story_release_id"]),
        trend_release_id=str(row["trend_release_id"]),
        input_status=str(row["input_status"]),
        previous_publication_id=str(row["previous_publication_id"]),
        created_at=str(row["created_at"]),
    )


def get_current_publication(conn: sqlite3.Connection, channel: str) -> Publication | None:
    row = conn.execute(
        "SELECT current_publication_id FROM published_channels WHERE channel = ?",
        (channel,),
    ).fetchone()
    return get_publication(conn, str(row[0])) if row else None


def list_publications(conn: sqlite3.Connection, channel: str | None = None) -> list[Publication]:
    if channel:
        rows = conn.execute(
            """SELECT publication_id FROM radar_publications
               WHERE channel = ? ORDER BY created_at DESC""",
            (channel,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT publication_id FROM radar_publications ORDER BY created_at DESC"
        ).fetchall()
    return [
        publication
        for row in rows
        if (publication := get_publication(conn, str(row["publication_id"]))) is not None
    ]


def label_engine_target(
    conn: sqlite3.Connection,
    *,
    target_kind: str,
    target_id: str,
    release_id: str,
    label: LabelValue,
    note: str = "",
) -> str:
    created_at = now_iso()
    label_id = _stable_id("label", target_kind, target_id, release_id, label, created_at)
    conn.execute(
        """INSERT INTO engine_labels
           (label_id, target_kind, target_id, release_id, label, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (label_id, target_kind, target_id, release_id, label, note, created_at),
    )
    conn.commit()
    return label_id


def active_label_story_pairs(
    conn: sqlite3.Connection,
    story_release_id: str,
    *,
    target: int = 150,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Interactively label the most informative story pairs for active learning."""
    release = get_story_release(conn, story_release_id)
    if release is None:
        raise ValueError(f"Story release not found: {story_release_id}")
    facet_release = get_facet_release(conn, release.facet_release_id)
    if facet_release is None:
        raise ValueError(f"Facet release not found: {release.facet_release_id}")
    item_rows = {
        str(row["item_id"]): row
        for row in conn.execute(
            "SELECT * FROM release_items WHERE release_id = ?",
            (facet_release.data_release_id,),
        ).fetchall()
    }
    existing = {
        str(row["target_id"])
        for row in conn.execute(
            """
            SELECT target_id
            FROM engine_labels
            WHERE target_kind = 'story_pair' AND release_id = ?
            """,
            (story_release_id,),
        ).fetchall()
    }
    rows = conn.execute(
        """
        SELECT *
        FROM story_candidate_pairs
        WHERE story_release_id = ?
        ORDER BY
          CASE decision WHEN 'review' THEN 0 WHEN 'auto_merge' THEN 1 ELSE 2 END,
          ABS(score - 0.82),
          score DESC,
          item_id_a,
          item_id_b
        """,
        (story_release_id,),
    ).fetchall()
    labeled: Counter[LabelValue] = Counter()
    asked = 0
    for row in rows:
        left_id = str(row["item_id_a"])
        right_id = str(row["item_id_b"])
        target_id = "|".join(_pair_key(left_id, right_id))
        if target_id in existing:
            continue
        left = item_rows.get(left_id)
        right = item_rows.get(right_id)
        if left is None or right is None:
            continue
        features = _json_dict(row["features_json"])
        output_fn("")
        output_fn(f"[{asked + 1}/{target}] score={float(row['score']):.3f} {row['decision']}")
        output_fn(
            f"A {left['provider']} / {left['source_cluster']} / {left['source_section']}: "
            f"{left['title']}"
        )
        output_fn(
            f"B {right['provider']} / {right['source_cluster']} / {right['source_section']}: "
            f"{right['title']}"
        )
        output_fn(
            "features: "
            + json.dumps(
                {
                    key: features.get(key)
                    for key in (
                        "title_score",
                        "token_jaccard",
                        "dense_similarity",
                        "shared_entities",
                        "shared_action_tokens",
                        "date_distance_days",
                        "number_conflict",
                        "location_conflict",
                        "person_conflict",
                        "generated_by",
                    )
                },
                ensure_ascii=False,
            )
        )
        answer = input_fn("same story? [y]es/[n]o/[u]nsure/[f]inish: ").strip().lower()
        if answer in {"f", "finish", "q", "quit"}:
            break
        label: LabelValue | None = None
        if answer in {"y", "yes", "same", "s"}:
            label = "same_story"
        elif answer in {"n", "no", "different", "d"}:
            label = "different_story"
        elif answer in {"u", "unsure", "low", "l"}:
            label = "low_signal"
        if label is None:
            output_fn("skipped: unknown answer")
            continue
        label_engine_target(
            conn,
            target_kind="story_pair",
            target_id=target_id,
            release_id=story_release_id,
            label=label,
            note="active_label",
        )
        labeled[label] += 1
        asked += 1
        if asked >= target:
            break
    return {
        "story_release_id": story_release_id,
        "asked": asked,
        "labels": dict(labeled),
    }


def auto_label_story_pairs(
    conn: sqlite3.Connection,
    story_release_id: str,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Детерминированная авто-разметка пар высокого доверия (Фаза 3, без человека).

    Метки ставятся правилами ``story_scoring.auto_label_pair``: provenance-якоря →
    ``same_story``, жёсткие конфликты → ``different_story``. Существующие метки
    (человеческие или более ранние авто) не перезаписываются.
    """

    release = get_story_release(conn, story_release_id)
    if release is None:
        raise ValueError(f"Story release not found: {story_release_id}")
    existing = {
        str(row["target_id"])
        for row in conn.execute(
            """SELECT target_id FROM engine_labels
               WHERE target_kind = 'story_pair' AND release_id = ?""",
            (story_release_id,),
        ).fetchall()
    }
    rows = conn.execute(
        """SELECT item_id_a, item_id_b, decision, reason, features_json
           FROM story_candidate_pairs WHERE story_release_id = ?""",
        (story_release_id,),
    ).fetchall()
    counts: Counter[str] = Counter()
    pending: list[tuple[str, str, str, str, str, str]] = []
    created_at = now_iso()
    for row in rows:
        target_id = "|".join(_pair_key(str(row["item_id_a"]), str(row["item_id_b"])))
        if target_id in existing:
            continue
        features = _json_dict(row["features_json"])
        label = auto_label_pair(str(row["decision"]), str(row["reason"]), features)
        if label is None:
            continue
        counts[label] += 1
        label_id = _stable_id("label", "story_pair", target_id, story_release_id, label, created_at)
        pending.append((label_id, "story_pair", target_id, story_release_id, label, "auto_label"))
    if persist and pending:
        conn.executemany(
            """INSERT INTO engine_labels
               (label_id, target_kind, target_id, release_id, label, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(*row, created_at) for row in pending],
        )
        conn.commit()
    return {
        "story_release_id": story_release_id,
        "added": len(pending),
        "labels": dict(counts),
    }


def train_story_merge_model(
    conn: sqlite3.Connection,
    story_release_id: str,
    *,
    target_precision: float = 0.95,
    persist: bool = True,
) -> dict[str, Any]:
    """Обучает логистическую модель слияния на размеченных парах (Фаза 3).

    Источники меток по приоритету: человеческие (``engine_labels`` без note=auto_label)
    поверх автоматических. Модель и её хэш сохраняются в ``metrics_json`` релиза, чтобы
    релиз оставался воспроизводимым.
    """

    release = get_story_release(conn, story_release_id)
    if release is None:
        raise ValueError(f"Story release not found: {story_release_id}")
    label_rows = conn.execute(
        """SELECT target_id, label, note FROM engine_labels
           WHERE target_kind = 'story_pair' AND release_id = ?""",
        (story_release_id,),
    ).fetchall()
    # Приоритет меток: человек > Qwen > авто-разметка (детерминированно, без гонок).
    auto_labels: dict[str, str] = {}
    qwen_labels: dict[str, str] = {}
    human_labels: dict[str, str] = {}
    for row in label_rows:
        note = str(row["note"] or "")
        target_id = str(row["target_id"])
        label = str(row["label"])
        if note == "auto_label":
            auto_labels[target_id] = label
        elif note == "qwen_review":
            qwen_labels[target_id] = label
        else:
            human_labels[target_id] = label
    labels_by_id = {**auto_labels, **qwen_labels, **human_labels}
    pair_rows = conn.execute(
        """SELECT item_id_a, item_id_b, features_json
           FROM story_candidate_pairs WHERE story_release_id = ?""",
        (story_release_id,),
    ).fetchall()
    vectors: list[Any] = []
    labels: list[bool] = []
    for row in pair_rows:
        target_id = "|".join(_pair_key(str(row["item_id_a"]), str(row["item_id_b"])))
        pair_label = labels_by_id.get(target_id)
        if pair_label in (None, "low_signal"):
            continue
        vectors.append(extract_feature_vector(_json_dict(row["features_json"])))
        labels.append(pair_label == "same_story")
    if not vectors:
        raise ValueError(f"No labeled pairs available for training: {story_release_id}")
    label_source = "human" if human_labels else ("qwen" if qwen_labels else "auto")
    model = train_merge_model(
        vectors,
        labels,
        target_precision=target_precision,
        label_source=label_source,
    )
    summary = model.to_params()
    if persist:
        metrics = {**release.metrics, "merge_model": summary}
        conn.execute(
            "UPDATE story_releases SET metrics_json = ? WHERE story_release_id = ?",
            (_json(metrics), story_release_id),
        )
        conn.commit()
    return {
        "story_release_id": story_release_id,
        "labeled_pairs": len(vectors),
        "label_source": label_source,
        "model": summary,
    }


def _load_pulse_items(
    conn: sqlite3.Connection, data_release_id: str, snapshot_date: str
) -> list[ContentItem]:
    rows = conn.execute(
        """SELECT item_id, provider, source_cluster, external_id, source_section,
                  title, excerpt, canonical_url, discussion_url, target_url,
                  domain_ids, metadata, raw_engagement, snapshot_date,
                  published_at, observed_at
           FROM release_items
           WHERE release_id = ? AND snapshot_date = ? AND provider = 'reddit'""",
        (data_release_id, snapshot_date),
    ).fetchall()
    items: list[ContentItem] = []
    for r in rows:
        items.append(
            ContentItem(
                item_id=r["item_id"],
                provider=r["provider"],
                source_cluster=r["source_cluster"],
                external_id=r["external_id"],
                source_section=r["source_section"],
                title=r["title"],
                excerpt=r["excerpt"] or "",
                canonical_url=r["canonical_url"] or "",
                discussion_url=r["discussion_url"] or "",
                target_url=r["target_url"] or "",
                domain_ids=_json_list(r["domain_ids"], ["other"]),
                metadata=_json_dict(r["metadata"]),
                raw_engagement=_json_dict(r["raw_engagement"]),
                snapshot_date=r["snapshot_date"],
                observed_at=r["observed_at"] or "",
                published_at=r["published_at"] or None,
            )
        )
    return items


def _load_pulse_history_titles(
    conn: sqlite3.Connection,
    data_release_id: str,
    profile: str,
    snapshot_date: str,
    history_window_days: int,
) -> set[str]:
    rows = conn.execute(
        """SELECT ri.title
           FROM release_items ri
           JOIN data_releases dr ON dr.release_id = ri.release_id
           WHERE dr.status = 'finalized' AND dr.profile = ?
             AND ri.provider = 'reddit' AND ri.snapshot_date < ?
             AND date(ri.snapshot_date) >= date(?, '-' || ? || ' days')""",
        (profile, snapshot_date, snapshot_date, history_window_days),
    ).fetchall()
    from .reddit_pulse import tokenize_title

    return {" ".join(sorted(tokenize_title(str(r["title"])))) for r in rows}


def create_signal_release(
    conn: sqlite3.Connection,
    *,
    items: list[ContentItem],
    history_titles: set[str],
    pack_by_subreddit: dict[str, str],
    story_release_id: str,
    data_release_id: str,
    profile: str,
    date: str,
    method: str = "reddit_pulse_v2",
    history_window_days: int = 7,
    signal_release_id: str | None = None,
) -> dict[str, Any]:
    """Собирает Reddit Pulse и пишет signal_release + community_signals (Фаза 7/cycle)."""

    facet_row = conn.execute(
        "SELECT facet_release_id FROM story_releases WHERE story_release_id = ?",
        (story_release_id,),
    ).fetchone()
    facet_release_id = str(facet_row["facet_release_id"]) if facet_row else ""
    story_id_by_item_id: dict[str, str] = {}
    mainstream_coverage_by_story_id: dict[str, int] = {}
    if story_release_id:
        story_rows = conn.execute(
            "SELECT item_id, story_id FROM engine_story_items WHERE story_release_id = ?",
            (story_release_id,),
        ).fetchall()
        story_id_by_item_id = {str(r["item_id"]): str(r["story_id"]) for r in story_rows}
        coverage_rows = conn.execute(
            """SELECT esi.story_id,
                      COUNT(DISTINCT ri.provider || ':' ||
                            COALESCE(NULLIF(ri.source_section, ''), ri.source_cluster))
                      AS mainstream_coverage
               FROM engine_story_items esi
               JOIN release_items ri ON ri.release_id = ? AND ri.item_id = esi.item_id
               WHERE esi.story_release_id = ? AND ri.provider != 'reddit'
                 AND ri.source_cluster IN ('mainstream', 'business', 'tech_culture')
               GROUP BY esi.story_id""",
            (data_release_id, story_release_id),
        ).fetchall()
        mainstream_coverage_by_story_id = {
            str(r["story_id"]): int(r["mainstream_coverage"] or 0) for r in coverage_rows
        }
    balance_rows = conn.execute(
        "SELECT source_cluster, COUNT(*) AS n FROM release_items "
        "WHERE release_id = ? GROUP BY source_cluster",
        (data_release_id,),
    ).fetchall()
    cluster_counts = {str(r["source_cluster"]): int(r["n"]) for r in balance_rows}
    gap_available = perspective_gap_available_counts(
        cluster_counts.get("voices", 0), cluster_counts.get("mainstream", 0)
    )
    signals = build_reddit_pulse_signals(
        items,
        history_titles,
        pack_by_subreddit=pack_by_subreddit,
        story_id_by_item_id=story_id_by_item_id,
        mainstream_coverage_by_story_id=mainstream_coverage_by_story_id,
        history_available=bool(history_titles),
        gap_available=gap_available,
    )
    params = {
        "method": method,
        "profile": profile,
        "date": date,
        "history_window_days": history_window_days,
        "story_release_id": story_release_id,
        "facet_release_id": facet_release_id,
        "history_item_count": len(history_titles),
        "pack_count": len(pack_by_subreddit),
    }
    params_hash = _hash_json(params)
    signal_release_id = signal_release_id or _stable_id(
        "signals", data_release_id, date, profile, method, params_hash, now_iso()
    )
    now = now_iso()
    metrics = {
        "schema_version": 2,
        "signal_count": len(signals),
        "history_item_count": len(history_titles),
        "history_available": bool(history_titles),
        "linked_story_count": len({s.linked_story_id for s in signals if s.linked_story_id}),
        "mainstream_covered_signal_count": sum(
            1 for s in signals if s.mainstream_coverage_count > 0
        ),
        "perspective_gap_available": gap_available,
        "neutral_novelty": not bool(history_titles),
    }
    conn.execute(
        """INSERT OR REPLACE INTO signal_releases
           (signal_release_id, data_release_id, facet_release_id, story_release_id,
            date, method, params_hash, metrics_json, git_sha, status, signal_count,
            created_at, finalized_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'finalized', ?, ?, ?)""",
        (
            signal_release_id,
            data_release_id,
            facet_release_id,
            story_release_id,
            date,
            method,
            params_hash,
            _json(metrics),
            _git_sha(),
            len(signals),
            now,
            now,
        ),
    )
    conn.execute("DELETE FROM community_signals WHERE signal_release_id = ?", (signal_release_id,))
    conn.executemany(
        """INSERT OR REPLACE INTO community_signals
           (signal_release_id, signal_id, item_id, subreddit, pack_id, signal_type, title,
            discussion_url, target_url, pulse_score, subreddit_percentile, score_velocity,
            comment_velocity, discussion_depth, comment_score_ratio,
            cross_subreddit_repetition, novelty, domain_ids_json, theme_ids_json,
            pain_points_json, project_scores_json, linked_story_id,
            mainstream_coverage_count, perspective_gap)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                signal_release_id,
                s.signal_id,
                s.item_id,
                s.subreddit,
                s.pack_id,
                s.signal_type,
                s.title,
                s.discussion_url,
                s.target_url,
                s.pulse_score,
                s.subreddit_percentile,
                s.score_velocity,
                s.comment_velocity,
                s.discussion_depth,
                s.comment_score_ratio,
                s.cross_subreddit_repetition,
                s.novelty,
                _json(s.domain_ids),
                _json(s.theme_ids),
                _json(s.pain_points),
                _json(s.project_scores),
                s.linked_story_id,
                s.mainstream_coverage_count,
                s.perspective_gap,
            )
            for s in signals
        ],
    )
    conn.commit()
    by_type: dict[str, int] = {}
    for s in signals:
        by_type[s.signal_type] = by_type.get(s.signal_type, 0) + 1
    top5 = sorted(signals, key=lambda x: x.pulse_score, reverse=True)[:5]
    return {
        "signal_release_id": signal_release_id,
        "metrics": metrics,
        "by_type": by_type,
        "top5": [
            {
                "title": s.title[:80],
                "subreddit": s.subreddit,
                "pulse_score": s.pulse_score,
                "type": s.signal_type,
            }
            for s in top5
        ],
    }


async def run_engine_cycle(
    corpus_conn: sqlite3.Connection,
    conn: sqlite3.Connection,
    *,
    corpus_path: Path,
    profile: str = "broad",
    window: int = 7,
    theme_catalog: dict[str, list[str]] | None = None,
    pack_by_subreddit: dict[str, str] | None = None,
    trend_method: str = "embedding_v2",
    embed_model: str = MODEL2VEC_DEFAULT,
    review_model: str = "qwen3.6-flash",
    review_limit: int = 0,
    trend_review_model: str = "qwen3.8-max-preview",
    trend_review_limit: int = 0,
    review_runner: Callable[[str, str], Awaitable[str]] | None = None,
    publish_channel: str | None = None,
    allow_partial: bool = True,
    pulse: bool = True,
    history_window_days: int = 7,
) -> dict[str, Any]:
    """Полный ночной цикл нового Engine (Фаза 7): релиз → stories → разметка → обучение →
    trends → pulse → (опц.) публикация. Один вызов = одна cron-строка."""

    corpus_conn.row_factory = sqlite3.Row
    run_rows = corpus_conn.execute(
        """SELECT run_id FROM runs
           WHERE profile = ? AND status <> 'running'
           ORDER BY snapshot_date DESC LIMIT ?""",
        (profile, window),
    ).fetchall()
    if not run_rows:
        raise ValueError(f"No finalized runs for profile {profile}")
    run_ids = [str(r["run_id"]) for r in reversed(run_rows)]
    data = create_data_release(corpus_conn, conn, source_db_path=corpus_path, run_ids=run_ids)
    facets = create_facet_release(
        conn, data_release_id=data.release_id, theme_catalog=theme_catalog or {}
    )
    # Кэш эмбеддингов для embedding_v2 (torch-free model2vec). При любой ошибке
    # (пакет не установлен, нет сети для загрузки модели) — graceful fallback:
    # stories/trends строятся без плотных векторов (trend_method → story_graph_v1).
    embed_ok = False
    if embed_model:
        try:
            cache_release_embeddings(conn, data_release_id=data.release_id, model_name=embed_model)
            embed_ok = True
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "embedding cache failed (%s); falling back to story_graph_v1 trends", exc
            )
    story_params: dict[str, Any] = {"review_model": review_model}
    if embed_ok:
        story_params["embedding_model"] = embed_model
    use_trend_method = (
        trend_method if (trend_method != "embedding_v2" or embed_ok) else "story_graph_v1"
    )
    provisional_stories = create_story_release(
        conn,
        facet_release_id=facets.facet_release_id,
        # review_model должен совпадать с моделью Qwen-разметки ниже, иначе кэш
        # llm_reviews не попадёт в apply_cached_story_reviews при следующем цикле.
        params=story_params,
    )
    auto = auto_label_story_pairs(conn, provisional_stories.story_release_id)
    review_attempted = 0
    review_valid = 0
    review_invalid = 0
    review_errors: list[str] = []
    if review_limit > 0 and review_runner is not None:
        jobs = prepare_story_review_jobs(
            conn, provisional_stories.story_release_id, limit=review_limit, model=review_model
        )
        for job in jobs:
            review_attempted += 1
            try:
                raw = await review_runner(str(job["prompt"]), review_model)
                result = store_story_review_response(
                    conn,
                    target_id=str(job["target_id"]),
                    input_hash=str(job["input_hash"]),
                    raw_response=raw,
                    allowed_item_ids={str(i) for i in job["item_ids"]},
                    model=review_model,
                    prompt_version=str(job["prompt_version"]),
                )
            except Exception as exc:  # Qwen must not lose a frozen nightly attempt.
                review_errors.append(exc.__class__.__name__)
                continue
            if result.get("valid"):
                review_valid += 1
                if result.get("decision") in ("same_story", "different_story"):
                    label_engine_target(
                        conn,
                        target_kind="story_pair",
                        target_id=str(job["target_id"]),
                        release_id=provisional_stories.story_release_id,
                        label=str(result["decision"]),  # type: ignore[arg-type]
                        note="qwen_review",
                    )
            else:
                review_invalid += 1
    # Обучение не должно валить ночной цикл: вырожденный набор меток (один класс) —
    # пропускаем, модель предыдущего цикла продолжит жить в metrics.
    try:
        trained = train_story_merge_model(conn, provisional_stories.story_release_id)
        label_source = str(trained.get("label_source", "auto"))
    except ValueError:
        trained = {}
        label_source = "skipped"
    # A review is an input to clustering, not a decorative side report.  Build
    # a second immutable attempt after valid Qwen answers so cached decisions
    # and the calibrated merge model affect the *same* nightly publication.
    stories = provisional_stories
    if review_valid:
        reviewed_story_params = {
            **story_params,
            "reviewed_from_story_release": provisional_stories.story_release_id,
        }
        trained_model = trained.get("model")
        if isinstance(trained_model, dict):
            reviewed_story_params["merge_model"] = trained_model
        stories = create_story_release(
            conn,
            facet_release_id=facets.facet_release_id,
            params=reviewed_story_params,
        )
        auto_label_story_pairs(conn, stories.story_release_id)
    trends = create_trend_release(
        conn, story_release_id=stories.story_release_id, method=use_trend_method
    )
    trend_review_attempted = 0
    trend_review_valid = 0
    trend_review_invalid = 0
    trend_review_errors: list[str] = []
    if trend_review_limit > 0 and review_runner is not None:
        trend_jobs = prepare_trend_review_jobs(
            conn,
            trends.trend_release_id,
            limit=trend_review_limit,
            model=trend_review_model,
        )
        for job in trend_jobs:
            trend_review_attempted += 1
            try:
                raw = await review_runner(str(job["prompt"]), trend_review_model)
                result = store_trend_review_response(
                    conn,
                    target_id=str(job["target_id"]),
                    input_hash=str(job["input_hash"]),
                    raw_response=raw,
                    allowed_story_ids={str(story_id) for story_id in job["story_ids"]},
                    model=trend_review_model,
                    prompt_version=str(job["prompt_version"]),
                )
            except Exception as exc:  # Keep the deterministic candidate release inspectable.
                trend_review_errors.append(exc.__class__.__name__)
                continue
            if result.get("valid"):
                trend_review_valid += 1
            else:
                trend_review_invalid += 1
        # Re-materialize so cached decisions become confirmed/rejected status
        # on the release that is inspected and potentially published.
        if trend_review_valid:
            trends = create_trend_release(
                conn,
                story_release_id=stories.story_release_id,
                method=use_trend_method,
            )
    pulse_result: dict[str, Any] | None = None
    if pulse:
        date_row = conn.execute(
            """SELECT snapshot_date FROM release_items
               WHERE release_id = ? AND provider = 'reddit'
               ORDER BY snapshot_date DESC LIMIT 1""",
            (data.release_id,),
        ).fetchone()
        if date_row is not None:
            pulse_date = str(date_row["snapshot_date"])
            pulse_result = create_signal_release(
                conn,
                items=_load_pulse_items(conn, data.release_id, pulse_date),
                history_titles=_load_pulse_history_titles(
                    conn, data.release_id, profile, pulse_date, history_window_days
                ),
                pack_by_subreddit=pack_by_subreddit or {},
                story_release_id=stories.story_release_id,
                data_release_id=data.release_id,
                profile=profile,
                date=pulse_date,
                history_window_days=history_window_days,
            )
    story_evaluation = evaluate_story_release(conn, stories.story_release_id)
    trend_evaluation = evaluate_trend_release(conn, trends.trend_release_id)
    from .quality import compute_quality, evaluate_floors

    quality_metrics = compute_quality(
        conn,
        data_release_id=data.release_id,
        story_release_id=stories.story_release_id,
        trend_release_id=trends.trend_release_id,
        signal_release_id=(pulse_result or {}).get("signal_release_id") or None,
    )
    quality_floors = evaluate_floors(quality_metrics)
    quality_report = store_quality_report(
        conn,
        data_release_id=data.release_id,
        story_release_id=stories.story_release_id,
        trend_release_id=trends.trend_release_id,
        signal_release_id=(pulse_result or {}).get("signal_release_id") or None,
        metrics=quality_metrics,
        floors=quality_floors,
    )
    quality_passed = bool(quality_report["passed"])
    publication_id = ""
    publication_blocked_reason = ""
    if publish_channel:
        # A partial corpus is useful for an explicitly opted-in shadow/preview
        # publication, but must never reach a production channel.  Keep this
        # decision aligned with ``publish_radar`` rather than pre-emptively
        # blocking every partial run here.
        partial_not_allowed = data.input_status != "complete" and (
            publish_channel in {"broad", "ai-native"} or not allow_partial
        )
        if partial_not_allowed:
            publication_blocked_reason = "input_partial"
        elif not quality_passed:
            publication_blocked_reason = "quality_floors_failed"
        else:
            publication = publish_radar(
                conn,
                story_release_id=stories.story_release_id,
                trend_release_id=trends.trend_release_id,
                channel=publish_channel,
                allow_partial=allow_partial,
            )
            publication_id = publication.publication_id
    return {
        "data_release_id": data.release_id,
        "facet_release_id": facets.facet_release_id,
        "story_release_id": stories.story_release_id,
        "trend_release_id": trends.trend_release_id,
        "trend_method": use_trend_method,
        "embed_model": embed_model if embed_ok else "",
        "auto_labels": auto.get("added", 0),
        "provisional_story_release_id": provisional_stories.story_release_id,
        "reviewed_pairs": review_attempted,
        "valid_reviewed_pairs": review_valid,
        "invalid_reviewed_pairs": review_invalid,
        "review_errors": review_errors,
        "reviewed_story_rebuilt": bool(review_valid),
        "reviewed_trends": trend_review_attempted,
        "valid_reviewed_trends": trend_review_valid,
        "invalid_reviewed_trends": trend_review_invalid,
        "trend_review_errors": trend_review_errors,
        "label_source": label_source,
        "signal_release_id": (pulse_result or {}).get("signal_release_id", ""),
        "perspective_gap_available": bool(
            (pulse_result or {}).get("metrics", {}).get("perspective_gap_available")
        ),
        "publication_id": publication_id,
        "publication_blocked_reason": publication_blocked_reason,
        "quality": {
            **quality_report,
            "story_evaluation": story_evaluation,
            "trend_evaluation": trend_evaluation,
        },
    }


def export_golden_candidates(
    conn: sqlite3.Connection,
    story_release_id: str,
    *,
    pair_limit: int = 120,
    group_limit: int = 30,
) -> dict[str, Any]:
    """Create a stratified, editable review payload from frozen real data."""
    release = get_story_release(conn, story_release_id)
    if release is None:
        raise ValueError(f"Story release not found: {story_release_id}")
    facet_release = get_facet_release(conn, release.facet_release_id)
    if facet_release is None:
        raise ValueError(f"Facet release not found: {release.facet_release_id}")
    facets = _load_item_facets(conn, facet_release.facet_release_id)
    items = {item.item_id: item for item in load_frozen_items(conn, facet_release.data_release_id)}
    rows = conn.execute(
        """
        SELECT *
        FROM story_candidate_pairs
        WHERE story_release_id = ?
        ORDER BY
          CASE decision WHEN 'review' THEN 0 WHEN 'auto_merge' THEN 1 ELSE 2 END,
          score DESC, item_id_a, item_id_b
        """,
        (story_release_id,),
    ).fetchall()
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item_id_a = str(row["item_id_a"])
        item_id_b = str(row["item_id_b"])
        if item_id_a not in items or item_id_b not in items:
            continue
        domains = sorted(
            set(_json_list(facets.get(item_id_a, {}).get("domain_ids"), ["other"]))
            | set(_json_list(facets.get(item_id_b, {}).get("domain_ids"), ["other"]))
        )
        primary_domain = domains[0] if domains else "other"
        by_domain[primary_domain].append(
            {
                "pair_id": "|".join(_pair_key(item_id_a, item_id_b)),
                "item_id_a": item_id_a,
                "item_id_b": item_id_b,
                "title_a": items[item_id_a].title,
                "title_b": items[item_id_b].title,
                "provider_a": items[item_id_a].provider,
                "provider_b": items[item_id_b].provider,
                "domains": domains,
                "score": float(row["score"]),
                "engine_decision": str(row["decision"]),
                "features": _json_dict(row["features_json"]),
                "label": "",
                "note": "",
            }
        )
    pairs = _round_robin_strata(by_domain, pair_limit)

    story_rows = conn.execute(
        """
        SELECT *
        FROM engine_stories
        WHERE story_release_id = ?
        ORDER BY item_count DESC, source_count DESC, story_id
        LIMIT ?
        """,
        (story_release_id, max(0, group_limit)),
    ).fetchall()
    groups = []
    for story in story_rows:
        story_id = str(story["story_id"])
        item_ids = [
            str(row["item_id"])
            for row in conn.execute(
                """
                SELECT item_id FROM engine_story_items
                WHERE story_release_id = ? AND story_id = ?
                ORDER BY item_id
                """,
                (story_release_id, story_id),
            ).fetchall()
        ]
        groups.append(
            {
                "story_id": story_id,
                "title": str(story["title"]),
                "domains": _json_list(story["domain_ids"], ["other"]),
                "items": [
                    {
                        "item_id": item_id,
                        "provider": items[item_id].provider,
                        "title": items[item_id].title,
                        "url": items[item_id].target_url
                        or items[item_id].canonical_url
                        or items[item_id].discussion_url,
                    }
                    for item_id in item_ids
                    if item_id in items
                ],
                "label": "",
                "note": "",
            }
        )
    return {
        "schema_version": 1,
        "story_release_id": story_release_id,
        "data_release_id": facet_release.data_release_id,
        "instructions": {
            "pair_labels": ["same_story", "different_story", "low_signal"],
            "group_labels": ["overmerge", "undermerge", "low_signal"],
            "blank_label": "not reviewed",
        },
        "pairs": pairs,
        "groups": groups,
    }


def import_golden_labels(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
) -> dict[str, int]:
    story_release_id = str(payload.get("story_release_id") or "")
    if get_story_release(conn, story_release_id) is None:
        raise ValueError(f"Story release not found: {story_release_id}")
    pair_labels = {"same_story", "different_story", "low_signal"}
    group_labels = {"overmerge", "undermerge", "low_signal"}
    imported_pairs = 0
    imported_groups = 0
    for pair in payload.get("pairs", []):
        if not isinstance(pair, dict):
            continue
        label = str(pair.get("label") or "")
        if not label:
            continue
        if label not in pair_labels:
            raise ValueError(f"Invalid pair label: {label}")
        item_id_a = str(pair.get("item_id_a") or "")
        item_id_b = str(pair.get("item_id_b") or "")
        if not item_id_a or not item_id_b:
            raise ValueError("Golden pair requires item_id_a and item_id_b")
        label_engine_target(
            conn,
            target_kind="story_pair",
            target_id="|".join(_pair_key(item_id_a, item_id_b)),
            release_id=story_release_id,
            label=label,  # type: ignore[arg-type]
            note=str(pair.get("note") or ""),
        )
        imported_pairs += 1
    for group in payload.get("groups", []):
        if not isinstance(group, dict):
            continue
        label = str(group.get("label") or "")
        if not label:
            continue
        if label not in group_labels:
            raise ValueError(f"Invalid group label: {label}")
        story_id = str(group.get("story_id") or "")
        if not story_id:
            raise ValueError("Golden group requires story_id")
        label_engine_target(
            conn,
            target_kind="story",
            target_id=story_id,
            release_id=story_release_id,
            label=label,  # type: ignore[arg-type]
            note=str(group.get("note") or ""),
        )
        imported_groups += 1
    return {"pair_labels": imported_pairs, "group_labels": imported_groups}


def _round_robin_strata(
    strata: dict[str, list[dict[str, Any]]],
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    offsets = {key: 0 for key in strata}
    keys = sorted(strata)
    while len(selected) < max(0, limit):
        progressed = False
        for key in keys:
            offset = offsets[key]
            if offset >= len(strata[key]):
                continue
            selected.append(strata[key][offset])
            offsets[key] += 1
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


def inspect_story_release(
    conn: sqlite3.Connection,
    story_release_id: str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    release = get_story_release(conn, story_release_id)
    if release is None:
        raise ValueError(f"Story release not found: {story_release_id}")
    facet_release = get_facet_release(conn, release.facet_release_id)
    if facet_release is None:
        raise ValueError("Facet release not found")
    item_rows = {
        str(row["item_id"]): row
        for row in conn.execute(
            "SELECT * FROM release_items WHERE release_id = ?",
            (facet_release.data_release_id,),
        ).fetchall()
    }
    story_rows = conn.execute(
        """SELECT * FROM engine_stories
           WHERE story_release_id = ?
           ORDER BY item_count DESC, source_count DESC, story_id
           LIMIT ?""",
        (story_release_id, limit),
    ).fetchall()
    stories = []
    for story in story_rows:
        memberships = conn.execute(
            """SELECT * FROM engine_story_items
               WHERE story_release_id = ? AND story_id = ?
               ORDER BY membership_score DESC, item_id""",
            (story_release_id, story["story_id"]),
        ).fetchall()
        stories.append(
            {
                "story": dict(story),
                "items": [
                    {
                        "item_id": membership["item_id"],
                        "provider": item_rows[membership["item_id"]]["provider"],
                        "source_cluster": item_rows[membership["item_id"]]["source_cluster"],
                        "title": item_rows[membership["item_id"]]["title"],
                        "canonical_url": item_rows[membership["item_id"]]["canonical_url"],
                        "target_url": item_rows[membership["item_id"]]["target_url"],
                        "content_scope": item_rows[membership["item_id"]]["content_scope"],
                        "membership_score": membership["membership_score"],
                        "reason": membership["membership_reason"],
                    }
                    for membership in memberships
                ],
            }
        )
    review_pairs = [
        dict(row)
        for row in conn.execute(
            """SELECT * FROM story_candidate_pairs
               WHERE story_release_id = ? AND decision = 'review'
               ORDER BY score DESC LIMIT ?""",
            (story_release_id, limit),
        ).fetchall()
    ]
    return {
        "release": asdict(release),
        "stories": stories,
        "review_pairs": review_pairs,
    }


def inspect_trend_release(
    conn: sqlite3.Connection,
    trend_release_id: str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    release = get_trend_release(conn, trend_release_id)
    if release is None:
        raise ValueError(f"Trend release not found: {trend_release_id}")
    trends = []
    rows = conn.execute(
        """SELECT * FROM engine_trends
           WHERE trend_release_id = ?
           ORDER BY confidence DESC, story_count DESC LIMIT ?""",
        (trend_release_id, limit),
    ).fetchall()
    for row in rows:
        story_rows = conn.execute(
            """SELECT s.*, ts.membership_score, ts.reason
               FROM engine_trend_stories ts
               JOIN engine_stories s
                 ON s.story_release_id = ?
                AND s.story_id = ts.story_id
               WHERE ts.trend_release_id = ? AND ts.trend_id = ?
               ORDER BY ts.membership_score DESC, s.story_id""",
            (release.story_release_id, trend_release_id, row["trend_id"]),
        ).fetchall()
        trends.append({"trend": dict(row), "stories": [dict(story) for story in story_rows]})
    return {"release": asdict(release), "trends": trends}


def evaluate_story_release(conn: sqlite3.Connection, story_release_id: str) -> dict[str, Any]:
    release = get_story_release(conn, story_release_id)
    if release is None:
        raise ValueError(f"Story release not found: {story_release_id}")
    label_rows = conn.execute(
        """SELECT target_id, label FROM engine_labels
           WHERE target_kind = 'story_pair' AND release_id = ?""",
        (story_release_id,),
    ).fetchall()
    labels = {str(row["target_id"]): str(row["label"]) for row in label_rows}
    pair_rows = conn.execute(
        "SELECT * FROM story_candidate_pairs WHERE story_release_id = ?",
        (story_release_id,),
    ).fetchall()
    predictions = {
        "|".join(_pair_key(str(row["item_id_a"]), str(row["item_id_b"]))): str(row["decision"])
        == "auto_merge"
        for row in pair_rows
    }
    facet_release = get_facet_release(conn, release.facet_release_id)
    providers: dict[str, str] = {}
    if facet_release is not None:
        providers = {
            str(row["item_id"]): str(row["provider"])
            for row in conn.execute(
                "SELECT item_id, provider FROM release_items WHERE release_id = ?",
                (facet_release.data_release_id,),
            ).fetchall()
        }
    tp = fp = fn = tn = 0
    cross_source_expected = 0
    cross_source_found = 0
    evaluated_labels = 0
    for target_id, label in labels.items():
        if label == "low_signal":
            continue
        evaluated_labels += 1
        expected = label == "same_story"
        predicted = predictions.get(target_id, False)
        if expected and predicted:
            tp += 1
        elif not expected and predicted:
            fp += 1
        elif expected:
            fn += 1
        else:
            tn += 1
        item_ids = target_id.split("|", 1)
        if (
            expected
            and len(item_ids) == 2
            and providers.get(item_ids[0])
            and providers.get(item_ids[0]) != providers.get(item_ids[1])
        ):
            cross_source_expected += 1
            if predicted:
                cross_source_found += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    cross_source_recall = (
        cross_source_found / cross_source_expected if cross_source_expected else 0.0
    )
    group_label_rows = conn.execute(
        """SELECT target_id, label FROM engine_labels
           WHERE target_kind = 'story' AND release_id = ?""",
        (story_release_id,),
    ).fetchall()
    group_labels = {str(row["target_id"]): str(row["label"]) for row in group_label_rows}
    overmerge_count = sum(1 for label in group_labels.values() if label == "overmerge")
    overmerge_rate = overmerge_count / len(group_labels) if group_labels else 0.0
    llm_review_count = sum(
        1 for row in pair_rows if "llm_review" in _json_dict(row["features_json"])
    )
    qwen_ratio = llm_review_count / len(pair_rows) if pair_rows else 0.0
    membership_rows = conn.execute(
        """
        SELECT membership_reason
        FROM engine_story_items
        WHERE story_release_id = ?
        """,
        (story_release_id,),
    ).fetchall()
    evidence_coverage = (
        sum(1 for row in membership_rows if str(row["membership_reason"] or "").strip())
        / len(membership_rows)
        if membership_rows
        else 0.0
    )
    result = {
        **release.metrics,
        "labeled_pairs": evaluated_labels,
        "labeled_groups": len(group_labels),
        "pair_precision": round(precision, 4),
        "pair_recall": round(recall, 4),
        "cross_source_story_recall": round(cross_source_recall, 4),
        "overmerge_rate": round(overmerge_rate, 4),
        "evidence_coverage": round(evidence_coverage, 4),
        "qwen_pair_ratio": round(qwen_ratio, 4),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "publication_gate": bool(
            evaluated_labels >= 120
            and len(group_labels) >= 30
            and precision >= 0.95
            and recall >= 0.75
            and overmerge_rate <= 0.03
            and cross_source_recall >= 0.75
            and evidence_coverage == 1.0
            and qwen_ratio <= 0.15
        ),
    }
    conn.execute(
        "UPDATE story_releases SET metrics_json = ? WHERE story_release_id = ?",
        (_json(result), story_release_id),
    )
    conn.commit()
    return result


def evaluate_trend_release(conn: sqlite3.Connection, trend_release_id: str) -> dict[str, Any]:
    release = get_trend_release(conn, trend_release_id)
    if release is None:
        raise ValueError(f"Trend release not found: {trend_release_id}")
    labels = conn.execute(
        """SELECT target_id, label FROM engine_labels
           WHERE target_kind = 'trend' AND release_id = ?""",
        (trend_release_id,),
    ).fetchall()
    useful = sum(1 for row in labels if str(row["label"]) == "useful_trend")
    usefulness = useful / len(labels) if labels else 0.0
    review_counts = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN review_status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed
        FROM engine_trends
        WHERE trend_release_id = ?
        """,
        (trend_release_id,),
    ).fetchone()
    total_trends = int(review_counts["total"] or 0)
    confirmed_trends = int(review_counts["confirmed"] or 0)
    qwen_review_coverage = confirmed_trends / total_trends if total_trends else 0.0
    result = {
        **release.metrics,
        "labeled_trends": len(labels),
        "manual_usefulness": round(usefulness, 4),
        "confirmed_trends": confirmed_trends,
        "qwen_review_coverage": round(qwen_review_coverage, 4),
        "publication_gate": bool(
            len(labels) >= 10
            and usefulness >= 0.75
            and total_trends > 0
            and qwen_review_coverage == 1.0
        ),
    }
    conn.execute(
        "UPDATE trend_releases SET metrics_json = ? WHERE trend_release_id = ?",
        (_json(result), trend_release_id),
    )
    conn.commit()
    return result


def _metric_delta(
    metrics: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, float | int]:
    deltas: dict[str, float | int] = {}
    for key, value in metrics.items():
        base_value = baseline.get(key)
        if isinstance(value, int) and isinstance(base_value, int):
            deltas[key] = value - base_value
        elif isinstance(value, float) and isinstance(base_value, int | float):
            deltas[key] = round(value - float(base_value), 4)
    return deltas


def _story_release_reason_counts(
    conn: sqlite3.Connection,
    story_release_id: str,
) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT membership_reason, COUNT(*) AS count
        FROM engine_story_items
        WHERE story_release_id = ?
          AND membership_reason <> 'story medoid'
        GROUP BY membership_reason
        ORDER BY count DESC, membership_reason
        """,
        (story_release_id,),
    ).fetchall()
    return {str(row["membership_reason"]): int(row["count"]) for row in rows}


def _story_release_cross_source_samples(
    conn: sqlite3.Connection,
    story_release_id: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    story_rows = conn.execute(
        """
        SELECT story_id, title, item_count, source_count
        FROM engine_stories
        WHERE story_release_id = ? AND source_count >= 2
        ORDER BY item_count DESC, source_count DESC, title
        LIMIT ?
        """,
        (story_release_id, max(0, limit)),
    ).fetchall()
    samples: list[dict[str, Any]] = []
    for story_row in story_rows:
        item_rows = conn.execute(
            """
            SELECT ri.provider, ri.title, esi.membership_reason
            FROM engine_story_items AS esi
            JOIN story_releases AS sr
              ON sr.story_release_id = esi.story_release_id
            JOIN facet_releases AS fr
              ON fr.facet_release_id = sr.facet_release_id
            JOIN release_items AS ri
              ON ri.release_id = fr.data_release_id
             AND ri.item_id = esi.item_id
            WHERE esi.story_release_id = ? AND esi.story_id = ?
            ORDER BY ri.provider, ri.title
            LIMIT 8
            """,
            (story_release_id, str(story_row["story_id"])),
        ).fetchall()
        samples.append(
            {
                "story_id": str(story_row["story_id"]),
                "title": str(story_row["title"]),
                "item_count": int(story_row["item_count"]),
                "source_count": int(story_row["source_count"]),
                "items": [
                    {
                        "provider": str(item_row["provider"]),
                        "title": str(item_row["title"]),
                        "reason": str(item_row["membership_reason"]),
                    }
                    for item_row in item_rows
                ],
            }
        )
    return samples


def compare_engine_versions(
    conn: sqlite3.Connection,
    left_id: str,
    right_id: str,
) -> dict[str, Any]:
    left_story = get_story_release(conn, left_id)
    right_story = get_story_release(conn, right_id)
    if left_story and right_story:
        keys = sorted(set(left_story.metrics) | set(right_story.metrics))
        return {
            "kind": "story",
            "left": asdict(left_story),
            "right": asdict(right_story),
            "delta": {
                key: _numeric_delta(left_story.metrics.get(key), right_story.metrics.get(key))
                for key in keys
            },
        }
    left_trend = get_trend_release(conn, left_id)
    right_trend = get_trend_release(conn, right_id)
    if left_trend and right_trend:
        keys = sorted(set(left_trend.metrics) | set(right_trend.metrics))
        return {
            "kind": "trend",
            "left": asdict(left_trend),
            "right": asdict(right_trend),
            "delta": {
                key: _numeric_delta(left_trend.metrics.get(key), right_trend.metrics.get(key))
                for key in keys
            },
        }
    raise ValueError("Both IDs must be releases of the same kind")


def _load_item_facets(conn: sqlite3.Connection, facet_release_id: str) -> dict[str, dict[str, Any]]:
    return {
        str(row["item_id"]): dict(row)
        for row in conn.execute(
            "SELECT * FROM item_facets WHERE facet_release_id = ?",
            (facet_release_id,),
        ).fetchall()
    }


def _load_engine_stories(conn: sqlite3.Connection, story_release_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM engine_stories WHERE story_release_id = ? ORDER BY story_id",
            (story_release_id,),
        ).fetchall()
    ]


def _load_story_item_ids(conn: sqlite3.Connection, story_release_id: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for row in conn.execute(
        """SELECT story_id, item_id FROM engine_story_items
           WHERE story_release_id = ? ORDER BY story_id, item_id""",
        (story_release_id,),
    ).fetchall():
        result[str(row["story_id"])].append(str(row["item_id"]))
    return dict(result)


def _add_index_pairs(
    pair_reasons: dict[tuple[str, str], set[str]],
    item_ids: Iterable[str],
    reason: str,
    *,
    max_candidate_pairs: int | None = None,
) -> bool:
    """Add a bounded index bucket; return whether the global budget was reached."""
    unique_ids = sorted(set(item_ids))
    for index, left in enumerate(unique_ids):
        for right in unique_ids[index + 1 :]:
            key = (left, right)
            if (
                max_candidate_pairs is not None
                and key not in pair_reasons
                and len(pair_reasons) >= max_candidate_pairs
            ):
                return True
            pair_reasons[key].add(reason)
    return max_candidate_pairs is not None and len(pair_reasons) >= max_candidate_pairs


def _near_duplicate_bucket_keys(normalized_title: str) -> list[str]:
    tokens = extract_ordered_tokens(normalized_title)
    if len(tokens) < 3:
        return []
    shingles = _title_shingles(tokens)
    fingerprint = _simhash(shingles | set(tokens))
    keys = [f"nd:sim:{band}:{(fingerprint >> (band * 16)) & 0xFFFF}" for band in range(4)]
    minhashes = sorted(_stable_u64(shingle) for shingle in shingles)[:4]
    keys.extend(f"nd:min:{value & 0xFFFFFFFF:08x}" for value in minhashes)
    return keys


def _near_duplicate_similarity_features(left_norm: str, right_norm: str) -> dict[str, Any]:
    left_tokens = extract_ordered_tokens(left_norm)
    right_tokens = extract_ordered_tokens(right_norm)
    left_shingles = _title_shingles(left_tokens)
    right_shingles = _title_shingles(right_tokens)
    shingle_jaccard = len(left_shingles & right_shingles) / max(
        len(left_shingles | right_shingles),
        1,
    )
    left_fingerprint = _simhash(left_shingles | set(left_tokens))
    right_fingerprint = _simhash(right_shingles | set(right_tokens))
    return {
        "near_duplicate_shingle_jaccard": round(shingle_jaccard, 4),
        "near_duplicate_simhash_distance": (left_fingerprint ^ right_fingerprint).bit_count(),
    }


def _title_shingles(tokens: list[str]) -> set[str]:
    if len(tokens) < 3:
        return set(tokens)
    return {" ".join(tokens[index : index + 3]) for index in range(len(tokens) - 2)}


def _simhash(features: set[str]) -> int:
    if not features:
        return 0
    counters = [0] * 64
    for feature in features:
        value = _stable_u64(feature)
        for bit in range(64):
            counters[bit] += 1 if value & (1 << bit) else -1
    fingerprint = 0
    for bit, score in enumerate(counters):
        if score >= 0:
            fingerprint |= 1 << bit
    return fingerprint


def _stable_u64(value: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(),
        "big",
    )


def _item_urls(item: FrozenItem) -> set[str]:
    return {
        url.rstrip("/")
        for url in (item.canonical_url, item.target_url)
        if url and url.startswith(("http://", "https://")) and "reddit.com/" not in url
    }


def _is_stable_landing_url(url: str) -> bool:
    """Return true for URLs that identify a long-lived object, not one event."""
    parsed = urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return True
    hostname = (parsed.hostname or "").lower()
    if hostname in {"github.com", "gitlab.com", "bitbucket.org"}:
        return len(parts) <= 2
    if hostname in {"huggingface.co", "www.huggingface.co"}:
        return len(parts) <= 2 or (len(parts) <= 3 and parts[0] in {"datasets", "spaces"})
    return False


def _is_huggingface_model_url(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    return hostname in {"huggingface.co", "www.huggingface.co"} and len(parts) == 2


def _item_date(item: FrozenItem) -> str:
    raw = item.published_at.strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            try:
                return parsedate_to_datetime(raw).date().isoformat()
            except (TypeError, ValueError, OverflowError):
                pass
    return item.snapshot_date[:10]


def _facet_entities(facet: dict[str, Any]) -> set[str]:
    return set(_json_list(facet.get("entities"))) - _GENERIC_ENTITIES


def _event_numbers(facet: dict[str, Any]) -> list[str]:
    frame = _json_dict(facet.get("event_frame_json"))
    return _json_list(frame.get("numbers"))


def _date_distance_days(left: FrozenItem, right: FrozenItem) -> int:
    left_date = _item_date(left)
    right_date = _item_date(right)
    try:
        return abs(
            (
                datetime.fromisoformat(left_date).date() - datetime.fromisoformat(right_date).date()
            ).days
        )
    except ValueError:
        return 0


_TREND_TOPIC_STOP = {
    "after",
    "amid",
    "are",
    "been",
    "being",
    "can",
    "does",
    "from",
    "have",
    "how",
    "just",
    "like",
    "into",
    "more",
    "new",
    "says",
    "the",
    "this",
    "was",
    "were",
    "what",
    "when",
    "who",
    "why",
    "will",
    "with",
    "would",
    "year",
    "years",
}
_GENERIC_TREND_PATTERNS = {
    "ai agent",
    "artificial intelligence",
    "machine learning",
    "open source",
    "president trump",
    "social media",
    "united states",
    "white house",
}
_GENERIC_TREND_THEMES = {
    "ai",
    "business",
    "culture",
    "geopolitics",
    "markets",
    "politics",
    "technology",
}


def _story_topic_keys(story: dict[str, Any]) -> list[str]:
    title = str(story.get("title") or "")
    if is_low_signal_title(title):
        return []
    tokens = [
        _normalize_trend_token(token)
        for token in normalize_title(title).split()
        if token not in _TREND_TOPIC_STOP and not token.isdigit()
    ]
    phrases: list[str] = []
    for index in range(min(len(tokens) - 1, 4)):
        if tokens[index] == tokens[index + 1]:
            continue
        phrase = " ".join((tokens[index], tokens[index + 1]))
        if phrase in _GENERIC_TREND_PATTERNS:
            continue
        phrases.append(phrase)
    return phrases


def _is_generic_trend_pattern(feature: str) -> bool:
    kind, value = feature.split(":", 1)
    return (kind == "topic" and value in _GENERIC_TREND_PATTERNS) or (
        kind == "theme" and value in _GENERIC_TREND_THEMES
    )


def _normalize_trend_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 5:
        return f"{token[:-3]}y"
    if token.endswith("s") and len(token) > 4 and not token.endswith(("ss", "us", "is", "news")):
        return token[:-1]
    return token


def _trend_display_name(key: str) -> str:
    kind, value = key.split(":", 1)
    if kind == "pain":
        return f"Боль: {value}"
    if kind == "theme":
        return value
    return f"Паттерн: {value}"


def _trend_lifecycle(
    first_seen: str,
    last_seen: str,
    active_days: int,
    history_status: str,
) -> str:
    if history_status != "ready":
        return "insufficient_history"
    try:
        age = (datetime.now(UTC).date() - datetime.fromisoformat(first_seen).date()).days
    except ValueError:
        age = 0
    if age <= 2:
        return "new"
    if active_days >= 5 and first_seen != last_seen:
        return "growing"
    return "stable"


def _json_int_dict(raw: Any) -> dict[str, int]:
    return {
        key: int(value or 0)
        for key, value in _json_dict(raw).items()
        if isinstance(value, int | float)
    }


def _numeric_delta(left: Any, right: Any) -> float | None:
    if isinstance(left, int | float) and isinstance(right, int | float):
        return round(float(right) - float(left), 4)
    return None
