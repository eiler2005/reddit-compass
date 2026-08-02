"""Offline contract tests for the separated Story/Trend Engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import pytest

from reddit_compass.intelligence.engine import (
    FrozenItem,
    PairCandidate,
    _add_index_pairs,
    _constrained_story_groups,
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
    resolve_pair_labels,
    rollback_publication,
    run_engine_cycle,
    store_quality_report,
    store_story_review_response,
    train_story_merge_model,
    verify_data_release,
)
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import (
    ContentItem,
    Observation,
    SourceCluster,
    SourceHealth,
)
from reddit_compass.intelligence.quality import FloorResult
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


def test_quality_report_is_persisted_by_immutable_release_identity(tmp_path: Path) -> None:
    engine = engine_db(tmp_path / "trend_engine.db")
    floor = FloorResult(
        metric="stories_overmerge_ge5",
        value=0,
        floor=0,
        op="max",
        passed=True,
        desc="no overmerged stories",
    )

    first = store_quality_report(
        engine,
        data_release_id="data_test",
        story_release_id="stories_test",
        trend_release_id="trends_test",
        signal_release_id=None,
        metrics={"stories_total": 12},
        floors=[floor],
    )
    second = store_quality_report(
        engine,
        data_release_id="data_test",
        story_release_id="stories_test",
        trend_release_id="trends_test",
        signal_release_id="signals_test",
        metrics={"stories_total": 13},
        floors=[floor],
    )
    row = engine.execute(
        """SELECT signal_release_id, metrics_json, floors_json, passed
           FROM engine_quality_reports
           WHERE data_release_id = ? AND story_release_id = ? AND trend_release_id = ?""",
        ("data_test", "stories_test", "trends_test"),
    ).fetchone()

    assert first["passed"] is True
    assert second["passed"] is True
    assert row is not None
    assert row["signal_release_id"] == "signals_test"
    assert json.loads(str(row["metrics_json"]))["stories_total"] == 13
    assert json.loads(str(row["floors_json"]))[0]["metric"] == "stories_overmerge_ge5"
    assert row["passed"] == 1


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


def test_data_release_marks_single_cluster_production_corpus_partial(tmp_path: Path) -> None:
    """Форма релиза 2026-08-01: run ``complete``, но выжил один кластер из шести.

    Пропавшие адаптеры не оставляют health-строк вообще, поэтому проверка «нет ли
    плохих строк» их не видела, и релиз доходил до полов качества, где
    ``stories_cross_source_per_1k`` структурно нулевой на одном провайдере.
    """
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    corpus.execute("DELETE FROM observations")
    corpus.execute("DELETE FROM items")
    corpus.commit()
    _add_required_cluster_coverage(corpus)
    corpus.execute("DELETE FROM observations WHERE item_id LIKE 'hackernews_%'")
    corpus.execute("DELETE FROM items WHERE provider = 'hackernews'")
    corpus.commit()
    engine = engine_db(tmp_path / "trend_engine.db")

    release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )

    assert release.input_status == "partial"


def test_data_release_accepts_granular_voice_coverage_over_empty_aggregate(
    tmp_path: Path,
) -> None:
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_corpus(corpus_path)
    _add_required_cluster_coverage(corpus)
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
            ),
            SourceHealth(
                source_id="reddit:LocalLLaMA",
                provider="reddit",
                cluster="voices",
                status="ok",
                count=20,
            ),
        ],
    )
    engine = engine_db(tmp_path / "trend_engine.db")

    release = create_data_release(
        corpus,
        engine,
        source_db_path=corpus_path,
        run_ids=_run_ids(),
    )

    assert release.input_status == "complete"


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
    # Тест про независимость версий релизов, а не про выбор алгоритма. Метод закреплён
    # явно: у синтетической фикстуры нет кэша эмбеддингов, и дефолтный `embedding_v2`
    # на трёх историях без векторов честно не находит связного тренда.
    # Сам `embedding_v2` покрыт отдельно — test_create_trend_release_embedding_v2.
    trends = create_trend_release(
        engine,
        story_release_id=stories.story_release_id,
        method="story_graph_v1",
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


def test_sparse_candidate_buckets_and_global_pair_budget_are_bounded() -> None:
    pair_reasons: dict[tuple[str, str], set[str]] = defaultdict(set)

    reached = _add_index_pairs(
        pair_reasons,
        [f"item:{index:03d}" for index in range(100)],
        "token",
        max_candidate_pairs=25,
    )

    assert reached is True
    assert len(pair_reasons) == 25

    # A shared high-frequency entity is not meaningful event evidence.  It
    # must be skipped instead of turning 80 inputs into 3,160 fuzzy scores.
    items = [
        _frozen_item(
            f"source:{index:03d}",
            f"https://example.test/{index}",
            f"OpenAI routine update {index}",
            "2026-07-29T08:00:00Z",
        )
        for index in range(80)
    ]
    candidates = generate_story_candidates(
        items,
        {item.item_id: {"entities": json.dumps(["openai"])} for item in items},
        params={"near_duplicate_enabled": False, "max_sparse_bucket_size": 16},
    )

    assert candidates == []


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


def test_bounded_component_experiment_promotes_only_explicit_review_candidates() -> None:
    items = [
        _frozen_item(
            "reuters:atlas-one",
            "https://reuters.example/atlas-one",
            "OpenAI unveils Atlas after security review",
            "2026-07-29T08:00:00Z",
        ),
        _frozen_item(
            "reuters:atlas-two",
            "https://reuters.example/atlas-two",
            "OpenAI Atlas launch follows security review",
            "2026-07-30T08:00:00Z",
        ),
    ]
    facets = {item.item_id: {"entities": json.dumps(["openai", "atlas"])} for item in items}

    default_candidates = generate_story_candidates(
        items,
        facets,
        params={"near_duplicate_enabled": False},
    )
    experiment_candidates = generate_story_candidates(
        items,
        facets,
        params={
            "near_duplicate_enabled": False,
            "bounded_component_enabled": True,
        },
    )

    assert default_candidates[0].decision == "review"
    assert experiment_candidates[0].decision == "auto_merge"
    assert experiment_candidates[0].reason == "bounded component evidence candidate"
    assert experiment_candidates[0].features["bounded_component_candidate"] is True


def test_bounded_component_experiment_caps_chain_at_four_items() -> None:
    items = [
        _frozen_item(
            f"provider{index}:item",
            f"https://example.test/{index}",
            f"Atlas launch evidence {index}",
            "2026-07-29T08:00:00Z",
        )
        for index in range(5)
    ]
    candidates = [
        PairCandidate(
            item_id_a=items[index].item_id,
            item_id_b=items[index + 1].item_id,
            score=0.8,
            decision="auto_merge",
            reason="bounded component evidence candidate",
            features={},
        )
        for index in range(4)
    ]

    groups = _constrained_story_groups(
        items,
        candidates,
        params={"bounded_component_enabled": True, "bounded_component_max_items": 4},
    )

    assert sorted(len(group) for group in groups) == [1, 4]
    assert any(
        membership_reason == "bounded component membership without direct medoid edge"
        for group in groups
        for _item, _score, membership_reason in group
    )


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


# --- `engine golden export --format review` (Фаза 3.1) --------------------------------
#
# The matching heuristics can't be steered precisely enough to produce a hand-checkable
# score/cluster distribution, so these tests build a data release from synthetic items
# spanning four source_clusters and then insert `story_candidate_pairs` rows directly —
# the same pattern already used by `test_story_review_jobs_use_the_same_pair_key_as_training`
# and `test_auto_label_and_train_merge_model_persist` above.

_REVIEW_SAMPLE_CLUSTERS = ("voices", "mainstream", "developers", "business")
_REVIEW_SAMPLE_PROVIDERS = {
    "voices": "reddit",
    "mainstream": "nytimes",
    "developers": "hackernews",
    "business": "reuters",
}
# 5 boundary-window (>=1 excess pair beyond the [0.45, 0.65] target quota is still
# reserved for tail-only scores) + 3 tail-only scores, one voices<->mainstream pair
# each: this cluster pairing is the scarce, high-value one the export must not starve.
_CROSS_BOUNDARY_SCORES = (0.45, 0.50, 0.55, 0.60, 0.65)
_CROSS_TAIL_SCORES = (0.20, 0.30, 0.40)
# Non voices<->mainstream combinations, 12 boundary + 12 tail scores each, so the
# boundary-window and tail pools are large enough to prove the >=50% quota precisely.
_NONCROSS_COMBOS = (
    ("voices", "voices"),
    ("mainstream", "mainstream"),
    ("developers", "developers"),
    ("business", "business"),
    ("developers", "business"),
)


def _review_sample_item(cluster: str, index: int, date: str) -> ContentItem:
    provider = _REVIEW_SAMPLE_PROVIDERS[cluster]
    item_id = f"{cluster}_{index}"
    return ContentItem(
        item_id=item_id,
        provider=provider,
        source_cluster=cluster,  # type: ignore[arg-type]
        external_id=item_id,
        canonical_url=f"https://{provider}.example/{item_id}",
        title=f"{cluster} synthetic story {index}",
        excerpt=f"{cluster} synthetic story {index}. Context for review sampling tests.",
        published_at=f"{date}T06:00:00Z",
        observed_at=f"{date}T07:00:00Z",
        snapshot_date=date,
        content_scope="abstract",
        source_section="technology",
        domain_ids=["ai_technology"],
    )


def _review_pair_features(score: float) -> str:
    return json.dumps(
        {
            "title_score": round(score, 4),
            "token_jaccard": round(score * 0.6, 4),
            "entity_score": round(score * 0.8, 4),
            "dense_similarity": None,
            "shared_entities": ["synthetic"],
            "number_conflict": False,
            "location_conflict": False,
            "person_conflict": False,
        }
    )


def _seed_review_sample_release(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    """Story release whose `story_candidate_pairs` are fully hand-controlled: 128
    decision='review' pairs spanning the score boundary window and every cluster
    combination (with a dedicated voices<->mainstream slice), plus two non-review
    pairs that must never appear in a `--format review` export."""
    corpus_path = tmp_path / "compass.db"
    corpus = sqlite3.connect(corpus_path)
    corpus.row_factory = sqlite3.Row
    migrate(corpus)
    date = "2026-07-29"
    run_id = f"{date}:broad"
    upsert_run(
        corpus,
        run_id=run_id,
        snapshot_date=date,
        profile="broad",
        status="complete",
        started_at=f"{date}T07:00:00Z",
        finished_at=f"{date}T07:10:00Z",
    )
    items = [
        _review_sample_item(cluster, index, date)
        for cluster in _REVIEW_SAMPLE_CLUSTERS
        for index in range(20)
    ]
    upsert_items(corpus, items)
    upsert_observations(
        corpus,
        [
            Observation(run_id=run_id, item_id=item.item_id, observed_at=item.observed_at)
            for item in items
        ],
    )
    corpus.commit()

    engine = engine_db(tmp_path / "trend_engine.db")
    data_release = create_data_release(corpus, engine, source_db_path=corpus_path, run_ids=[run_id])
    facets = create_facet_release(engine, data_release_id=data_release.release_id)
    stories = create_story_release(engine, facet_release_id=facets.facet_release_id)
    # Replace whatever the real matching heuristics produced with the controlled pool.
    engine.execute(
        "DELETE FROM story_candidate_pairs WHERE story_release_id = ?",
        (stories.story_release_id,),
    )

    used_pairs: set[tuple[str, str]] = set()
    rows: list[tuple[str, str, str, float, str, str, str]] = []

    def add(item_a: str, item_b: str, score: float, decision: str) -> None:
        key = tuple(sorted((item_a, item_b)))
        assert key not in used_pairs, f"duplicate synthetic pair {key}"
        used_pairs.add(key)
        rows.append(
            (
                stories.story_release_id,
                key[0],
                key[1],
                score,
                decision,
                _review_pair_features(score),
                "synthetic",
            )
        )

    for index, score in enumerate(_CROSS_BOUNDARY_SCORES + _CROSS_TAIL_SCORES):
        add(f"voices_{index}", f"mainstream_{index}", score, "review")

    for cluster_a, cluster_b in _NONCROSS_COMBOS:
        for k in range(12):
            boundary_score = round(0.45 + 0.20 * k / 11, 4)
            tail_score = round(0.10 + 0.05 * k, 4) if k < 6 else round(0.70 + 0.05 * (k - 6), 4)
            add(f"{cluster_a}_{k % 20}", f"{cluster_b}_{(k + 7) % 20}", boundary_score, "review")
            add(f"{cluster_a}_{k % 20}", f"{cluster_b}_{(k + 3) % 20}", tail_score, "review")

    # Non-review decisions must be invisible to a --format review export.
    add("voices_19", "mainstream_19", 0.90, "auto_merge")
    add("developers_19", "business_19", 0.10, "reject")

    engine.executemany(
        """INSERT INTO story_candidate_pairs
           (story_release_id, item_id_a, item_id_b, score, decision, features_json, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    engine.commit()
    return engine, stories.story_release_id


def test_golden_review_export_includes_only_review_decision_pairs(tmp_path: Path) -> None:
    engine, story_release_id = _seed_review_sample_release(tmp_path)

    golden = export_golden_candidates(
        engine,
        story_release_id,
        output_format="review",
        sample=200,
        seed=13,
    )

    assert golden["format"] == "review"
    # 128 review pairs were seeded; the 2 auto_merge/reject pairs are excluded.
    assert len(golden["pairs"]) == 128
    assert all(pair["decision"] == "review" for pair in golden["pairs"])
    exported_ids = {pair["pair_id"] for pair in golden["pairs"]}
    assert "mainstream_19|voices_19" not in exported_ids
    assert "business_19|developers_19" not in exported_ids


def test_golden_set_export_reserves_pairs_for_each_engine_decision(tmp_path: Path) -> None:
    engine, story_release_id = _seed_review_sample_release(tmp_path)

    golden = export_golden_candidates(
        engine,
        story_release_id,
        pair_limit=20,
        group_limit=0,
    )

    decisions = {pair["engine_decision"] for pair in golden["pairs"]}
    assert len(golden["pairs"]) == 20
    assert {"auto_merge", "review", "reject"} <= decisions


def test_golden_review_export_favors_the_decision_boundary_window(tmp_path: Path) -> None:
    engine, story_release_id = _seed_review_sample_release(tmp_path)

    golden = export_golden_candidates(
        engine,
        story_release_id,
        output_format="review",
        sample=40,
        seed=13,
    )

    pairs = golden["pairs"]
    assert len(pairs) == 40
    boundary = [pair for pair in pairs if 0.45 <= pair["score"] <= 0.65]
    assert len(boundary) / len(pairs) >= 0.5


def test_golden_review_export_reserves_voices_mainstream_quota(tmp_path: Path) -> None:
    engine, story_release_id = _seed_review_sample_release(tmp_path)

    golden = export_golden_candidates(
        engine,
        story_release_id,
        output_format="review",
        sample=40,
        seed=13,
    )

    cross_pairs = [
        pair
        for pair in golden["pairs"]
        if {pair["source_cluster_a"], pair["source_cluster_b"]} == {"voices", "mainstream"}
    ]
    # 8 voices<->mainstream review pairs were seeded; round(40 * 0.15) = 6 is the
    # mandatory floor reserved up front, independent of how the score-boundary
    # stratification lands (it may pick up the rest too — the quota is a floor, not
    # a cap, so >= 6 is the contract, not an exact count).
    assert 6 <= len(cross_pairs) <= 8


def test_golden_review_export_is_deterministic_for_a_fixed_seed(tmp_path: Path) -> None:
    engine, story_release_id = _seed_review_sample_release(tmp_path)

    first = export_golden_candidates(
        engine, story_release_id, output_format="review", sample=40, seed=7
    )
    second = export_golden_candidates(
        engine, story_release_id, output_format="review", sample=40, seed=7
    )
    different_seed = export_golden_candidates(
        engine, story_release_id, output_format="review", sample=40, seed=8
    )

    assert first["pairs"] == second["pairs"]
    assert [pair["pair_id"] for pair in first["pairs"]] != [
        pair["pair_id"] for pair in different_seed["pairs"]
    ]


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
    _add_required_cluster_coverage(corpus)
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
    _add_required_cluster_coverage(corpus)
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


def test_label_sources_resolve_by_trust(tmp_path: Path) -> None:
    """Метка более доверенного источника перекрывает менее доверенную для той же пары.

    Порядок: human > claude_review > qwen_review > auto_label. Источник читается как
    префикс ``note`` до двоеточия, поэтому обоснование метки не теряется.
    """
    engine = engine_db(tmp_path / "trend_engine.db")

    def add(target: str, label: str, note: str) -> None:
        label_engine_target(
            engine,
            target_kind="story_pair",
            target_id=target,
            release_id="stories_v1",
            label=label,  # type: ignore[arg-type]
            note=note,
        )

    # Одна пара размечена всеми четырьмя источниками, каждый со своим вердиктом.
    add("a|b", "different_story", "auto_label")
    add("a|b", "different_story", "qwen_review: разные компании")
    add("a|b", "same_story", "claude_review: один и тот же отчёт")
    add("a|b", "same_story", "разобрал руками")
    # Остальные пары покрыты только частью источников.
    add("c|d", "same_story", "claude_review: общий первоисточник")
    add("c|d", "different_story", "auto_label")
    add("e|f", "different_story", "qwen_review")
    add("g|h", "same_story", "auto_label")

    resolved = resolve_pair_labels(engine, "stories_v1")
    labels, composition, leading = resolved.labels, resolved.composition, resolved.leading

    assert labels["a|b"] == "same_story", "человеческая метка обязана победить"
    assert labels["c|d"] == "same_story", "claude_review сильнее auto_label"
    assert labels["e|f"] == "different_story"
    assert labels["g|h"] == "same_story"
    assert leading == "human"
    assert composition == {
        "human": 1,
        "claude_review": 2,
        "qwen_review": 2,
        "auto_label": 3,
    }


def test_leading_label_source_flags_circular_labels(tmp_path: Path) -> None:
    """Пока метки только авто-разметочные, оценка качества сравнивает правила с собой."""
    engine = engine_db(tmp_path / "trend_engine.db")
    label_engine_target(
        engine,
        target_kind="story_pair",
        target_id="a|b",
        release_id="stories_v1",
        label="same_story",
        note="auto_label",
    )
    leading = resolve_pair_labels(engine, "stories_v1").leading
    assert leading == "auto_label"

    label_engine_target(
        engine,
        target_kind="story_pair",
        target_id="c|d",
        release_id="stories_v1",
        label="different_story",
        note="claude_review: разные события",
    )
    leading_after = resolve_pair_labels(engine, "stories_v1").leading
    assert leading_after == "claude_review"


def test_golden_import_stamps_label_source(tmp_path: Path) -> None:
    """``--note claude_review`` помечает всю партию, обоснование остаётся в хвосте."""
    engine = engine_db(tmp_path / "trend_engine.db")
    engine.execute(
        """INSERT INTO story_releases
           (story_release_id, facet_release_id, method, params_hash, status,
            metrics_json, git_sha, created_at)
           VALUES ('stories_v1', 'facets_v1', 'hybrid_v2', 'h', 'evaluated', '{}', '', 'now')"""
    )
    engine.commit()

    import_golden_labels(
        engine,
        {
            "story_release_id": "stories_v1",
            "pairs": [
                {"item_id_a": "a", "item_id_b": "b", "label": "same_story", "note": "один отчёт"}
            ],
        },
        source="claude_review",
    )
    row = engine.execute("SELECT note FROM engine_labels").fetchone()
    assert row["note"] == "claude_review: один отчёт"

    resolved = resolve_pair_labels(engine, "stories_v1")
    composition, leading = resolved.composition, resolved.leading
    assert leading == "claude_review"
    assert composition == {"claude_review": 1}

    with pytest.raises(ValueError, match="Unknown label source"):
        import_golden_labels(engine, {"story_release_id": "stories_v1"}, source="nonsense")


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

    # Все 30 пар решены правилами детерминированно, поэтому авто-метки на них —
    # пересказ самих правил. Обучаться на них нельзя, и отказ должен быть явным.
    with pytest.raises(ValueError, match="No informative labeled pairs"):
        train_story_merge_model(engine, "stories_ml")

    # Серая зона — единственное место, где метка добавляет информацию.
    grey = []
    for i in range(15):
        grey.append(
            ("stories_ml", f"g{i}", f"h{i}", 0.6, "review", json.dumps(positive()), "ambiguous")
        )
        grey.append(
            ("stories_ml", f"m{i}", f"n{i}", 0.55, "review", json.dumps(negative()), "ambiguous")
        )
    engine.executemany(
        """INSERT INTO story_candidate_pairs
           (story_release_id, item_id_a, item_id_b, score, decision, features_json, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        grey,
    )
    for i in range(15):
        for left, right, label in (
            (f"g{i}", f"h{i}", "same_story"),
            (f"m{i}", f"n{i}", "different_story"),
        ):
            label_engine_target(
                engine,
                target_kind="story_pair",
                target_id=f"{left}|{right}",
                release_id="stories_ml",
                label=label,  # type: ignore[arg-type]
                note="claude_review: размечено в серой зоне",
            )
    engine.commit()

    trained = train_story_merge_model(engine, "stories_ml")
    # Источники меток названы так же, как их ``note`` — один словарь вместо двух.
    assert trained["label_source"] == "claude_review"
    assert trained["labeled_pairs"] == 30, "в обучение идёт только серая зона"
    assert trained["label_composition"] == {"claude_review": 30}
    assert trained["model"]["tautological_labels_dropped"] == 30
    assert trained["model"]["labels_are_circular"] is False
    model = trained["model"]
    assert model["precision_at_threshold"] >= 0.95
    assert model["model_hash"]

    row = engine.execute(
        "SELECT metrics_json FROM story_releases WHERE story_release_id = 'stories_ml'"
    ).fetchone()
    stored = json.loads(row["metrics_json"])["merge_model"]
    assert stored["model_hash"] == model["model_hash"]

    # Человеческая метка имеет приоритет над машинной на той же паре.
    label_engine_target(
        engine,
        target_kind="story_pair",
        target_id="g0|h0",
        release_id="stories_ml",
        label="different_story",
        note="manual",
    )
    retrained = train_story_merge_model(engine, "stories_ml")
    assert retrained["label_source"] == "human"
    assert retrained["label_composition"] == {"human": 1, "claude_review": 29}


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


def _add_required_cluster_coverage(conn: sqlite3.Connection) -> None:
    """Make a synthetic broad release complete without weakening its gates.

    Прод-профиль обязан покрыть кластеры, у источников которых в реестре объявлен
    ``expected_min_items > 0`` — сегодня это ``voices`` (reddit) и ``developers``
    (hackernews). Синтетический корпус состоит из reuters/nytimes, поэтому оба
    обязательных кластера досыпаются здесь.
    """
    items: list[ContentItem] = []
    observations: list[Observation] = []
    coverage: tuple[tuple[str, SourceCluster, str], ...] = (
        ("reddit", "voices", "Reddit discussion for"),
        ("hackernews", "developers", "Hacker News thread for"),
    )
    for run_id, date in zip(_run_ids(), ("2026-07-27", "2026-07-28", "2026-07-29"), strict=True):
        for provider, cluster, title_prefix in coverage:
            item = ContentItem(
                item_id=f"{provider}_{date}",
                provider=provider,
                source_cluster=cluster,
                external_id=f"{provider}_{date}",
                canonical_url=f"https://{provider}.example/{date}",
                title=f"{title_prefix} {date}",
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


def test_run_engine_cycle_can_publish_opted_in_partial_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicitly opted-in shadow publication may expose a partial corpus.

    Production channels remain protected by ``publish_radar``.  This test keeps
    the cycle orchestration aligned with that lower-level contract, so preview
    releases do not need a manual post-cycle publication workaround.
    """
    corpus_path = tmp_path / "compass.db"
    corpus = _seed_cycle_corpus(corpus_path)
    upsert_run(
        corpus,
        run_id="2026-07-29:broad",
        snapshot_date="2026-07-29",
        profile="broad",
        status="partial",
        started_at="2026-07-29T07:00:00Z",
        finished_at="2026-07-29T07:10:00Z",
    )
    corpus.commit()
    engine = engine_db(tmp_path / "trend_engine.db")
    import reddit_compass.intelligence.quality as quality_module
    from reddit_compass.intelligence.quality import FloorResult

    # The tiny fixture intentionally cannot satisfy corpus-size floors.  The
    # contract under test is only the partial-shadow routing after quality has
    # passed; production gate coverage lives in publish_radar tests.
    monkeypatch.setattr(
        quality_module,
        "evaluate_floors",
        lambda _metrics: [
            FloorResult(
                metric="test",
                value=0.0,
                floor=0.0,
                op="max",
                passed=True,
                desc="test-only passed floor",
            )
        ],
    )
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
                publish_channel="shadow",
                allow_partial=True,
                pulse=False,
            )
        )
    finally:
        corpus.close()
        engine.close()

    assert result["quality"]["passed"] is True
    assert result["publication_id"]
    assert result["publication_blocked_reason"] == ""


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


def test_medoid_threshold_is_a_release_parameter(tmp_path: Path) -> None:
    """Порог ребра до медоида настраивается и попадает в params релиза.

    Он был жёстко зашит как 0.72 — выше всей серой зоны (score 0.45–0.65), поэтому
    пары, поднятые ревью или merge-моделью до auto_merge, всё равно отсекались при
    сборке групп. Разметка и обучение при этом не влияли на результат вовсе.
    """
    from reddit_compass.intelligence.engine import (
        DEFAULT_MEDOID_MIN_SCORE,
        PairCandidate,
        _story_generation_params,
        _valid_group_against_medoid,
    )

    def pair(score: float) -> PairCandidate:
        return PairCandidate(
            item_id_a="a",
            item_id_b="b",
            score=score,
            decision="auto_merge",
            reason="test",
            features={},
        )

    grey_zone = {("a", "b"): pair(0.60)}
    assert not _valid_group_against_medoid(["a", "b"], "a", grey_zone, min_score=0.72)
    assert _valid_group_against_medoid(["a", "b"], "a", grey_zone, min_score=0.55)

    # Дефолт должен пропускать середину серой зоны, иначе слой ревью бессмысленен.
    assert DEFAULT_MEDOID_MIN_SCORE < 0.65
    assert _valid_group_against_medoid(["a", "b"], "a", grey_zone)

    # Значение обязано лежать в params до вычисления params_hash — релиз воспроизводим.
    assert _story_generation_params()["medoid_min_score"] == DEFAULT_MEDOID_MIN_SCORE
    assert _story_generation_params({"medoid_min_score": 0.7})["medoid_min_score"] == 0.7


def test_library_trend_method_matches_production() -> None:
    """Дефолт библиотеки обязан совпадать с тем, что реально считает ночной прогон.

    Расхождение было не косметическим: на одном и том же story-релизе (4 957 items)
    `story_graph_v1` дал 6 трендов с 5 негодными именами и одним дублем — то есть ронял
    полы качества, — а `embedding_v2` дал 109 трендов и полы имён прошёл.
    """
    from reddit_compass.intelligence.engine import DEFAULT_TREND_METHOD

    assert DEFAULT_TREND_METHOD == "embedding_v2"
