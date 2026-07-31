"""API contracts for immutable Trend Engine publications."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from reddit_compass.api.app import create_app
from reddit_compass.api.ui import _build_today_reading_list, _today_change_candidates
from reddit_compass.db import get_db
from reddit_compass.intelligence.engine import engine_db, store_quality_report
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import SourceHealth
from reddit_compass.intelligence.quality import FloorResult
from reddit_compass.intelligence.repository import save_source_health, upsert_run


@pytest.fixture
def engine_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    corpus_path = tmp_path / "compass.db"
    corpus_conn = get_db(corpus_path)
    migrate(corpus_conn)
    corpus_conn.close()

    engine_path = tmp_path / "trend_engine.db"
    conn = engine_db(engine_path)
    created_at = "2026-07-29T10:00:00Z"
    conn.execute(
        """
        INSERT INTO data_releases (
            release_id, profile, dates_json, run_ids_json, source_db_path,
            source_db_checksum, input_checksum, input_status, source_coverage_json,
            item_count, observation_count, status, created_at, finalized_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "data_test",
            "broad",
            '["2026-07-29"]',
            '["run_test"]',
            str(corpus_path),
            "source-checksum",
            "input-checksum",
            "complete",
            '{"rss:world": 1}',
            1,
            1,
            "building",
            created_at,
            "",
        ),
    )
    conn.execute(
        """
        INSERT INTO release_items (
            release_id, item_id, provider, source_cluster, external_id,
            canonical_url, title, snapshot_date, source_section, domain_ids,
            row_checksum
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "data_test",
            "rss:1",
            "reuters",
            "mainstream",
            "1",
            "https://example.com/story",
            "A verified story",
            "2026-07-29",
            "world",
            '["world_geopolitics"]',
            "row-checksum",
        ),
    )
    conn.execute(
        """
        INSERT INTO release_items (
            release_id, item_id, provider, source_cluster, external_id,
            canonical_url, title, snapshot_date, source_section, domain_ids,
            discussion_url, target_url, raw_engagement, metadata, row_checksum
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "data_test",
            "zreddit:1",
            "reddit",
            "voices",
            "1",
            "https://www.reddit.com/r/news/comments/1/pulse",
            "Pulse story",
            "2026-07-29",
            "news",
            '["society_politics"]',
            "javascript:alert(1)",
            "https://example.com/story",
            '{"score": 100, "comments": 40, "upvote_ratio": 0.91}',
            '{"is_self": false}',
            "reddit-row-checksum",
        ),
    )
    conn.execute(
        """
        INSERT INTO facet_releases (
            facet_release_id, data_release_id, method, params_hash,
            status, metrics_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "facet_test",
            "data_test",
            "test",
            "params",
            "evaluated",
            "{}",
            created_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO item_facets (
            facet_release_id, item_id, domain_ids, theme_ids
        ) VALUES (?, ?, ?, ?)
        """,
        ("facet_test", "rss:1", '["world_geopolitics"]', '["geopolitics"]'),
    )
    conn.execute(
        """
        INSERT INTO story_releases (
            story_release_id, facet_release_id, method, params_hash,
            status, metrics_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "story_test",
            "facet_test",
            "hybrid_test",
            "params",
            "published",
            '{"story_count": 1}',
            created_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO engine_stories (
            story_release_id, story_id, canonical_key, title, domain_ids,
            project_scores, first_seen, last_seen, confidence, source_count, item_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "story_test",
            "story_1",
            "verified-story",
            "A verified story",
            '["world_geopolitics"]',
            '{"rbc": 88, "book": 67}',
            "2026-07-27",
            "2026-07-29",
            "high",
            2,
            3,
        ),
    )
    conn.execute(
        """
        INSERT INTO engine_story_items (
            story_release_id, story_id, item_id, membership_score, membership_reason
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("story_test", "story_1", "rss:1", 1.0, "exact_url"),
    )
    conn.execute(
        """
        INSERT INTO engine_story_items (
            story_release_id, story_id, item_id, membership_score, membership_reason
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("story_test", "story_1", "zreddit:1", 0.95, "shared canonical/target URL"),
    )
    conn.execute(
        """
        INSERT INTO trend_releases (
            trend_release_id, story_release_id, window, method, params_hash,
            status, history_status, metrics_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "trend_test",
            "story_test",
            "30d",
            "story_graph_test",
            "params",
            "published",
            "ready",
            '{"trend_count": 1}',
            created_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO engine_trends (
            trend_release_id, trend_id, name_ru, pattern, domain_ids,
            confidence, lifecycle, source_scope, first_seen, last_seen,
            story_count, source_count, project_scores, evidence_story_ids,
            review_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "trend_test",
            "trend_1",
            "Проверяемый тренд",
            "Три независимых события",
            '["world_geopolitics"]',
            0.91,
            "growing",
            "cross_source",
            "2026-07-20",
            "2026-07-29",
            3,
            4,
            '{"rbc": 92, "book": 71}',
            '["story_1"]',
            "confirmed",
        ),
    )
    conn.execute(
        """
        INSERT INTO engine_trend_stories (
            trend_release_id, trend_id, story_id, membership_score, reason
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("trend_test", "trend_1", "story_1", 0.97, "shared_pattern"),
    )
    conn.execute(
        """
        INSERT INTO signal_releases (
            signal_release_id, data_release_id, facet_release_id, story_release_id,
            date, method, params_hash, metrics_json, git_sha, status,
            signal_count, created_at, finalized_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "signals_test",
            "data_test",
            "facet_test",
            "story_test",
            "2026-07-29",
            "reddit_pulse_v2",
            "params",
            "{}",
            "test-sha",
            "finalized",
            1,
            "2026-07-29T10:00:00Z",
            "2026-07-29T10:00:00Z",
        ),
    )
    conn.execute(
        """
        INSERT INTO signal_releases (
            signal_release_id, data_release_id, facet_release_id, story_release_id,
            date, method, params_hash, metrics_json, git_sha, status,
            signal_count, created_at, finalized_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "signals_wrong_date",
            "data_test",
            "facet_test",
            "story_test",
            "2026-07-28",
            "reddit_pulse_v2",
            "params",
            "{}",
            "test-sha",
            "finalized",
            1,
            "2026-07-30T10:00:00Z",
            "2026-07-30T10:00:00Z",
        ),
    )
    conn.execute(
        """
        INSERT INTO community_signals (
            signal_release_id, signal_id, item_id, subreddit, signal_type,
            title, discussion_url, pulse_score, domain_ids_json,
            linked_story_id, mainstream_coverage_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "signals_test",
            "pulse_zreddit:1",
            "zreddit:1",
            "news",
            "policy_politics",
            "Pulse story",
            "javascript:alert(1)",
            77.0,
            '["society_politics"]',
            "story_1",
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO community_signals (
            signal_release_id, signal_id, item_id, subreddit, signal_type,
            title, discussion_url, pulse_score, domain_ids_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "signals_wrong_date",
            "pulse_wrong",
            "zreddit:1",
            "news",
            "policy_politics",
            "Wrong date pulse",
            "https://reddit.com/r/news/comments/wrong",
            99.0,
            '["society_politics"]',
        ),
    )
    conn.execute(
        """
        UPDATE data_releases
        SET status = 'finalized', finalized_at = ?
        WHERE release_id = 'data_test'
        """,
        (created_at,),
    )
    conn.execute(
        """
        INSERT INTO radar_publications (
            publication_id, channel, data_release_id, story_release_id,
            trend_release_id, input_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "publication_test",
            "broad",
            "data_test",
            "story_test",
            "trend_test",
            "complete",
            created_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO radar_publications (
            publication_id, channel, data_release_id, story_release_id,
            trend_release_id, input_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "publication_shadow",
            "shadow",
            "data_test",
            "story_test",
            "trend_test",
            "complete",
            created_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO published_channels (channel, current_publication_id, updated_at)
        VALUES (?, ?, ?)
        """,
        ("broad", "publication_test", created_at),
    )
    conn.execute(
        """
        INSERT INTO published_channels (channel, current_publication_id, updated_at)
        VALUES (?, ?, ?)
        """,
        ("shadow", "publication_shadow", created_at),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("RC_DB_PATH", str(corpus_path))
    monkeypatch.setenv("RC_ENGINE_DB_PATH", str(engine_path))
    with TestClient(create_app()) as client:
        yield client


def test_radar_reads_only_current_publication(engine_client: TestClient) -> None:
    response = engine_client.get("/api/v2/radar/2026-07-29?channel=broad")

    assert response.status_code == 200
    payload = response.json()
    assert payload["publication_id"] == "publication_test"
    assert payload["data_release_id"] == "data_test"
    assert payload["story_release_id"] == "story_test"
    assert payload["trend_release_id"] == "trend_test"
    assert payload["history_status"] == "ready"
    assert payload["shelves"]["growing"][0]["trend_id"] == "trend_1"
    assert payload["candidate_count"] == 1
    assert payload["confirmed_count"] == 1


def test_radar_rubric_uses_member_story_domains_not_broad_trend_array(
    engine_client: TestClient,
) -> None:
    """A broad legacy trend domain array must not make every tab identical."""
    engine_path = Path(os.environ["RC_ENGINE_DB_PATH"])
    conn = engine_db(engine_path)
    conn.execute(
        "UPDATE engine_trends SET domain_ids = ? WHERE trend_release_id = ? AND trend_id = ?",
        ('["ai_technology", "business_markets", "world_geopolitics"]', "trend_test", "trend_1"),
    )
    conn.commit()
    conn.close()

    ai_response = engine_client.get("/api/v2/radar/2026-07-29?channel=broad&domain=ai_technology")
    world_response = engine_client.get(
        "/api/v2/radar/2026-07-29?channel=broad&domain=world_geopolitics"
    )

    assert ai_response.status_code == 200
    assert world_response.status_code == 200
    assert ai_response.json()["shelves"] == {}
    assert world_response.json()["shelves"]["growing"][0]["trend_id"] == "trend_1"


def test_radar_labels_published_pending_rows_as_candidates(
    engine_client: TestClient,
) -> None:
    engine_path = Path(os.environ["RC_ENGINE_DB_PATH"])
    conn = engine_db(engine_path)
    conn.execute(
        "UPDATE engine_trends SET review_status = 'pending' WHERE trend_release_id = ?",
        ("trend_test",),
    )
    conn.commit()
    conn.close()

    response = engine_client.get("/runs/2026-07-29/radar?channel=broad")

    assert response.status_code == 200
    assert "Кандидаты trendwatching" in response.text
    assert "Подтверждено: 0" in response.text


def test_unpublished_engine_pages_show_latest_evaluated_preview(
    engine_client: TestClient,
) -> None:
    engine_path = Path(os.environ["RC_ENGINE_DB_PATH"])
    conn = engine_db(engine_path)
    conn.execute("DELETE FROM published_channels")
    conn.execute("DELETE FROM radar_publications")
    conn.commit()
    conn.close()

    radar_response = engine_client.get("/api/v2/radar/2026-07-29?channel=broad")
    trends_response = engine_client.get("/api/v2/engine/trends?channel=broad")
    trends_page = engine_client.get("/trends")
    radar_page = engine_client.get("/runs/2026-07-29/radar?channel=broad")
    today_page = engine_client.get("/today")
    changes_response = engine_client.get("/ui/today-changes?date=2026-07-29")

    assert radar_response.status_code == 200
    assert radar_response.json()["preview"] is True
    assert radar_response.json()["publication_id"] == ""
    assert radar_response.json()["trend_release_id"] == "trend_test"
    assert trends_response.status_code == 200
    assert trends_response.json()["preview"] is True
    assert trends_response.json()["items"][0]["trend_id"] == "trend_1"
    assert trends_page.status_code == 200
    assert "Preview mode" in trends_page.text
    assert "Проверяемый тренд" in trends_page.text
    assert radar_page.status_code == 200
    assert "Кандидаты trendwatching" in radar_page.text
    assert "Кандидатов в release: 1" in radar_page.text
    assert "pending" in radar_page.text or "confirmed" in radar_page.text
    assert today_page.status_code == 200
    assert changes_response.status_code == 200
    assert "Preview mode" in today_page.text
    assert changes_response.json()["items"][0]["title"] == "Проверяемый тренд"
    assert changes_response.json()["items"][0]["url"] == "/trends/trend_1?channel=broad"
    assert "Качество и ограничения" in today_page.text
    assert "Куда идти дальше" in today_page.text


def test_engine_today_dashboard_is_clickable_and_informative(
    engine_client: TestClient,
) -> None:
    response = engine_client.get("/today")
    reading_response = engine_client.get("/ui/today-reading?date=2026-07-29")
    changes_response = engine_client.get("/ui/today-changes?date=2026-07-29")

    assert response.status_code == 200
    assert reading_response.status_code == 200
    assert changes_response.status_code == 200
    assert "Сводка выпуска" in response.text
    assert "trend-кандидатов" in response.text
    assert "Качество и ограничения" in response.text
    assert "Что прочитать сегодня" in response.text
    assert "Тематический срез" in response.text
    assert "Куда идти дальше" in response.text
    assert 'href="/news?channel=broad"' in response.text
    assert 'href="/stories?channel=broad&amp;domain=world_geopolitics"' in response.text
    assert 'src="/static/today_reading.js?v=20260731.2"' in response.text
    assert 'data-server-rendered="true"' in response.text
    assert "data-reading-item" in response.text
    assert 'href="https://example.com/story"' in response.text
    assert reading_response.json()["items"][0]["primary_url"] == "https://example.com/story"
    assert changes_response.json()["items"][0]["title"] == "Проверяемый тренд"
    assert "evidence_story_ids" not in changes_response.json()["items"][0]
    assert set(changes_response.json()["items"][0]) == {
        "url",
        "title",
        "pattern",
        "lifecycle_label",
        "source_scope_label",
        "source_scope",
        "review_label",
        "confidence_pct",
        "source_count",
        "story_count",
    }
    assert "javascript:alert" not in response.text
    assert "javascript:alert" not in reading_response.text


def test_today_hides_unreviewed_or_unusable_trend_candidates() -> None:
    radar = SimpleNamespace(
        shelves={
            "growing": [
                {
                    "trend_id": "pending_bad",
                    "title": "my ai job me",
                    "review_status": "pending",
                    "lifecycle": "growing",
                },
                {
                    "trend_id": "confirmed_bad",
                    "title": "ai agent",
                    "review_status": "confirmed",
                    "lifecycle": "growing",
                },
                {
                    "trend_id": "confirmed_good",
                    "title": "Новая проверяемая тенденция",
                    "review_status": "confirmed",
                    "lifecycle": "growing",
                    "confidence": 0.9,
                },
            ]
        }
    )

    cards = _today_change_candidates(radar, "channel=broad")

    assert [card["title"] for card in cards] == ["Новая проверяемая тенденция"]
    assert cards[0]["url"] == "/trends/confirmed_good?channel=broad"


def test_today_reading_list_prefers_article_and_dedupes_story(
    engine_client: TestClient,
) -> None:
    engine_path = Path(os.environ["RC_ENGINE_DB_PATH"])
    conn = engine_db(engine_path)
    try:
        items = _build_today_reading_list(
            conn,
            {
                "data_release_id": "data_test",
                "story_release_id": "story_test",
                "date": "2026-07-29",
            },
        )
    finally:
        conn.close()

    # The Reuters item and its Reddit discussion belong to one story.  The
    # reading queue exposes the article URL and does not render the same story
    # twice; a malicious discussion URL never becomes a link.
    assert len(items) == 1
    assert items[0]["primary_url"] == "https://example.com/story"
    assert items[0]["secondary_url"] == ""


def test_radar_keeps_previous_publication_for_new_date(
    engine_client: TestClient,
) -> None:
    response = engine_client.get("/api/v2/radar/2026-07-30?channel=broad")

    assert response.status_code == 200
    assert response.json()["serving_previous_publication"] is True


def test_engine_inspection_endpoints(engine_client: TestClient) -> None:
    releases = engine_client.get("/api/v2/engine/releases")
    publications = engine_client.get("/api/v2/engine/publications")
    story = engine_client.get("/api/v2/engine/story-releases/story_test")
    trend = engine_client.get("/api/v2/engine/trend-releases/trend_test")

    assert releases.status_code == 200
    assert releases.json()["data_releases"][0]["release_id"] == "data_test"
    assert publications.json()[0]["publication_id"] == "publication_test"
    assert story.status_code == 200
    assert trend.status_code == 200


def test_engine_ui_and_radar_use_publication(engine_client: TestClient) -> None:
    engine_page = engine_client.get("/engine")
    radar_page = engine_client.get("/runs/2026-07-29/radar")
    today_page = engine_client.get("/today")
    changes_response = engine_client.get("/ui/today-changes?date=2026-07-29")

    assert engine_page.status_code == 200
    assert "publication_test" in engine_page.text
    assert radar_page.status_code == 200
    assert "Проверяемый тренд" in radar_page.text
    assert "trend_test" in radar_page.text
    assert "Trendwatching cockpit" in radar_page.text
    assert "Reddit Pulse" in radar_page.text
    assert "Pulse story" in radar_page.text
    assert "Wrong date pulse" not in radar_page.text
    assert "javascript:alert" not in radar_page.text
    assert today_page.status_code == 200
    assert changes_response.json()["items"][0]["title"] == "Проверяемый тренд"
    assert "publication_test" in today_page.text


def test_runs_page_exposes_collection_to_publication_stages(engine_client: TestClient) -> None:
    corpus = get_db(Path(os.environ["RC_DB_PATH"]))
    upsert_run(
        corpus,
        run_id="run_test",
        snapshot_date="2026-07-29",
        profile="broad",
        status="complete",
        started_at="2026-07-29T09:00:00Z",
        finished_at="2026-07-29T10:00:00Z",
    )
    save_source_health(
        corpus,
        "run_test",
        [
            SourceHealth(
                source_id="rss",
                provider="rss",
                cluster="mainstream",
                status="ok",
                count=1,
            )
        ],
    )
    corpus.commit()
    corpus.close()

    engine = engine_db(Path(os.environ["RC_ENGINE_DB_PATH"]))
    try:
        # A newer facet-only attempt references the same collection run.  The
        # ledger must continue to show the current published full chain rather
        # than this incomplete attempt merely because it has a later timestamp.
        engine.execute(
            """
            INSERT INTO data_releases (
                release_id, profile, dates_json, run_ids_json, source_db_path,
                source_db_checksum, input_checksum, input_status, source_coverage_json,
                item_count, observation_count, status, created_at, finalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "data_pending",
                "broad",
                '["2026-07-29"]',
                '["run_test"]',
                "fixture.db",
                "source-checksum",
                "input-checksum",
                "complete",
                "{}",
                1,
                1,
                "finalized",
                "2026-07-30T10:00:00Z",
                "2026-07-30T10:00:00Z",
            ),
        )
        engine.execute(
            """
            INSERT INTO facet_releases (
                facet_release_id, data_release_id, method, params_hash,
                status, metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "facet_pending",
                "data_pending",
                "fixture",
                "params",
                "evaluated",
                "{}",
                "2026-07-30T10:00:00Z",
            ),
        )
        store_quality_report(
            engine,
            data_release_id="data_test",
            story_release_id="story_test",
            trend_release_id="trend_test",
            signal_release_id="signals_test",
            metrics={"stories_total": 1},
            floors=[
                FloorResult(
                    metric="stories_overmerge_ge5",
                    value=0,
                    floor=0,
                    op="max",
                    passed=True,
                    desc="fixture floor",
                )
            ],
        )
        engine.commit()
        assert (
            engine.execute(
                "SELECT passed FROM engine_quality_reports WHERE data_release_id = 'data_test'"
            ).fetchone()[0]
            == 1
        )
    finally:
        engine.close()

    response = engine_client.get("/runs")

    assert response.status_code == 200
    assert "Сбор источников" in response.text
    assert "Frozen Data Release" in response.text
    assert "Trends / Qwen" in response.text
    assert "Quality gate" in response.text
    assert "Publication" in response.text
    assert "publication_test" in response.text
    assert "все абсолютные полы пройдены" in response.text
    assert "результат не записан для этой версии" not in response.text
    assert "data_test" in response.text
    assert "data_pending" not in response.text


def test_reddit_pulse_api_filters_by_release_date_and_sanitizes_url(
    engine_client: TestClient,
) -> None:
    response = engine_client.get("/api/v2/reddit-pulse?data_release=data_test&date=2026-07-29")

    assert response.status_code == 200
    payload = response.json()
    assert payload["signal_release_id"] == "signals_test"
    assert payload["items"][0]["title"] == "Pulse story"
    assert payload["items"][0]["discussion_url"] == ""
    assert payload["items"][0]["reddit_score"] == 100
    assert payload["items"][0]["reddit_comments"] == 40

    summary = engine_client.get(
        "/api/v2/reddit-pulse/summary?data_release=data_test&date=2026-07-29"
    )
    assert summary.status_code == 200
    top_pulse = summary.json()["top_pulse"][0]
    assert top_pulse["title"] == "Pulse story"
    assert top_pulse["discussion_url"] == ""
    assert top_pulse["reddit_score"] == 100
    assert top_pulse["reddit_comments"] == 40


def test_published_news_stories_trends_and_project_lens_are_separate(
    engine_client: TestClient,
) -> None:
    news = engine_client.get("/api/v2/news?page_size=10")
    stories = engine_client.get("/api/v2/engine/stories?page_size=10")
    trends = engine_client.get("/api/v2/engine/trends?page_size=10")
    lens = engine_client.get("/api/v2/projects/rbc/lens?limit=10")

    assert news.status_code == 200
    assert stories.status_code == 200
    assert trends.status_code == 200
    assert lens.status_code == 200
    assert news.json()["items"][0]["item_id"] == "rss:1"
    assert news.json()["items"][0]["story_id"] == "story_1"
    assert stories.json()["items"][0]["story_id"] == "story_1"
    assert stories.json()["items"][0]["evidence_items"][0]["provider"] == "reuters"
    assert trends.json()["items"][0]["trend_id"] == "trend_1"
    assert trends.json()["items"][0]["stories"][0]["story_id"] == "story_1"
    assert lens.json()["trends"][0]["project_scores"]["rbc"] == 92
    assert lens.json()["stories"][0]["project_scores"]["rbc"] == 88


def test_published_story_and_trend_detail_endpoints(engine_client: TestClient) -> None:
    story = engine_client.get("/api/v2/engine/stories/story_1")
    trend = engine_client.get("/api/v2/engine/trends/trend_1")

    assert story.status_code == 200
    assert trend.status_code == 200
    assert story.json()["story_id"] == "story_1"
    assert story.json()["evidence_items"][0]["item_id"] == "rss:1"
    assert story.json()["trends"][0]["trend_id"] == "trend_1"
    assert trend.json()["trend_id"] == "trend_1"
    assert trend.json()["stories"][0]["story_id"] == "story_1"
    assert trend.json()["stories"][0]["evidence_items"][0]["provider"] == "reuters"


def test_published_trend_detail_limits_member_stories(engine_client: TestClient) -> None:
    engine_path = Path(os.environ["RC_ENGINE_DB_PATH"])
    conn = engine_db(engine_path)
    for index in range(25):
        story_id = f"story_extra_{index}"
        conn.execute(
            """
            INSERT INTO engine_stories (
                story_release_id, story_id, canonical_key, title, domain_ids,
                project_scores, first_seen, last_seen, confidence, source_count, item_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "story_test",
                story_id,
                story_id,
                f"Extra story {index}",
                '["world_geopolitics"]',
                '{"rbc": 10}',
                "2026-07-27",
                "2026-07-29",
                "medium",
                1,
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO engine_trend_stories (
                trend_release_id, trend_id, story_id, membership_score, reason
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("trend_test", "trend_1", story_id, 0.5, "test"),
        )
    conn.execute(
        "UPDATE engine_trends SET story_count = ? WHERE trend_release_id = ? AND trend_id = ?",
        (26, "trend_test", "trend_1"),
    )
    conn.commit()
    conn.close()

    trend = engine_client.get("/api/v2/engine/trends/trend_1")
    trend_detail = engine_client.get("/trends/trend_1")

    assert trend.status_code == 200
    assert len(trend.json()["stories"]) == 8
    assert trend_detail.status_code == 200
    assert "Показано 8 из 26 stories" in trend_detail.text


def test_published_layer_ui_pages_render(engine_client: TestClient) -> None:
    news = engine_client.get("/news")
    stories = engine_client.get("/stories")
    trends = engine_client.get("/trends")
    project = engine_client.get("/projects/rbc")
    story_detail = engine_client.get("/stories/story_1")
    trend_detail = engine_client.get("/trends/trend_1")

    assert news.status_code == 200
    assert "Сырой входящий корпус" in news.text
    assert "A verified story" in news.text
    assert stories.status_code == 200
    assert "Конкретные события" in stories.text
    assert trends.status_code == 200
    assert "Проверяемый тренд" in trends.text
    assert project.status_code == 200
    assert "Project Lens" in project.text
    assert story_detail.status_code == 200
    assert "Evidence items" in story_detail.text
    assert trend_detail.status_code == 200
    assert "Stories inside trend" in trend_detail.text


def test_published_layer_ui_preserves_shadow_channel(engine_client: TestClient) -> None:
    radar_redirect = engine_client.get("/radar?channel=shadow", follow_redirects=False)
    news = engine_client.get("/news?channel=shadow&publication_id=publication_shadow")
    stories = engine_client.get("/stories?channel=shadow&publication_id=publication_shadow")

    assert radar_redirect.status_code == 302
    assert radar_redirect.headers["location"] == "/runs/2026-07-29/radar?channel=shadow"
    assert news.status_code == 200
    assert 'name="channel" value="shadow"' in news.text
    assert 'name="publication_id" value="publication_shadow"' in news.text
    assert "/stories/story_1?channel=shadow&amp;publication_id=publication_shadow" in news.text
    assert stories.status_code == 200
    assert "/radar?channel=shadow&amp;publication_id=publication_shadow" in stories.text
