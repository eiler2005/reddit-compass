"""Offline contract tests for the separated Story/Trend Engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from reddit_compass.intelligence.engine import (
    FrozenItem,
    _discover_trends_graph,
    _story_topic_keys,
    active_label_story_pairs,
    auto_label_story_pairs,
    cache_release_embeddings,
    compare_story_engine_variants,
    create_data_release,
    create_facet_release,
    create_story_release,
    create_trend_release,
    diagnose_engine_release,
    engine_db,
    evaluate_story_release,
    evaluate_trend_release,
    export_golden_candidates,
    export_story_candidates_for_release,
    generate_story_candidates,
    get_current_publication,
    import_golden_labels,
    import_legacy_lab,
    inspect_story_release,
    inspect_trend_release,
    label_engine_target,
    load_frozen_items,
    load_release_embeddings,
    prepare_story_review_jobs,
    publish_radar,
    rollback_publication,
    run_engine_cycle,
    store_story_review_response,
    train_story_merge_model,
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


def test_engine_db_uses_wal_for_concurrent_api_reads(tmp_path: Path) -> None:
    engine = engine_db(tmp_path / "trend_engine.db")

    assert str(engine.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
    assert int(engine.execute("PRAGMA busy_timeout").fetchone()[0]) == 10000


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


def test_data_release_marks_expected_empty_voice_source_partial(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    save_source_health(
        corpus,
        "2026-07-27:broad",
        [
            SourceHealth(
                source_id="reddit",
                provider="reddit",
                cluster="voices",
                status="ok",
                count=0,
            )
        ],
    )
    engine = engine_db(tmp_path / "trend_engine.db")

    release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=["2026-07-27:broad"],
    )

    health = engine.execute(
        "SELECT status, message FROM release_source_health WHERE release_id = ?",
        (release.release_id,),
    ).fetchone()
    assert release.input_status == "partial"
    assert health["status"] == "empty"
    assert "expected at least 1 item" in health["message"]
    report = diagnose_engine_release(engine, data_release_id=release.release_id)
    assert "input_status_partial" in report["warnings"]
    assert "dominant_cluster_empty:voices" in report["warnings"]


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


def test_huggingface_model_release_url_can_merge_launch_wave() -> None:
    model_url = "https://huggingface.co/moonshotai/Kimi-K3"
    items = [
        _frozen_item(
            "reddit:kimi",
            model_url,
            "KIMI K3’s WEIGHTS ARE OUT!",
            "2026-07-28T08:00:00Z",
        ),
        _frozen_item(
            "hn:kimi",
            model_url,
            "Kimi-K3 on HuggingFace",
            "2026-07-29T08:00:00Z",
        ),
    ]

    candidates = generate_story_candidates(items, {})

    assert len(candidates) == 1
    assert candidates[0].decision == "auto_merge"
    assert candidates[0].reason == "shared HuggingFace model release URL"


def test_near_duplicate_title_fingerprint_merges_syndicated_headlines() -> None:
    items = [
        _frozen_item(
            "wired:claude",
            "https://wired.example/claude-chats-search",
            "Private Claude chats exposed in Google and Bing search results",
            "2026-07-29T08:00:00Z",
        ),
        _frozen_item(
            "reddit:claude",
            "https://reddit.example/discussion",
            "Private Claude chats exposed through Google and Bing searches",
            "2026-07-29T09:00:00Z",
        ),
    ]

    candidates = generate_story_candidates(
        items,
        {},
        params={"near_duplicate_enabled": True},
    )

    assert len(candidates) == 1
    assert candidates[0].decision == "auto_merge"
    assert candidates[0].reason == "near-duplicate title fingerprint"
    assert "near_duplicate" in candidates[0].features["generated_by"]
    assert candidates[0].features["near_duplicate_simhash_distance"] <= 18


def test_semantic_dedup_embedding_needs_provenance_review() -> None:
    items = [
        _frozen_item(
            "guardian:openai",
            "https://guardian.example/openai-model-delay",
            "OpenAI delays model launch after safety tests",
            "2026-07-29T08:00:00Z",
        ),
        _frozen_item(
            "hn:openai",
            "https://news.ycombinator.com/item?id=1",
            "Safety testing forces OpenAI to postpone model launch",
            "2026-07-29T09:00:00Z",
        ),
    ]

    candidates = generate_story_candidates(
        items,
        {
            "guardian:openai": {"entities": json.dumps(["openai"])},
            "hn:openai": {"entities": json.dumps(["openai"])},
        },
        params={
            "semantic_dedup_enabled": True,
            "semantic_dedup_threshold": 0.9,
            "dense_candidate_threshold": 0.9,
            "dense_top_k": 4,
        },
        embeddings={
            "guardian:openai": [1.0, 0.0, 0.0],
            "hn:openai": [0.99, 0.01, 0.0],
        },
    )

    assert len(candidates) == 1
    assert candidates[0].decision == "review"
    assert candidates[0].reason == "ambiguous event similarity; LLM/manual review required"
    assert candidates[0].features["dense_similarity"] >= 0.9
    assert candidates[0].features["semantic_review_match"] is True


def test_cross_source_event_title_match_merges_news_but_not_topic_posts() -> None:
    news_items = [
        _frozen_item(
            "ft:oil",
            "https://ft.example/oil-hormuz",
            "Oil price tumbles as Iran and US pause strikes over Strait of Hormuz",
            "2026-07-29T08:00:00Z",
        ),
        _frozen_item(
            "guardian:oil",
            "https://guardian.example/oil-hormuz",
            "Oil prices fall as US pauses strikes on Iran over strait of Hormuz",
            "2026-07-30T08:00:00Z",
        ),
    ]
    candidates = generate_story_candidates(
        news_items,
        {
            "ft:oil": {"entities": json.dumps(["oil", "iran", "hormuz"])},
            "guardian:oil": {"entities": json.dumps(["oil", "iran", "hormuz"])},
        },
    )
    assert len(candidates) == 1
    assert candidates[0].decision == "auto_merge"
    assert candidates[0].reason == "cross-source event title/entity match"

    topic_items = [
        _frozen_item(
            "hn:vibe",
            "https://news.ycombinator.com/item?id=1",
            "Mechanism of Vibe Coding",
            "2026-07-29T08:00:00Z",
        ),
        _frozen_item(
            "reddit:vibe",
            "https://reddit.example/vibe",
            "I love Vibe Coding!",
            "2026-07-30T08:00:00Z",
        ),
    ]
    topic_candidates = generate_story_candidates(
        topic_items,
        {
            "hn:vibe": {"entities": json.dumps(["vibe", "coding"])},
            "reddit:vibe": {"entities": json.dumps(["vibe", "coding"])},
        },
    )
    assert all(candidate.decision != "auto_merge" for candidate in topic_candidates)


def test_story_engine_ab_compare_runs_all_variants(tmp_path: Path) -> None:
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

    comparison = compare_story_engine_variants(
        engine,
        facet_release_id=facets.facet_release_id,
        base_params={
            "embedding_model": "missing-model",
            "dense_candidate_threshold": 0.55,
        },
        limit=6,
        sample_limit=2,
    )

    variants = comparison["variants"]
    assert [variant["variant"] for variant in variants] == [
        "baseline_sparse_dense",
        "minhash_simhash_near_duplicates",
        "semantic_dedup",
        "combined_near_and_semantic",
    ]
    assert all(variant["story_release_id"].startswith("stories_") for variant in variants)
    assert variants[0]["delta_vs_baseline"]["story_count"] == 0


def test_story_candidate_export_is_read_only_and_explainable(tmp_path: Path) -> None:
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

    exported = export_story_candidates_for_release(
        engine,
        facet_release_id=facets.facet_release_id,
        params={"embedding_model": "missing-model"},
        limit=6,
        candidate_limit=3,
    )

    assert exported["data_release_id"] == data_release.release_id
    assert exported["item_count"] == 6
    assert exported["candidate_count"] == 3
    assert exported["decision_counts"]["auto_merge"] >= 1
    assert exported["candidates"][0]["left"]["provider"] in {"nytimes", "reuters"}
    assert exported["candidates"][0]["features"]["generated_by"]
    assert engine.execute("SELECT COUNT(*) FROM story_releases").fetchone()[0] == 0


def test_release_embeddings_can_be_loaded_for_selected_items_only(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")
    data_release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )
    cache_release_embeddings(
        engine,
        data_release_id=data_release.release_id,
        model_name="lexical-hash-v1",
    )

    all_item_ids = [item.item_id for item in load_frozen_items(engine, data_release.release_id)]
    selected_item_ids = set(all_item_ids[:2])

    selected = load_release_embeddings(
        engine,
        data_release_id=data_release.release_id,
        model_name="lexical-hash-v1",
        item_ids=selected_item_ids,
    )

    assert set(selected) == selected_item_ids
    assert len(selected) < len(all_item_ids)
    assert (
        load_release_embeddings(
            engine,
            data_release_id=data_release.release_id,
            model_name="lexical-hash-v1",
            item_ids=[],
        )
        == {}
    )


def test_release_embedding_cache_reuses_vectors_without_empty_encode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")
    data_release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )
    cache_release_embeddings(
        engine,
        data_release_id=data_release.release_id,
        model_name="lexical-hash-v1",
    )

    def unexpected_encode(*_args: object, **_kwargs: object) -> list[list[float]]:
        pytest.fail("existing hashes must not invoke the embedding backend with an empty batch")

    monkeypatch.setattr("reddit_compass.intelligence.engine.encode_passages", unexpected_encode)
    cached = cache_release_embeddings(
        engine,
        data_release_id=data_release.release_id,
        model_name="lexical-hash-v1",
    )

    assert cached["new_vector_count"] == 0
    assert cached["cached_vector_count"] == cached["item_count"]


def test_engine_diagnose_reports_undermerge_and_next_commands(tmp_path: Path) -> None:
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
    stories = create_story_release(
        engine,
        facet_release_id=facets.facet_release_id,
        params={
            "auto_merge_threshold": 0.99,
            "review_threshold": 0.4,
            "cross_source_event_title_enabled": False,
        },
    )
    trends = create_trend_release(engine, story_release_id=stories.story_release_id)

    report = diagnose_engine_release(
        engine,
        story_release_id=stories.story_release_id,
        trend_release_id=trends.trend_release_id,
        limit=5,
    )

    assert report["data_release"]["release_id"] == data_release.release_id
    assert report["story_release"]["story_release_id"] == stories.story_release_id
    assert report["trend_release"]["trend_release_id"] == trends.trend_release_id
    assert report["candidate_decision_counts"]["review"] >= 1
    assert report["possible_undermerge_pairs"]
    assert (
        "cross_source_low: verify canonical URLs, Reddit target URLs and title/entity matching"
        in report["warnings"]
    )
    assert any("engine stories candidates" in command for command in report["next_commands"])


def test_generic_topic_phrase_does_not_become_trend_pattern() -> None:
    assert "open source" not in _story_topic_keys(
        {"title": "Open source AI tools reshape developer workflows"}
    )
    assert "security review" in _story_topic_keys(
        {"title": "OpenAI starts security review after model breach"}
    )


def test_theme_alone_does_not_create_trend() -> None:
    stories = [
        {
            "story_id": f"story_{index}",
            "title": title,
            "domain_ids": json.dumps(["business_markets"]),
            "first_seen": f"2026-07-{27 + index}",
            "last_seen": f"2026-07-{27 + index}",
            "project_scores": "{}",
        }
        for index, title in enumerate(
            [
                "Oracle quarterly revenue beats expectations",
                "Startup founder discusses hiring plan",
                "Retail chain changes loyalty program",
            ]
        )
    ]
    story_items = {f"story_{index}": [f"item_{index}"] for index in range(3)}
    facets = {
        f"item_{index}": {
            "candidate_themes": json.dumps(["business"]),
            "theme_ids": "[]",
            "pain_points": "[]",
            "event_frame_json": "{}",
        }
        for index in range(3)
    }

    trends, _ = _discover_trends_graph(
        stories,
        story_items,
        facets,
        {},
        params={"min_stories": 3, "min_dates": 2},
    )

    assert trends == []


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
        allow_partial=True,
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
        allow_partial=True,
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

    with pytest.raises(ValueError, match="complete Data Release"):
        publish_radar(
            engine,
            story_release_id=stories.story_release_id,
            trend_release_id=trends.trend_release_id,
            channel="broad",
            allow_partial=True,
        )


def test_production_channel_requires_quality_gates(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    _add_voice_coverage(corpus)
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


def test_production_channel_accepts_quality_floors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    _add_voice_coverage(corpus)
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

    class PassingFloor:
        passed = True

    monkeypatch.setattr(
        "reddit_compass.intelligence.quality.compute_quality",
        lambda *_args, **_kwargs: {"stories_overmerge_ge5": 0},
    )
    monkeypatch.setattr(
        "reddit_compass.intelligence.quality.evaluate_floors",
        lambda _metrics: [PassingFloor()],
    )

    publication = publish_radar(
        engine,
        story_release_id=stories.story_release_id,
        trend_release_id=trends.trend_release_id,
        channel="broad",
        allow_partial=True,
    )

    assert get_current_publication(engine, "broad") == publication


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


def test_active_label_story_pairs_writes_informative_pair_labels(tmp_path: Path) -> None:
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
    answers = iter(["y", "n"])
    output: list[str] = []

    result = active_label_story_pairs(
        engine,
        stories.story_release_id,
        target=2,
        input_fn=lambda _: next(answers),
        output_fn=output.append,
    )

    rows = engine.execute(
        """
        SELECT label
        FROM engine_labels
        WHERE target_kind = 'story_pair' AND release_id = ?
        ORDER BY created_at
        """,
        (stories.story_release_id,),
    ).fetchall()
    assert result["asked"] == 2
    assert {row["label"] for row in rows} == {"same_story", "different_story"}
    assert any("features:" in line for line in output)


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


def test_story_review_jobs_use_the_same_pair_key_as_training(tmp_path: Path) -> None:
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
    engine.execute(
        """INSERT INTO story_candidate_pairs
           (story_release_id, item_id_a, item_id_b, score, decision, features_json, reason)
           VALUES (?, ?, ?, ?, 'review', '{}', 'test review')""",
        (stories.story_release_id, "event1_reuters", "event1_nyt", 0.5),
    )
    engine.commit()

    jobs = prepare_story_review_jobs(engine, stories.story_release_id, limit=1)

    assert len(jobs) == 1
    assert jobs[0]["target_id"] == "event1_nyt|event1_reuters"
    label_engine_target(
        engine,
        target_kind="story_pair",
        target_id=str(jobs[0]["target_id"]),
        release_id=stories.story_release_id,
        label="same_story",
        note="qwen_review",
    )
    label = engine.execute(
        """SELECT target_id FROM engine_labels
           WHERE release_id = ? AND target_kind = 'story_pair'""",
        (stories.story_release_id,),
    ).fetchone()
    assert label["target_id"] == "event1_nyt|event1_reuters"


def test_auto_label_and_train_merge_model_persist(tmp_path: Path) -> None:
    engine = engine_db(tmp_path / "trend_engine.db")
    engine.execute(
        """INSERT INTO story_releases
           (story_release_id, facet_release_id, method, params_hash, status,
            metrics_json, git_sha, created_at)
           VALUES ('stories_ml', 'facets_ml', 'hybrid_v2', 'h', 'evaluated', '{}', 'sha',
                   '2026-07-30T00:00:00Z')"""
    )

    def positive() -> dict[str, object]:
        return {
            "title_score": 0.93,
            "token_jaccard": 0.7,
            "entity_score": 0.8,
            "shared_entities": ["openai"],
            "shared_action_tokens": ["launch"],
            "date_distance_days": 1,
            "source_independent": True,
            "action_match": True,
            "number_conflict": False,
            "location_conflict": False,
            "person_conflict": False,
        }

    def negative() -> dict[str, object]:
        return {
            "title_score": 0.2,
            "token_jaccard": 0.05,
            "entity_score": 0.0,
            "shared_entities": [],
            "shared_action_tokens": [],
            "date_distance_days": 1,
            "source_independent": True,
            "action_match": False,
            "number_conflict": True,
            "location_conflict": False,
            "person_conflict": False,
        }

    pairs = []
    for i in range(15):
        pairs.append(
            (
                "stories_ml",
                f"a{i}",
                f"b{i}",
                0.9,
                "auto_merge",
                json.dumps(positive()),
                "near-duplicate title fingerprint",
            )
        )
        pairs.append(
            (
                "stories_ml",
                f"c{i}",
                f"d{i}",
                0.2,
                "reject",
                json.dumps(negative()),
                "number/date event conflict",
            )
        )
    engine.executemany(
        """INSERT INTO story_candidate_pairs
           (story_release_id, item_id_a, item_id_b, score, decision, features_json, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        pairs,
    )
    engine.commit()

    labeled = auto_label_story_pairs(engine, "stories_ml")
    assert labeled["labels"].get("same_story") == 15
    assert labeled["labels"].get("different_story") == 15
    # Повторный прогон не дублирует метки.
    assert auto_label_story_pairs(engine, "stories_ml")["added"] == 0

    trained = train_story_merge_model(engine, "stories_ml")
    assert trained["label_source"] == "auto"
    assert trained["labeled_pairs"] == 30
    model = trained["model"]
    assert model["precision_at_threshold"] >= 0.95
    assert model["model_hash"]

    row = engine.execute(
        "SELECT metrics_json FROM story_releases WHERE story_release_id = 'stories_ml'"
    ).fetchone()
    stored = json.loads(row["metrics_json"])["merge_model"]
    assert stored["model_hash"] == model["model_hash"]

    # Человеческая метка имеет приоритет над авто-меткой.
    label_engine_target(
        engine,
        target_kind="story_pair",
        target_id="a0|b0",
        release_id="stories_ml",
        label="different_story",
        note="manual",
    )
    retrained = train_story_merge_model(engine, "stories_ml")
    assert retrained["label_source"] == "human"


def test_create_trend_release_embedding_v2(tmp_path: Path) -> None:
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
        method="embedding_v2",
        params={"min_stories": 3, "min_dates": 2},
    )
    assert trends.status == "evaluated"
    rows = engine.execute(
        "SELECT name_ru, source_scope, confidence FROM engine_trends WHERE trend_release_id = ?",
        (trends.trend_release_id,),
    ).fetchall()
    for row in rows:
        assert row["source_scope"] in {"cross_source", "community_only", "mainstream_only"}
        assert len(str(row["name_ru"]).split()) >= 2


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


def _add_voice_coverage(conn: sqlite3.Connection) -> None:
    """Make a synthetic broad release complete without weakening its gates."""
    items: list[ContentItem] = []
    observations: list[Observation] = []
    for run_id, date in zip(_run_ids(), ("2026-07-27", "2026-07-28", "2026-07-29"), strict=True):
        item = ContentItem(
            item_id=f"reddit_{date}",
            provider="reddit",
            source_cluster="voices",
            external_id=f"reddit_{date}",
            canonical_url=f"https://reddit.example/{date}",
            title=f"Reddit discussion for {date}",
            observed_at=f"{date}T07:00:00Z",
            snapshot_date=date,
            content_scope="excerpt",
        )
        items.append(item)
        observations.append(
            Observation(run_id=run_id, item_id=item.item_id, observed_at=item.observed_at)
        )
    upsert_items(conn, items)
    upsert_observations(conn, observations)
    conn.commit()


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


def _cycle_item(item_id: str, provider: str, cluster: str, title: str, date: str) -> ContentItem:
    return ContentItem(
        item_id=item_id,
        provider=provider,
        source_cluster=cluster,
        external_id=item_id,
        canonical_url=f"https://{provider}.example/{item_id}",
        title=title,
        excerpt=f"{title}. Context and evidence.",
        published_at=f"{date}T06:00:00Z",
        observed_at=f"{date}T07:00:00Z",
        snapshot_date=date,
        content_scope="abstract",
        source_section="technology" if provider != "reddit" else "artificial",
        domain_ids=["ai_technology"],
        raw_engagement={"score": 100, "comments": 20, "upvote_ratio": 0.9},
        metadata={"is_self": provider == "reddit"},
    )


def _seed_cycle_corpus(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    for date in ("2026-07-28", "2026-07-29"):
        run_id = f"{date}:broad"
        upsert_run(
            conn,
            run_id=run_id,
            snapshot_date=date,
            profile="broad",
            status="complete",
            started_at=f"{date}T07:00:00Z",
            finished_at=f"{date}T07:10:00Z",
        )
        items = [
            # cross-source same story (provenance merge → same_story)
            _cycle_item(
                f"a_reddit_{date}", "reddit", "voices", "OpenAI launches quantum agent", date
            ),
            _cycle_item(
                f"a_reuters_{date}",
                "reuters",
                "mainstream",
                "OpenAI launches quantum agent platform",
                date,
            ),
            # number conflict → reject → different_story
            _cycle_item(
                f"c_reddit_{date}", "reddit", "voices", "OpenAI raises 5 billion in funding", date
            ),
            _cycle_item(
                f"c_reuters_{date}",
                "reuters",
                "mainstream",
                "OpenAI raises 9 billion in funding",
                date,
            ),
        ]
        upsert_items(conn, items)
        upsert_observations(
            conn,
            [
                Observation(run_id=run_id, item_id=it.item_id, observed_at=f"{date}T07:00:00Z")
                for it in items
            ],
        )
    conn.commit()
    return conn


def test_run_engine_cycle_builds_all_layers(tmp_path: Path) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_cycle_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")
    try:
        result = asyncio.run(
            run_engine_cycle(
                corpus,
                engine,
                corpus_path=corpus_path,
                profile="broad",
                window=2,
                theme_catalog={},
                pack_by_subreddit={"artificial": "ai_technology"},
                review_limit=0,
                publish_channel=None,
                pulse=True,
            )
        )
    finally:
        corpus.close()
        engine.close()

    assert result["data_release_id"]
    assert result["story_release_id"]
    assert result["trend_release_id"]
    assert result["signal_release_id"]  # pulse ran (reddit present)
    assert result["auto_labels"] > 0
    assert result["reviewed_pairs"] == 0  # no Qwen runner
    assert result["publication_id"] == ""  # publish skipped
    # На вырожденном синтетическом корпусе обучение корректно пропускается;
    # на реальных данных (с LLM-фасетами) обе категории присутствуют.
    assert result["label_source"] in {"auto", "qwen", "human", "skipped"}


def test_run_engine_cycle_rebuilds_stories_after_valid_qwen_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid bounded review must affect this cycle, not wait until tomorrow."""
    import reddit_compass.intelligence.engine as engine_module

    corpus_path = tmp_path / "compass.db"
    corpus = _seed_cycle_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")
    item_ids = ["a_reddit_2026-07-28", "a_reuters_2026-07-28"]
    job = {
        "target_id": "|".join(sorted(item_ids)),
        "item_ids": item_ids,
        "model": "qwen-test",
        "prompt_version": engine_module.STORY_REVIEW_PROMPT_VERSION,
        "input_hash": "test-review-input",
        "prompt": "test pair",
    }
    monkeypatch.setattr(engine_module, "prepare_story_review_jobs", lambda *_args, **_kwargs: [job])

    async def review_runner(_prompt: str, _model: str) -> str:
        return json.dumps(
            {
                "decision": "same_story",
                "event_frame": {
                    "actors": ["OpenAI"],
                    "action": "launches",
                    "object": "quantum agent",
                    "geography": [],
                    "event_date": "2026-07-28",
                },
                "evidence_item_ids": item_ids,
                "conflicts": [],
                "confidence": 0.91,
                "reason": "Same launch",
            }
        )

    try:
        result = asyncio.run(
            run_engine_cycle(
                corpus,
                engine,
                corpus_path=corpus_path,
                profile="broad",
                window=2,
                embed_model="",
                review_model="qwen-test",
                review_limit=1,
                review_runner=review_runner,
                publish_channel=None,
                pulse=False,
            )
        )
    finally:
        corpus.close()
        engine.close()

    assert result["valid_reviewed_pairs"] == 1
    assert result["reviewed_story_rebuilt"] is True
    assert result["story_release_id"] != result["provisional_story_release_id"]
