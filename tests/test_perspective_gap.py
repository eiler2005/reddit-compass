"""Tests for perspective gap computation."""

from __future__ import annotations

import sqlite3

from reddit_compass.intelligence.perspective_gap import compute_perspective_gaps


def _setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS engine_stories (
            story_release_id TEXT NOT NULL,
            story_id TEXT NOT NULL,
            canonical_key TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            summary_ru TEXT NOT NULL DEFAULT '',
            domain_ids TEXT NOT NULL DEFAULT '["other"]',
            theme_ids TEXT NOT NULL DEFAULT '[]',
            project_scores TEXT NOT NULL DEFAULT '{}',
            first_seen TEXT NOT NULL DEFAULT '',
            last_seen TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'low',
            source_count INTEGER NOT NULL DEFAULT 0,
            item_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (story_release_id, story_id)
        );
        CREATE TABLE IF NOT EXISTS engine_story_items (
            story_release_id TEXT NOT NULL,
            story_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            membership_score REAL NOT NULL DEFAULT 1.0,
            membership_reason TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (story_release_id, story_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS community_signals (
            signal_release_id TEXT NOT NULL,
            signal_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            subreddit TEXT NOT NULL,
            pack_id TEXT NOT NULL DEFAULT '',
            signal_type TEXT NOT NULL DEFAULT 'other',
            title TEXT NOT NULL DEFAULT '',
            discussion_url TEXT NOT NULL DEFAULT '',
            target_url TEXT NOT NULL DEFAULT '',
            pulse_score REAL NOT NULL DEFAULT 0,
            subreddit_percentile REAL NOT NULL DEFAULT 0,
            score_velocity REAL NOT NULL DEFAULT 0,
            comment_velocity REAL NOT NULL DEFAULT 0,
            discussion_depth REAL NOT NULL DEFAULT 0,
            comment_score_ratio REAL NOT NULL DEFAULT 0,
            cross_subreddit_repetition REAL NOT NULL DEFAULT 0,
            novelty REAL NOT NULL DEFAULT 0,
            domain_ids_json TEXT NOT NULL DEFAULT '[]',
            theme_ids_json TEXT NOT NULL DEFAULT '[]',
            pain_points_json TEXT NOT NULL DEFAULT '[]',
            project_scores_json TEXT NOT NULL DEFAULT '{}',
            linked_story_id TEXT,
            mainstream_coverage_count INTEGER NOT NULL DEFAULT 0,
            perspective_gap REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (signal_release_id, signal_id)
        );
    """)


def _insert_signal(
    conn: sqlite3.Connection,
    signal_release_id: str,
    item_id: str,
    title: str,
    subreddit: str,
    pulse_score: float,
    mainstream_coverage: int = 0,
) -> None:
    conn.execute(
        """INSERT INTO community_signals
           (signal_release_id, signal_id, item_id, subreddit,
            signal_type, title, pulse_score, mainstream_coverage_count)
           VALUES (?, ?, ?, ?, 'discussion', ?, ?, ?)""",
        (
            signal_release_id,
            f"pulse_{item_id}",
            item_id,
            subreddit,
            title,
            pulse_score,
            mainstream_coverage,
        ),
    )


def _insert_story(
    conn: sqlite3.Connection,
    story_release_id: str,
    story_id: str,
    title: str,
    source_count: int,
) -> None:
    conn.execute(
        """INSERT INTO engine_stories
           (story_release_id, story_id, canonical_key, title,
            source_count, item_count, confidence)
           VALUES (?, ?, ?, ?, ?, 1, 'medium')""",
        (story_release_id, story_id, story_id, title, source_count),
    )


def _link_story_item(
    conn: sqlite3.Connection,
    story_release_id: str,
    story_id: str,
    item_id: str,
) -> None:
    conn.execute(
        """INSERT INTO engine_story_items
           (story_release_id, story_id, item_id, membership_score,
            membership_reason)
           VALUES (?, ?, ?, 1.0, 'story medoid')""",
        (story_release_id, story_id, item_id),
    )


class TestPerspectiveGap:
    def test_mainstream_gap_high_pulse_low_coverage(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_db(conn)
        _insert_signal(conn, "sig1", "i1", "Viral Reddit topic", "technology", 80.0, 0)
        _insert_signal(conn, "sig1", "i2", "Covered everywhere", "news", 70.0, 5)
        conn.commit()

        gaps = compute_perspective_gaps(conn, "sig1", "sr1", pulse_threshold=60.0)
        mainstream_gaps = [g for g in gaps if g.gap_type == "mainstream_gap"]
        assert len(mainstream_gaps) == 1
        assert mainstream_gaps[0].item_id == "i1"
        assert mainstream_gaps[0].mainstream_coverage_count == 0

    def test_no_mainstream_gap_when_covered(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_db(conn)
        _insert_signal(conn, "sig1", "i1", "Well covered", "news", 80.0, 3)
        conn.commit()

        gaps = compute_perspective_gaps(
            conn, "sig1", "sr1", pulse_threshold=60.0, mainstream_threshold=2
        )
        mainstream_gaps = [g for g in gaps if g.gap_type == "mainstream_gap"]
        assert len(mainstream_gaps) == 0

    def test_elite_media_gap_high_sources_no_pulse(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_db(conn)
        _insert_story(conn, "sr1", "s1", "Media-only story", 5)
        _link_story_item(conn, "sr1", "s1", "i1")
        # No pulse signal for this item
        conn.commit()

        gaps = compute_perspective_gaps(conn, "sig1", "sr1", pulse_threshold=60.0)
        elite_gaps = [g for g in gaps if g.gap_type == "elite_media_gap"]
        assert len(elite_gaps) == 1
        assert elite_gaps[0].item_id == "s1"

    def test_no_elite_gap_when_reddit_discusses(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_db(conn)
        _insert_story(conn, "sr1", "s1", "Both covered", 5)
        _link_story_item(conn, "sr1", "s1", "i1")
        _insert_signal(conn, "sig1", "i1", "Both covered", "news", 75.0, 3)
        conn.commit()

        gaps = compute_perspective_gaps(conn, "sig1", "sr1", pulse_threshold=60.0)
        elite_gaps = [g for g in gaps if g.gap_type == "elite_media_gap"]
        assert len(elite_gaps) == 0

    def test_gap_sorted_by_score(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_db(conn)
        _insert_signal(conn, "sig1", "i1", "Gap A", "tech", 90.0, 0)
        _insert_signal(conn, "sig1", "i2", "Gap B", "tech", 70.0, 0)
        conn.commit()

        gaps = compute_perspective_gaps(conn, "sig1", "sr1", pulse_threshold=60.0)
        mainstream_gaps = [g for g in gaps if g.gap_type == "mainstream_gap"]
        assert len(mainstream_gaps) == 2
        assert mainstream_gaps[0].gap_score >= mainstream_gaps[1].gap_score

    def test_empty_when_no_signals(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_db(conn)
        conn.commit()

        gaps = compute_perspective_gaps(conn, "sig1", "sr1")
        assert len(gaps) == 0
