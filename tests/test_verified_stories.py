"""Tests for Verified Story layer and hard guards."""

from __future__ import annotations

import sqlite3

from reddit_compass.intelligence.verified_stories import (
    GENERIC_ANCHORS,
    check_group_size_guards,
    get_verified_stories,
    get_verified_story_ids,
    is_generic_anchor,
)


def _setup_engine_db(conn: sqlite3.Connection) -> None:
    """Create minimal engine schema for testing."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS data_releases (
            release_id TEXT PRIMARY KEY,
            profile TEXT NOT NULL DEFAULT 'test',
            status TEXT NOT NULL DEFAULT 'finalized',
            created_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS story_releases (
            story_release_id TEXT PRIMARY KEY,
            data_release_id TEXT NOT NULL,
            facet_release_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'finalized',
            created_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS release_items (
            release_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            source_cluster TEXT NOT NULL DEFAULT 'voices',
            external_id TEXT NOT NULL DEFAULT '',
            canonical_url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            summary_ru TEXT NOT NULL DEFAULT '',
            excerpt TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            observed_at TEXT NOT NULL DEFAULT '',
            snapshot_date TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT 'en',
            content_scope TEXT NOT NULL DEFAULT 'headline',
            source_section TEXT NOT NULL DEFAULT '',
            domain_ids TEXT NOT NULL DEFAULT '["other"]',
            discussion_url TEXT NOT NULL DEFAULT '',
            target_url TEXT NOT NULL DEFAULT '',
            dedupe_group_id TEXT NOT NULL DEFAULT '',
            evidence_refs TEXT NOT NULL DEFAULT '[]',
            raw_engagement TEXT NOT NULL DEFAULT '{}',
            metadata TEXT NOT NULL DEFAULT '{}',
            row_checksum TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (release_id, item_id)
        );
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
        CREATE TABLE IF NOT EXISTS engine_labels (
            label_id TEXT PRIMARY KEY,
            release_id TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            label TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
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


def _insert_story(
    conn: sqlite3.Connection,
    release_id: str,
    story_id: str,
    title: str,
    source_count: int = 1,
    item_count: int = 1,
) -> None:
    conn.execute(
        """INSERT INTO engine_stories
           (story_release_id, story_id, canonical_key, title,
            source_count, item_count, confidence)
           VALUES (?, ?, ?, ?, ?, ?, 'medium')""",
        (release_id, story_id, story_id, title, source_count, item_count),
    )


def _insert_item(
    conn: sqlite3.Connection,
    data_release_id: str,
    story_release_id: str,
    story_id: str,
    item_id: str,
    provider: str,
    reason: str = "story medoid",
) -> None:
    conn.execute(
        """INSERT INTO engine_story_items
           (story_release_id, story_id, item_id, membership_score,
            membership_reason)
           VALUES (?, ?, ?, 1.0, ?)""",
        (story_release_id, story_id, item_id, reason),
    )
    conn.execute(
        """INSERT OR IGNORE INTO release_items
           (release_id, item_id, provider, source_cluster, external_id,
            canonical_url, title, row_checksum)
           VALUES (?, ?, ?, 'voices', ?, '', ?, 'x')""",
        (data_release_id, item_id, provider, item_id, f"Title {item_id}"),
    )


class TestGenericAnchors:
    def test_generic_anchors_list(self):
        assert "ai" in GENERIC_ANCHORS
        assert "agent" in GENERIC_ANCHORS
        assert "open source" in GENERIC_ANCHORS
        assert "startup" in GENERIC_ANCHORS
        assert "llm" in GENERIC_ANCHORS

    def test_is_generic_anchor(self):
        assert is_generic_anchor("ai")
        assert is_generic_anchor("AI")
        assert is_generic_anchor("  agent  ")
        assert not is_generic_anchor("OpenAI")
        assert not is_generic_anchor("GPT-5")


class TestVerifiedStories:
    def test_cross_source_url_verified(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Cross-source story", source_count=2, item_count=2)
        _insert_item(conn, "dr1", "sr1", "s1", "i1", "rss", "story medoid")
        _insert_item(conn, "dr1", "sr1", "s1", "i2", "reddit", "shared canonical/target URL")
        conn.commit()

        verified = get_verified_stories(conn, "sr1")
        assert len(verified) == 1
        assert "cross_source_url" in verified[0].verification_reasons

    def test_semantic_only_not_verified(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Semantic only story", source_count=2, item_count=2)
        _insert_item(conn, "dr1", "sr1", "s1", "i1", "rss", "story medoid")
        _insert_item(conn, "dr1", "sr1", "s1", "i2", "reddit", "semantic embedding dedup")
        conn.commit()

        verified = get_verified_stories(conn, "sr1")
        assert len(verified) == 0

    def test_near_duplicate_verified(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Near-dup story", source_count=1, item_count=2)
        _insert_item(conn, "dr1", "sr1", "s1", "i1", "rss", "story medoid")
        _insert_item(conn, "dr1", "sr1", "s1", "i2", "rss", "near-duplicate title fingerprint")
        conn.commit()

        verified = get_verified_stories(conn, "sr1")
        assert len(verified) == 1
        assert "near_duplicate_title" in verified[0].verification_reasons

    def test_qwen_confirmed_verified(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Qwen story", source_count=2, item_count=2)
        _insert_item(conn, "dr1", "sr1", "s1", "i1", "rss", "story medoid")
        _insert_item(
            conn, "dr1", "sr1", "s1", "i2", "reddit", "validated by cached Qwen story review"
        )
        conn.commit()

        verified = get_verified_stories(conn, "sr1")
        assert len(verified) == 1
        assert "qwen_confirmed" in verified[0].verification_reasons

    def test_manual_label_verified(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Manual story", source_count=1, item_count=1)
        _insert_item(conn, "dr1", "sr1", "s1", "i1", "rss", "story medoid")
        conn.execute(
            """INSERT INTO engine_labels
               VALUES ('l1', 'sr1', 'story', 's1', 'same_story', '', '')"""
        )
        conn.commit()

        verified = get_verified_stories(conn, "sr1")
        assert len(verified) == 1
        assert "manual_label" in verified[0].verification_reasons

    def test_community_only_high_pulse_verified(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Reddit pulse story", source_count=1, item_count=1)
        _insert_item(conn, "dr1", "sr1", "s1", "i1", "reddit", "story medoid")
        conn.execute(
            """INSERT INTO community_signals
               (signal_release_id, signal_id, item_id, subreddit,
                signal_type, title, pulse_score)
               VALUES ('sig1', 'pulse_i1', 'i1', 'technology',
                       'discussion', 'Reddit pulse story', 75.0)"""
        )
        conn.commit()

        verified = get_verified_stories(conn, "sr1", signal_release_id="sig1")
        assert len(verified) == 1
        assert "community_only_high_pulse" in verified[0].verification_reasons

    def test_community_only_low_pulse_not_verified(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Low pulse story", source_count=1, item_count=1)
        _insert_item(conn, "dr1", "sr1", "s1", "i1", "reddit", "story medoid")
        conn.execute(
            """INSERT INTO community_signals
               (signal_release_id, signal_id, item_id, subreddit,
                signal_type, title, pulse_score)
               VALUES ('sig1', 'pulse_i1', 'i1', 'technology',
                       'discussion', 'Low pulse story', 30.0)"""
        )
        conn.commit()

        verified = get_verified_stories(conn, "sr1", signal_release_id="sig1")
        assert len(verified) == 0

    def test_get_verified_story_ids(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Verified", source_count=2, item_count=2)
        _insert_item(conn, "dr1", "sr1", "s1", "i1", "rss", "story medoid")
        _insert_item(conn, "dr1", "sr1", "s1", "i2", "reddit", "shared canonical/target URL")
        _insert_story(conn, "sr1", "s2", "Not verified", source_count=1, item_count=1)
        _insert_item(conn, "dr1", "sr1", "s2", "i3", "rss", "story medoid")
        conn.commit()

        ids = get_verified_story_ids(conn, "sr1")
        assert ids == {"s1"}


class TestGroupSizeGuards:
    def test_same_provider_over_limit_no_provenance(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Big same-provider", source_count=1, item_count=10)
        for i in range(10):
            _insert_item(conn, "dr1", "sr1", "s1", f"i{i}", "rss", "semantic embedding dedup")
        conn.commit()

        warnings = check_group_size_guards(conn, "sr1", same_provider_max=8)
        assert len(warnings) == 1
        assert "same-provider" in warnings[0].warning

    def test_same_provider_over_limit_with_provenance(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Big with provenance", source_count=1, item_count=10)
        _insert_item(conn, "dr1", "sr1", "s1", "i0", "rss", "story medoid")
        for i in range(1, 10):
            _insert_item(
                conn, "dr1", "sr1", "s1", f"i{i}", "rss", "near-duplicate title fingerprint"
            )
        conn.commit()

        warnings = check_group_size_guards(conn, "sr1", same_provider_max=8)
        assert len(warnings) == 0

    def test_cross_source_over_limit_no_review(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Big cross-source", source_count=3, item_count=20)
        _insert_item(conn, "dr1", "sr1", "s1", "i0", "rss", "story medoid")
        for i in range(1, 10):
            _insert_item(conn, "dr1", "sr1", "s1", f"i{i}", "rss", "semantic embedding dedup")
        for i in range(10, 20):
            _insert_item(conn, "dr1", "sr1", "s1", f"i{i}", "reddit", "semantic embedding dedup")
        conn.commit()

        warnings = check_group_size_guards(conn, "sr1", cross_source_max=15)
        assert len(warnings) == 1
        assert "cross-source" in warnings[0].warning

    def test_under_limits_no_warning(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _setup_engine_db(conn)
        conn.execute("INSERT INTO story_releases VALUES ('sr1', 'dr1', '', 'finalized', '')")
        conn.execute("INSERT INTO data_releases VALUES ('dr1', 'test', 'finalized', '')")
        _insert_story(conn, "sr1", "s1", "Small story", source_count=1, item_count=3)
        for i in range(3):
            _insert_item(conn, "dr1", "sr1", "s1", f"i{i}", "rss", "story medoid")
        conn.commit()

        warnings = check_group_size_guards(conn, "sr1")
        assert len(warnings) == 0
