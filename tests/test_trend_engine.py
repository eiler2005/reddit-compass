"""Offline contract tests for the separated Story/Trend Engine."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from reddit_compass.intelligence.engine import (
    FrozenItem,
    create_data_release,
    create_facet_release,
    create_story_release,
    create_trend_release,
    engine_db,
    evaluate_story_release,
    evaluate_trend_release,
    export_golden_candidates,
    generate_story_candidates,
    get_current_publication,
    import_golden_labels,
    import_legacy_lab,
    inspect_story_release,
    inspect_trend_release,
    label_engine_target,
    load_frozen_items,
    publish_radar,
    rollback_publication,
    store_story_review_response,
    verify_data_release,
)
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import ContentItem, Observation, SourceHealth
from reddit_compass.intelligence.repository import (
    save_source_health,
    upsert_items,
    upsert_observations,
    upsert_run,
)


def test_data_release_is_frozen_and_checksum_verified(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")

    release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )
    original_title = load_frozen_items(engine, release.release_id)[0].title
    corpus.execute("UPDATE items SET title = 'MUTATED' WHERE item_id = 'event1_reuters'")
    corpus.commit()

    assert load_frozen_items(engine, release.release_id)[0].title == original_title
    assert verify_data_release(engine, release.release_id)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        engine.execute(
            """UPDATE release_items SET title = 'forbidden'
               WHERE release_id = ? AND item_id = 'event1_reuters'""",
            (release.release_id,),
        )


def test_story_and_trend_releases_are_independent_versions(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")
    release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )
    facets = create_facet_release(
        engine,
        data_release_id=release.release_id,
        theme_catalog={"ai_security": ["security review", "security rules"]},
    )
    stories = create_story_release(engine, facet_release_id=facets.facet_release_id)
    trends = create_trend_release(
        engine,
        story_release_id=stories.story_release_id,
        params={"min_stories": 3, "min_dates": 2},
    )

    assert stories.metrics["item_count"] == 6
    assert stories.metrics["story_count"] == 3
    assert stories.metrics["cross_source_story_count"] == 3
    assert trends.metrics["story_count"] == 3
    assert trends.metrics["trend_count"] >= 1
    assert trends.metrics["history_release_count"] == 1
    assert trends.history_status == "insufficient_history"
    trend_metrics = evaluate_trend_release(engine, trends.trend_release_id)
    assert trend_metrics["qwen_review_coverage"] == 0.0
    assert trend_metrics["publication_gate"] is False
    story_inspection = inspect_story_release(engine, stories.story_release_id)
    trend_inspection = inspect_trend_release(engine, trends.trend_release_id)
    assert len(story_inspection["stories"]) == 3
    assert trend_inspection["trends"]


def test_stable_repository_url_does_not_force_different_events_to_merge() -> None:
    repository_url = "https://github.com/example/safe-ai"
    items = [
        _frozen_item(
            "hn:first",
            repository_url,
            "SafeAI project releases first week security report",
            "Mon, 20 Jul 2026 08:00:00 GMT",
        ),
        _frozen_item(
            "hn:second",
            repository_url,
            "SafeAI project releases second week benchmark report",
            "Sun, 26 Jul 2026 08:00:00 GMT",
        ),
    ]

    candidates = generate_story_candidates(items, {})

    assert all(candidate.decision != "auto_merge" for candidate in candidates)
    assert all(candidate.reason != "shared canonical/target URL" for candidate in candidates)
    if candidates:
        assert candidates[0].features["date_distance_days"] == 6
        assert candidates[0].features["stable_landing_url_match"] is True


def test_stable_repository_url_can_merge_matching_event_titles() -> None:
    repository_url = "https://github.com/example/safe-ai"
    title = "SafeAI publishes its first security audit report"
    items = [
        _frozen_item("hn:one", repository_url, title, "2026-07-20T08:00:00Z"),
        _frozen_item("reddit:one", repository_url, title, "2026-07-20T10:00:00Z"),
    ]

    candidates = generate_story_candidates(items, {})

    assert len(candidates) == 1
    assert candidates[0].decision == "auto_merge"
    assert candidates[0].features["stable_landing_url_match"] is True


def test_golden_set_export_and_import_are_release_scoped(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")
    data_release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )
    facets = create_facet_release(engine, data_release_id=data_release.release_id)
    stories = create_story_release(engine, facet_release_id=facets.facet_release_id)

    golden = export_golden_candidates(
        engine,
        stories.story_release_id,
        pair_limit=120,
        group_limit=30,
    )
    assert golden["pairs"]
    assert golden["groups"]
    golden["pairs"][0]["label"] = "same_story"
    golden["groups"][0]["label"] = "overmerge"

    imported = import_golden_labels(engine, golden)
    metrics = evaluate_story_release(engine, stories.story_release_id)

    assert imported == {"pair_labels": 1, "group_labels": 1}
    assert metrics["labeled_pairs"] == 1
    assert metrics["labeled_groups"] == 1
    assert metrics["publication_gate"] is False


def test_publish_and_rollback_switch_only_channel_pointer(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")
    release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )
    facets = create_facet_release(engine, data_release_id=release.release_id)
    first_stories = create_story_release(engine, facet_release_id=facets.facet_release_id)
    first_trends = create_trend_release(
        engine,
        story_release_id=first_stories.story_release_id,
    )
    first_publication = publish_radar(
        engine,
        story_release_id=first_stories.story_release_id,
        trend_release_id=first_trends.trend_release_id,
        channel="shadow",
    )
    second_stories = create_story_release(
        engine,
        facet_release_id=facets.facet_release_id,
        params={"auto_merge_threshold": 0.9},
    )
    first_story_ids = {
        row["story_id"]
        for row in engine.execute(
            "SELECT story_id FROM engine_stories WHERE story_release_id = ?",
            (first_stories.story_release_id,),
        ).fetchall()
    }
    second_story_ids = {
        row["story_id"]
        for row in engine.execute(
            "SELECT story_id FROM engine_stories WHERE story_release_id = ?",
            (second_stories.story_release_id,),
        ).fetchall()
    }
    assert first_story_ids <= second_story_ids
    second_trends = create_trend_release(
        engine,
        story_release_id=second_stories.story_release_id,
    )
    second_publication = publish_radar(
        engine,
        story_release_id=second_stories.story_release_id,
        trend_release_id=second_trends.trend_release_id,
        channel="shadow",
    )

    assert get_current_publication(engine, "shadow") == second_publication
    rollback_publication(
        engine,
        channel="shadow",
        to_publication_id=first_publication.publication_id,
    )
    assert get_current_publication(engine, "shadow") == first_publication
    assert engine.execute("SELECT COUNT(*) FROM radar_publications").fetchone()[0] == 2


def test_partial_release_requires_explicit_publish_override(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path, status="partial")
    engine = engine_db(tmp_path / "trend_engine.db")
    release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )
    facets = create_facet_release(engine, data_release_id=release.release_id)
    stories = create_story_release(engine, facet_release_id=facets.facet_release_id)
    trends = create_trend_release(engine, story_release_id=stories.story_release_id)

    with pytest.raises(ValueError, match="allow_partial"):
        publish_radar(
            engine,
            story_release_id=stories.story_release_id,
            trend_release_id=trends.trend_release_id,
            channel="shadow",
        )
    publication = publish_radar(
        engine,
        story_release_id=stories.story_release_id,
        trend_release_id=trends.trend_release_id,
        channel="shadow",
        allow_partial=True,
    )
    assert publication.input_status == "partial"


def test_production_channel_requires_quality_gates(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")
    release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )
    facets = create_facet_release(engine, data_release_id=release.release_id)
    stories = create_story_release(engine, facet_release_id=facets.facet_release_id)
    trends = create_trend_release(engine, story_release_id=stories.story_release_id)

    with pytest.raises(ValueError, match="publication gates"):
        publish_radar(
            engine,
            story_release_id=stories.story_release_id,
            trend_release_id=trends.trend_release_id,
            channel="broad",
        )


def test_legacy_lab_import_copies_only_checksum_matched_release(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    corpus.execute("PRAGMA wal_checkpoint(FULL)")
    corpus.commit()
    checksum = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    legacy_path = tmp_path / "cluster_lab.db"
    legacy = sqlite3.connect(legacy_path)
    legacy.executescript(
        """
        CREATE TABLE data_releases (
            release_id TEXT PRIMARY KEY,
            run_ids_json TEXT NOT NULL,
            source_db_checksum TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE cluster_experiments (
            experiment_id TEXT PRIMARY KEY,
            release_id TEXT NOT NULL,
            method TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            params_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    legacy.execute(
        "INSERT INTO data_releases VALUES (?, ?, ?, ?)",
        ("legacy_release", '["2026-07-27:broad"]', checksum, "2026-07-29T00:00:00Z"),
    )
    legacy.execute(
        "INSERT INTO cluster_experiments VALUES (?, ?, ?, ?, ?, ?)",
        (
            "legacy_experiment",
            "legacy_release",
            "hybrid_v1",
            "",
            "{}",
            "2026-07-29T00:01:00Z",
        ),
    )
    legacy.commit()
    legacy.close()
    engine = engine_db(tmp_path / "trend_engine.db")

    result = import_legacy_lab(
        engine,
        legacy_lab_path=legacy_path,
        source_db_path=corpus_path,
    )

    assert result["imported_releases"] == 1
    experiment = engine.execute(
        """
        SELECT status, engine_id FROM legacy_lab_imports
        WHERE legacy_kind = 'experiment' AND legacy_id = 'legacy_experiment'
        """
    ).fetchone()
    assert experiment["status"] == "requires_rerun"
    assert experiment["engine_id"]


def test_manual_labels_are_version_scoped(tmp_path: Path) -> None:
    engine = engine_db(tmp_path / "trend_engine.db")
    label_id = label_engine_target(
        engine,
        target_kind="story_pair",
        target_id="a|b",
        release_id="stories_v1",
        label="same_story",
        note="synthetic",
    )
    row = engine.execute("SELECT * FROM engine_labels WHERE label_id = ?", (label_id,)).fetchone()
    assert row["release_id"] == "stories_v1"
    assert row["label"] == "same_story"


def test_invalid_story_review_cache_can_be_replaced(tmp_path: Path) -> None:
    engine = engine_db(tmp_path / "trend_engine.db")
    target_id = "left|right"
    input_hash = "same-input"
    first = store_story_review_response(
        engine,
        target_id=target_id,
        input_hash=input_hash,
        raw_response="{not-json",
        allowed_item_ids={"left", "right"},
    )
    valid_response = json.dumps(
        {
            "decision": "same_story",
            "event_frame": {
                "actors": [],
                "action": "",
                "object": "",
                "geography": [],
                "event_date": "",
            },
            "evidence_item_ids": ["left", "right"],
            "conflicts": [],
            "confidence": 0.91,
            "reason": "Same event",
        }
    )
    second = store_story_review_response(
        engine,
        target_id=target_id,
        input_hash=input_hash,
        raw_response=valid_response,
        allowed_item_ids={"left", "right"},
    )

    row = engine.execute("SELECT decision, valid FROM llm_reviews").fetchone()
    assert first["valid"] is False
    assert second["valid"] is True
    assert row["decision"] == "same_story"
    assert row["valid"] == 1


def _run_ids() -> list[str]:
    return [f"2026-07-{day}:broad" for day in ("27", "28", "29")]


def _seed_corpus(path: Path, status: str = "complete") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    events = [
        (
            "2026-07-27",
            "event1",
            "OpenAI starts AI security review after model breach",
            "OpenAI begins AI security review following model breach",
        ),
        (
            "2026-07-28",
            "event2",
            "Anthropic tightens AI security rules after data leak",
            "Anthropic adds AI security rules following data leak",
        ),
        (
            "2026-07-29",
            "event3",
            "Google orders AI security review after agent incident",
            "Google opens AI security review following agent incident",
        ),
    ]
    for date, event_id, reuters_title, nyt_title in events:
        run_id = f"{date}:broad"
        upsert_run(
            conn,
            run_id=run_id,
            snapshot_date=date,
            profile="broad",
            status=status,
            started_at=f"{date}T07:00:00Z",
            finished_at=f"{date}T07:10:00Z",
        )
        items = [
            _item(f"{event_id}_reuters", "reuters", reuters_title, date),
            _item(f"{event_id}_nyt", "nytimes", nyt_title, date),
        ]
        upsert_items(conn, items)
        upsert_observations(
            conn,
            [
                Observation(
                    run_id=run_id,
                    item_id=item.item_id,
                    observed_at=f"{date}T07:00:00Z",
                )
                for item in items
            ],
        )
        save_source_health(
            conn,
            run_id,
            [
                SourceHealth(
                    source_id="reuters:technology",
                    provider="reuters",
                    cluster="business",
                    status="ok",
                    count=1,
                ),
                SourceHealth(
                    source_id="nytimes:technology",
                    provider="nytimes",
                    cluster="mainstream",
                    status="ok",
                    count=1,
                ),
            ],
        )
    conn.commit()
    return conn


def _item(item_id: str, provider: str, title: str, date: str) -> ContentItem:
    return ContentItem(
        item_id=item_id,
        provider=provider,
        source_cluster="business" if provider == "reuters" else "mainstream",
        external_id=item_id,
        canonical_url=f"https://{provider}.example/{item_id}",
        title=title,
        excerpt=f"{title}. Independent evidence and context.",
        published_at=f"{date}T06:00:00Z",
        observed_at=f"{date}T07:00:00Z",
        snapshot_date=date,
        content_scope="abstract",
        source_section="technology",
        domain_ids=["ai_technology", "security_privacy"],
    )


def _frozen_item(
    item_id: str,
    canonical_url: str,
    title: str,
    published_at: str,
) -> FrozenItem:
    return FrozenItem(
        item_id=item_id,
        provider=item_id.split(":", 1)[0],
        source_cluster="developers",
        canonical_url=canonical_url,
        target_url="",
        discussion_url="",
        title=title,
        excerpt="",
        published_at=published_at,
        snapshot_date="2026-07-29",
        content_scope="headline",
        source_section="top",
        domain_ids=["ai_technology"],
        raw_engagement={},
        metadata={},
    )
