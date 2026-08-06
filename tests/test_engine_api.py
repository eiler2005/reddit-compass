"""API contracts for immutable Trend Engine publications."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from reddit_compass.api.app import create_app
from reddit_compass.api.dates import display_date
from reddit_compass.api.ui import _build_today_reading_list, _today_change_candidates
from reddit_compass.api.v2 import _sort_news_rows
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
            review_status, review_name_ru, counterpoints
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "trend_test",
            "trend_1",
            "verified trend pattern",
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
            "Проверяемый тренд",
            '["story_1"]',
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
    assert "Проверяемый тренд" in changes_response.text
    assert 'href="/trends/trend_1?channel=broad"' in changes_response.text
    assert "Качество и ограничения" in today_page.text
    assert "Куда идти дальше" in today_page.text


def test_engine_today_dashboard_is_clickable_and_informative(
    engine_client: TestClient,
) -> None:
    response = engine_client.get("/today")
    fresh_response = engine_client.get("/today?sort=fresh")
    reading_response = engine_client.get("/ui/today-reading?date=2026-07-29")
    changes_response = engine_client.get("/ui/today-changes?date=2026-07-29")

    assert response.status_code == 200
    assert fresh_response.status_code == 200
    assert reading_response.status_code == 200
    assert changes_response.status_code == 200
    # Вверху /today стоит связная строка, а не пять KPI-плиток: ни материалов,
    # ни trend-кандидатов, ни source clusters не отвечали на вопрос «что читать».
    assert "today-lede" in response.text
    assert "подтверждены больше чем одним источником" in response.text
    assert "kpi-num" not in response.text
    assert "Качество и ограничения" in response.text
    assert "Что прочитать сегодня" in response.text
    assert "Тематический срез" in response.text
    assert "Куда идти дальше" in response.text
    assert 'href="/news?channel=broad"' in response.text
    assert 'href="/stories?channel=broad&amp;domain=world_geopolitics"' in response.text
    # Версия в cache-buster меняется при каждой правке скрипта — пинить её значит
    # ломать тест на ровном месте. Проверяем, что скрипт подключён.
    assert 'src="/static/today_reading.js?v=' in response.text
    assert 'data-server-rendered="true"' in response.text
    assert "data-reading-item" in response.text
    assert "Порядок brief" in fresh_response.text
    assert "Свежее выше" in fresh_response.text
    assert "sort=fresh" in fresh_response.text
    assert 'href="https://example.com/story"' in response.text
    # Ленты отдают готовую разметку, а не JSON: карточка описана только в Jinja.
    assert 'href="https://example.com/story"' in reading_response.text
    assert "data-reading-item" in reading_response.text
    assert "Проверяемый тренд" in changes_response.text
    # Тяжёлые поля не должны утекать во фрагмент: ответ обязан оставаться ниже
    # лимита обратного прокси на маленькие ответы.
    assert "evidence_story_ids" not in changes_response.text
    assert len(changes_response.text) < 4096
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


def test_today_changes_sort_by_strength_then_last_evidence_date() -> None:
    radar = SimpleNamespace(
        shelves={
            "new": [
                {
                    "trend_id": "weaker_newer",
                    "title": "Более слабый, но новый",
                    "review_status": "confirmed",
                    "confidence": 0.80,
                    "source_count": 2,
                    "story_count": 2,
                    "first_seen": "2026-08-04",
                    "last_seen": "2026-08-05",
                },
                {
                    "trend_id": "tie_older",
                    "title": "Равный, старое доказательство",
                    "review_status": "confirmed",
                    "confidence": 0.90,
                    "source_count": 3,
                    "story_count": 4,
                    "first_seen": "2026-08-01",
                    "last_seen": "2026-08-03",
                },
            ],
            "stable": [
                {
                    "trend_id": "strong_older",
                    "title": "Сильный и подтверждённый",
                    "review_status": "confirmed",
                    "confidence": 0.98,
                    "source_count": 5,
                    "story_count": 6,
                    "first_seen": "2026-07-29",
                    "last_seen": "2026-08-02",
                },
                {
                    "trend_id": "tie_newer",
                    "title": "Равный, свежее доказательство",
                    "review_status": "confirmed",
                    "confidence": 0.90,
                    "source_count": 3,
                    "story_count": 4,
                    "first_seen": "2026-08-01",
                    "last_seen": "2026-08-05",
                },
            ],
        }
    )

    cards = _today_change_candidates(radar, "channel=broad")

    assert [card["title"] for card in cards] == [
        "Сильный и подтверждённый",
        "Равный, свежее доказательство",
        "Равный, старое доказательство",
        "Более слабый, но новый",
    ]
    assert cards[1]["first_seen"] == "2026-08-01"
    assert cards[1]["last_seen"] == "2026-08-05"


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


def test_actors_and_key_events_reach_the_screen(engine_client: TestClient) -> None:
    """Акторы и дети собираются в API — они обязаны и рисоваться.

    Регрессия целого класса: `distinct_actors` и `children` доезжали до `TrendOut`, но
    на странице тренда не рендерились вовсе, а акторы — нигде, кроме строки ребёнка.
    Нормализация акторов отдельным LLM-проходом при этом была видна только метрике.
    """
    engine_path = Path(os.environ["RC_ENGINE_DB_PATH"])
    conn = engine_db(engine_path)
    conn.execute(
        "UPDATE engine_trends SET distinct_actors = ? WHERE trend_id = 'trend_1'",
        ('["Bank of England", "LinkedIn", "DeepSeek"]',),
    )
    conn.execute(
        """
        INSERT INTO engine_trends (
            trend_release_id, trend_id, name_ru, pattern, domain_ids,
            confidence, lifecycle, source_scope, first_seen, last_seen,
            story_count, source_count, project_scores, evidence_story_ids,
            review_status, counterpoints, parent_trend_id, distinct_actors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "trend_test",
            "trend_child",
            "model releases in AI",
            "Дочернее событие",
            '["world_geopolitics"]',
            0.8,
            "growing",
            "cross_source",
            "2026-07-20",
            "2026-07-29",
            3,
            3,
            "{}",
            "[]",
            "pending",
            "[]",
            "trend_1",
            '["Mistral", "Qwen"]',
        ),
    )
    conn.commit()
    conn.close()

    listing = engine_client.get("/trends")
    detail = engine_client.get("/trends/trend_1")

    assert listing.status_code == 200
    assert "Bank of England" in listing.text
    assert "model releases in AI" in listing.text
    assert detail.status_code == 200
    assert "Bank of England" in detail.text
    assert "DeepSeek" in detail.text
    # Drill-down до сих пор существовал только в комментарии к API.
    assert "Key events" in detail.text
    assert "model releases in AI" in detail.text


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
    assert "Проверяемый тренд" in changes_response.text
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
    assert news.json()["items"][0]["item_id"] == "zreddit:1"
    assert news.json()["items"][0]["story_id"] == "story_1"
    assert stories.json()["items"][0]["story_id"] == "story_1"
    assert stories.json()["items"][0]["evidence_items"][0]["provider"] == "reuters"
    assert trends.json()["items"][0]["trend_id"] == "trend_1"
    assert trends.json()["items"][0]["stories"][0]["story_id"] == "story_1"
    assert lens.json()["trends"][0]["project_scores"]["rbc"] == 92
    assert lens.json()["stories"][0]["project_scores"]["rbc"] == 88


def test_published_lists_rank_strength_before_freshness_and_expose_dates(
    engine_client: TestClient,
) -> None:
    engine_path = Path(os.environ["RC_ENGINE_DB_PATH"])
    conn = engine_db(engine_path)
    try:
        created_at = "2026-08-05T10:00:00Z"
        conn.execute(
            """
            INSERT INTO data_releases (
                release_id, profile, dates_json, run_ids_json, source_db_path,
                source_db_checksum, input_checksum, input_status, source_coverage_json,
                item_count, observation_count, status, created_at, finalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "data_sort",
                "broad",
                '["2026-08-02", "2026-08-05"]',
                '["run_sort"]',
                str(engine_path),
                "source-checksum-sort",
                "input-checksum-sort",
                "complete",
                '{"rss:world": 2, "reddit:all": 1}',
                3,
                3,
                "building",
                created_at,
                "",
            ),
        )
        conn.executemany(
            """
            INSERT INTO release_items (
                release_id, item_id, provider, source_cluster, external_id,
                canonical_url, title, snapshot_date, published_at, source_section,
                domain_ids, raw_engagement, row_checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "data_sort",
                    "news:strong-old",
                    "reuters",
                    "mainstream",
                    "strong-old",
                    "https://example.com/strong-old",
                    "Сильная, но более ранняя новость",
                    "2026-08-02",
                    "2026-08-02T09:00:00Z",
                    "world",
                    '["world_geopolitics"]',
                    '{"score": 3, "comments": 1}',
                    "strong-old-checksum",
                ),
                (
                    "data_sort",
                    "news:weak-new",
                    "reuters",
                    "mainstream",
                    "weak-new",
                    "https://example.com/weak-new",
                    "Слабая, но свежая новость",
                    "2026-08-05",
                    "2026-08-05T09:00:00Z",
                    "world",
                    '["world_geopolitics"]',
                    '{"score": 999, "comments": 999}',
                    "weak-new-checksum",
                ),
                (
                    "data_sort",
                    "news:strong-copy",
                    "reuters",
                    "mainstream",
                    "strong-copy",
                    "https://example.com/strong-copy",
                    "Ещё один материал того же сильного сюжета",
                    "2026-08-03",
                    "2026-08-03T09:00:00Z",
                    "world",
                    '["world_geopolitics"]',
                    '{"score": 2, "comments": 0}',
                    "strong-copy-checksum",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO release_items (
                release_id, item_id, provider, source_cluster, external_id,
                canonical_url, title, snapshot_date, published_at, source_section,
                domain_ids, raw_engagement, row_checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "data_sort",
                "pulse:sort",
                "reddit",
                "voices",
                "pulse-sort",
                "https://reddit.example/pulse-sort",
                "Reddit signal with an explicit date",
                "2026-08-05",
                "2026-08-05T11:00:00Z",
                "news",
                '["world_geopolitics"]',
                '{"score": 100, "comments": 40}',
                "pulse-sort-checksum",
            ),
        )
        conn.execute(
            """
            INSERT INTO facet_releases (
                facet_release_id, data_release_id, method, params_hash,
                status, metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("facet_sort", "data_sort", "test", "params", "evaluated", "{}", created_at),
        )
        conn.execute(
            """
            INSERT INTO story_releases (
                story_release_id, facet_release_id, method, params_hash,
                status, metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("story_sort", "facet_sort", "hybrid_test", "params", "published", "{}", created_at),
        )
        conn.executemany(
            """
            INSERT INTO engine_stories (
                story_release_id, story_id, canonical_key, title, domain_ids,
                project_scores, first_seen, last_seen, confidence, source_count, item_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "story_sort",
                    "story_strong_old",
                    "strong-old",
                    "Сильный, но более ранний сюжет",
                    '["world_geopolitics"]',
                    '{"rbc": 80}',
                    "2026-07-30",
                    "2026-08-02",
                    "high",
                    5,
                    5,
                ),
                (
                    "story_sort",
                    "story_weak_new",
                    "weak-new",
                    "Слабый, но свежий сюжет",
                    '["world_geopolitics"]',
                    '{"rbc": 70}',
                    "2026-08-04",
                    "2026-08-05",
                    "high",
                    4,
                    100,
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO engine_story_items (
                story_release_id, story_id, item_id, membership_score, membership_reason
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("story_sort", "story_strong_old", "news:strong-old", 1.0, "exact_url"),
                ("story_sort", "story_strong_old", "news:strong-copy", 0.9, "semantic"),
                ("story_sort", "story_weak_new", "news:weak-new", 1.0, "exact_url"),
            ],
        )
        conn.execute(
            """
            INSERT INTO trend_releases (
                trend_release_id, story_release_id, window, method, params_hash,
                status, history_status, metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "trend_sort",
                "story_sort",
                "7d",
                "story_graph_test",
                "params",
                "published",
                "ready",
                "{}",
                created_at,
            ),
        )
        conn.executemany(
            """
            INSERT INTO engine_trends (
                trend_release_id, trend_id, name_ru, pattern, domain_ids,
                confidence, lifecycle, source_scope, first_seen, last_seen,
                story_count, source_count, project_scores, evidence_story_ids, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "trend_sort",
                    "trend_strong_old",
                    "Сильный, но ранний тренд",
                    "Проверяемый паттерн",
                    '["world_geopolitics"]',
                    0.99,
                    "stable",
                    "cross_source",
                    "2026-07-30",
                    "2026-08-02",
                    6,
                    5,
                    '{"rbc": 90}',
                    "[]",
                    "confirmed",
                ),
                (
                    "trend_sort",
                    "trend_tie_new",
                    "Равный, но свежий тренд",
                    "Проверяемый паттерн",
                    '["world_geopolitics"]',
                    0.91,
                    "stable",
                    "cross_source",
                    "2026-08-01",
                    "2026-08-05",
                    3,
                    4,
                    '{"rbc": 85}',
                    "[]",
                    "confirmed",
                ),
                (
                    "trend_sort",
                    "trend_tie_old",
                    "Равный, но старый тренд",
                    "Проверяемый паттерн",
                    '["world_geopolitics"]',
                    0.91,
                    "stable",
                    "cross_source",
                    "2026-08-01",
                    "2026-08-03",
                    3,
                    4,
                    '{"rbc": 85}',
                    "[]",
                    "confirmed",
                ),
            ],
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
                "signals_sort",
                "data_sort",
                "facet_sort",
                "story_sort",
                "2026-08-05",
                "reddit_pulse_v2",
                "params",
                "{}",
                "test-sha",
                "finalized",
                1,
                created_at,
                created_at,
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
                "signals_sort",
                "pulse_sort",
                "pulse:sort",
                "news",
                "policy_politics",
                "Reddit signal with an explicit date",
                "https://reddit.example/pulse-sort",
                77.0,
                '["world_geopolitics"]',
            ),
        )
        conn.execute(
            "UPDATE data_releases SET status = 'finalized', finalized_at = ? "
            "WHERE release_id = 'data_sort'",
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
                "publication_sort",
                "broad",
                "data_sort",
                "story_sort",
                "trend_sort",
                "complete",
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    publication = "channel=broad&publication_id=publication_sort"
    news = engine_client.get(f"/api/v2/news?{publication}&page_size=10").json()["items"]
    news_projection = engine_client.get(f"/api/v2/news?{publication}&page_size=10").json()
    all_news = engine_client.get(f"/api/v2/news?{publication}&page_size=10&view=items").json()
    fresh_news = engine_client.get(
        f"/api/v2/news?{publication}&page_size=10&provider=reuters&sort=fresh"
    ).json()["items"]
    stories = engine_client.get(f"/api/v2/engine/stories?{publication}&page_size=10").json()[
        "items"
    ]
    fresh_stories = engine_client.get(
        f"/api/v2/engine/stories?{publication}&page_size=10&sort=fresh"
    ).json()["items"]
    trends = engine_client.get(f"/api/v2/engine/trends?{publication}&page_size=10").json()["items"]
    fresh_trends = engine_client.get(
        f"/api/v2/engine/trends?{publication}&page_size=10&sort=fresh"
    ).json()["items"]
    pulse = engine_client.get("/api/v2/reddit-pulse?data_release=data_sort&date=2026-08-05").json()
    news_page = engine_client.get(f"/news?{publication}")
    trends_page = engine_client.get(f"/trends?{publication}")

    assert news[0]["item_id"] == "news:strong-old"
    assert news[0]["published_at"] == "2026-08-02T09:00:00Z"
    assert news[0]["story_source_count"] == 5
    assert news_projection["view"] == "stories"
    assert news_projection["total"] == 3
    assert news_projection["item_total"] == 4
    assert all_news["view"] == "items"
    assert all_news["total"] == 4
    assert {item["item_id"] for item in all_news["items"]} >= {
        "news:strong-old",
        "news:strong-copy",
    }
    assert fresh_news[0]["item_id"] == "news:weak-new"
    assert [story["story_id"] for story in stories[:2]] == [
        "story_strong_old",
        "story_weak_new",
    ]
    assert fresh_stories[0]["story_id"] == "story_weak_new"
    assert [trend["trend_id"] for trend in trends[:2]] == [
        "trend_strong_old",
        "trend_tie_new",
    ]
    assert trends[1]["last_seen"] == "2026-08-05"
    assert fresh_trends[0]["trend_id"] == "trend_tie_new"
    assert pulse["items"][0]["published_at"] == "2026-08-05T11:00:00Z"
    # Страница показывает день, а не отметку времени провайдера: в одной колонке рядом
    # стояли RFC 2822, ISO и ISO с микросекундами из отката на `observed_at`.
    assert "2026-08-02" in news_page.text
    assert "2026-08-02T09:00:00Z" not in news_page.text
    assert "По сюжетам" in news_page.text
    assert "Все материалы" in news_page.text
    assert "Сначала свежее" in news_page.text
    assert "даты: 2026-08-01 → 2026-08-05" in trends_page.text


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
    assert "один представитель на уже связанный сюжет" in news.text
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


def test_trend_ui_original_title_russian_subtitle_and_linked_chips(
    engine_client: TestClient,
) -> None:
    """Оригинальное имя — заголовок, русское имя ревью — подпись рядом;
    доменные чипы — ссылки на фильтр текущего слоя; counterpoints — ссылки на stories."""
    trends = engine_client.get("/trends")

    assert trends.status_code == 200
    assert "verified trend pattern" in trends.text
    assert "Проверяемый тренд" in trends.text
    assert "domain=world_geopolitics" in trends.text

    detail = engine_client.get("/trends/trend_1")

    assert detail.status_code == 200
    assert "verified trend pattern" in detail.text
    assert "Проверяемый тренд" in detail.text
    assert 'href="/stories/story_1?' in detail.text


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


def test_pulse_topic_clouds_carry_examples(engine_client: TestClient) -> None:
    """Название тематики без примеров не читается: «Прочее» ничего не объясняет."""
    from reddit_compass.api.ui import _pulse_topic_clouds
    from reddit_compass.intelligence.engine import open_engine_readonly

    conn = open_engine_readonly(Path(os.environ["RC_ENGINE_DB_PATH"]))
    try:
        clouds = _pulse_topic_clouds(conn, "signals_test")
    finally:
        conn.close()

    assert clouds, "у релиза есть сигналы — облака не должны быть пустыми"
    politics = next(c for c in clouds if c["signal_type"] == "policy_politics")
    assert politics["label"] == "Политика и регулирование", "сырой тип в интерфейсе не читается"
    assert politics["total"] >= 1
    assert politics["examples"], "облако без примеров бесполезно"


def test_pulse_links_view_renders_direct_post_links(engine_client: TestClient) -> None:
    """Клик по тематике даёт ссылки на посты, а не сетку карточек с метриками."""
    response = engine_client.get("/pulse?signal_type=policy_politics&view=links")

    assert response.status_code == 200
    assert "pulse-link-list" in response.text
    assert 'class="pulse-card"' not in response.text, "в режиме ссылок карточек быть не должно"
    # Небезопасная схема в discussion_url не должна доезжать до разметки.
    assert "javascript:alert" not in response.text


def test_today_new_reddit_respects_topic_and_subreddit_quotas() -> None:
    """Блок «Новое на Reddit» держит разнообразие квотами.

    Политика ограничена жёстче остальных тем: у policy_politics самый высокий средний
    pulse, и без отдельного потолка она вытесняет из блока всё остальное.
    """
    import sqlite3

    from reddit_compass.api.ui import (
        _NEW_REDDIT_PER_SUBREDDIT,
        _NEW_REDDIT_POLITICS_CAP,
        _build_today_reddit_new,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE community_signals (
            signal_release_id TEXT, item_id TEXT, subreddit TEXT, signal_type TEXT,
            title TEXT, discussion_url TEXT, target_url TEXT, pulse_score REAL
        );
        CREATE TABLE signal_releases (signal_release_id TEXT, data_release_id TEXT);
        CREATE TABLE release_items (
            release_id TEXT, item_id TEXT, published_at TEXT, raw_engagement TEXT
        );
        INSERT INTO signal_releases VALUES ('sig', 'data');
        """
    )
    # Десять политических постов с высшим pulse и по два из других тем.
    rows = [
        ("sig", f"p{i}", "politics", "policy_politics", f"P{i}", "", "", 99.0) for i in range(10)
    ]
    rows += [("sig", f"a{i}", f"sub{i}", "ai_capability", f"A{i}", "", "", 50.0) for i in range(4)]
    conn.executemany("INSERT INTO community_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.executemany(
        "INSERT INTO release_items VALUES ('data', ?, '2026-07-30T10:00:00Z', '{}')",
        [(row[1],) for row in rows],
    )
    conn.commit()

    posts = _build_today_reddit_new(conn, "sig", exclude_item_ids=set(), limit=20)
    politics = [p for p in posts if p["signal_type"] == "policy_politics"]

    assert len(politics) <= _NEW_REDDIT_POLITICS_CAP, "политика обязана быть ограничена"
    assert len(politics) < len(posts), "блок не должен состоять из одной темы"
    per_subreddit: dict[str, int] = {}
    for post in posts:
        key = str(post["subreddit"])
        per_subreddit[key] = per_subreddit.get(key, 0) + 1
    assert max(per_subreddit.values()) <= _NEW_REDDIT_PER_SUBREDDIT


def test_today_new_reddit_excludes_items_already_in_reading_list() -> None:
    """Блок не должен дублировать ленту чтения, стоящую выше на той же странице."""
    import sqlite3

    from reddit_compass.api.ui import _build_today_reddit_new

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE community_signals (
            signal_release_id TEXT, item_id TEXT, subreddit TEXT, signal_type TEXT,
            title TEXT, discussion_url TEXT, target_url TEXT, pulse_score REAL
        );
        CREATE TABLE signal_releases (signal_release_id TEXT, data_release_id TEXT);
        CREATE TABLE release_items (
            release_id TEXT, item_id TEXT, published_at TEXT, raw_engagement TEXT
        );
        INSERT INTO signal_releases VALUES ('sig', 'data');
        INSERT INTO community_signals VALUES ('sig','shown','a','question','Shown','','',90.0);
        INSERT INTO community_signals VALUES ('sig','fresh','b','question','Fresh','','',10.0);
        INSERT INTO release_items VALUES ('data','shown','2026-07-30T10:00:00Z','{}');
        INSERT INTO release_items VALUES ('data','fresh','2026-07-30T09:00:00Z','{}');
        """
    )
    conn.commit()

    posts = _build_today_reddit_new(conn, "sig", exclude_item_ids={"shown"})

    assert [p["item_id"] for p in posts] == ["fresh"]


def test_signal_type_labels_cover_every_canonical_type() -> None:
    """Каждый тип сигнала обязан иметь человекочитаемое название.

    Незакрытый тип уезжает в интерфейс сырым. На проде так вылезла крупнейшая
    тематика «news link» (494 сигнала): в локальных данных этого типа не было,
    и пропуск заметили только на живом релизе.
    """
    from typing import get_args

    from reddit_compass.api.ui import _SIGNAL_TYPE_LABELS
    from reddit_compass.intelligence.reddit_pulse import SignalType

    missing = set(get_args(SignalType)) - set(_SIGNAL_TYPE_LABELS)
    assert not missing, f"нет названий для типов: {sorted(missing)}"


def _seed_trend_children(engine_path: Path) -> None:
    """Рубрика `trend_1` с двумя конкретными событиями под ней.

    Сюжет `story_1` намеренно лежит и в рубрике, и в одном из событий — так его и
    раскладывает слой: состав родителя надмножество составов детей.
    """
    conn = engine_db(engine_path)
    for trend_id, name, actors in (
        ("trend_child_a", "Проверяемый тренд by companies", '["Anthropic", "TikTok"]'),
        ("trend_child_b", "Проверяемый тренд by countries", '["China", "US"]'),
    ):
        conn.execute(
            """
            INSERT INTO engine_trends (
                trend_release_id, trend_id, name_ru, pattern, domain_ids,
                confidence, lifecycle, source_scope, first_seen, last_seen,
                story_count, source_count, project_scores, evidence_story_ids,
                review_status, parent_trend_id, distinct_actors
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "trend_test",
                trend_id,
                name,
                "Три независимых события",
                '["world_geopolitics"]',
                0.80,
                "growing",
                "cross_source",
                "2026-07-20",
                "2026-07-29",
                3,
                2,
                "{}",
                '["story_1"]',
                "confirmed",
                "trend_1",
                actors,
            ),
        )
    conn.execute(
        """
        INSERT INTO engine_trend_stories (
            trend_release_id, trend_id, story_id, membership_score, reason
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("trend_test", "trend_child_a", "story_1", 0.95, "shared_pattern"),
    )
    conn.commit()
    conn.close()


def test_trend_list_shows_roots_with_children_inside_the_card(
    engine_client: TestClient,
) -> None:
    """Верхний уровень — рубрики; конкретные события живут внутри карточки.

    Плоский список из рубрик и их же детей ставил бы их рядом как равные, а это две
    формулировки одного и того же.
    """
    _seed_trend_children(Path(os.environ["RC_ENGINE_DB_PATH"]))

    payload = engine_client.get("/api/v2/engine/trends?channel=broad").json()

    assert [item["trend_id"] for item in payload["items"]] == ["trend_1"]
    children = payload["items"][0]["children"]
    assert {child["trend_id"] for child in children} == {"trend_child_a", "trend_child_b"}
    assert children[0]["distinct_actors"]


def test_trend_detail_exposes_its_children(engine_client: TestClient) -> None:
    _seed_trend_children(Path(os.environ["RC_ENGINE_DB_PATH"]))

    payload = engine_client.get("/api/v2/engine/trends/trend_1?channel=broad").json()

    assert {child["trend_id"] for child in payload["children"]} == {
        "trend_child_a",
        "trend_child_b",
    }


def test_story_detail_lists_the_most_specific_trend_only(engine_client: TestClient) -> None:
    """Сюжет лежит и в рубрике, и в её событии; показывать обе строки — дважды одно."""
    _seed_trend_children(Path(os.environ["RC_ENGINE_DB_PATH"]))

    payload = engine_client.get("/api/v2/engine/stories/story_1?channel=broad").json()

    trend_ids = {trend["trend_id"] for trend in payload["trends"]}
    assert trend_ids == {"trend_child_a"}


def test_radar_shelves_never_show_a_trend_next_to_its_own_child(
    engine_client: TestClient,
) -> None:
    """Иначе родитель и ребёнок занимают две карточки из пяти на Today."""
    _seed_trend_children(Path(os.environ["RC_ENGINE_DB_PATH"]))

    payload = engine_client.get("/api/v2/radar/2026-07-29?channel=broad").json()

    shelved = {row["trend_id"] for rows in payload["shelves"].values() for row in rows}
    assert shelved == {"trend_1"}


def test_trend_children_render_on_the_list_page(engine_client: TestClient) -> None:
    _seed_trend_children(Path(os.environ["RC_ENGINE_DB_PATH"]))

    response = engine_client.get("/trends")

    assert response.status_code == 200
    assert "Проверяемый тренд by companies" in response.text
    assert "Anthropic, TikTok" in response.text


def _news_row(item_id: str, published_at: str) -> dict[str, object]:
    """Строка News-проекции; `sqlite3.Row` здесь не нужен — сортировка читает по ключу."""
    return {
        "item_id": item_id,
        "published_at": published_at,
        "observed_at": "",
        "snapshot_date": "",
        "story_source_count": 1,
        "story_item_count": 1,
        "raw_engagement": "{}",
    }


def test_news_sort_orders_mixed_date_formats_chronologically() -> None:
    """Сортировка обязана идти по времени, а не по написанию даты.

    `published_at` не нормализован: Reddit и HN отдают ISO-8601, RSS — RFC 2822. В боевом
    релизе это 3219 против 1414 строк. Пока дата была пятым ключом `_news_strength`, до
    неё доходило только при совпадении четырёх предыдущих. `b048cdf` поднял её в первичный
    ключ `sort=fresh`, и лексикографика начала сортировать по названию дня недели:
    `W > T > S > M > F > "2"` — сначала все среды, затем вторники, и только после всех RSS
    шли 3219 материалов Reddit и HN независимо от свежести.
    """
    rows = [
        _news_row("iso-oldest", "2026-07-27T06:00:00Z"),
        _news_row("rfc-newest", "Wed, 29 Jul 2026 06:00:00 GMT"),
        _news_row("iso-middle", "2026-07-28T06:00:00Z"),
        _news_row("undated", ""),
    ]

    fresh = [str(row["item_id"]) for row in _sort_news_rows(rows, sort="fresh")]  # type: ignore[arg-type]
    oldest = [str(row["item_id"]) for row in _sort_news_rows(rows, sort="oldest")]  # type: ignore[arg-type]

    assert fresh[:3] == ["rfc-newest", "iso-middle", "iso-oldest"]
    assert oldest[:3] == ["iso-oldest", "iso-middle", "rfc-newest"]
    # Материал без даты — последний в обоих направлениях, а не первый по возрастанию.
    assert fresh[-1] == "undated"
    assert oldest[-1] == "undated"


def test_published_date_filter_collapses_three_formats_to_one_day() -> None:
    """В одной колонке стояли RFC 2822, ISO и ISO с микросекундами из отката `observed_at`."""
    assert display_date("Wed, 29 Jul 2026 06:59:37 GMT") == "2026-07-29"
    assert display_date("2026-07-29T06:59:37Z") == "2026-07-29"
    assert display_date("2026-07-27T21:19:31.983321Z") == "2026-07-27"
    # Испорченная дата у одного материала не имеет права ронять выдачу целиком.
    assert display_date("not a date") == ""
    assert display_date(None) == ""


def test_unknown_sort_is_rejected_instead_of_silently_coerced(engine_client: TestClient) -> None:
    """API обязан ответить 422, а не отдать другой порядок под видом запрошенного.

    `_safe_sort` молча приводил неизвестное значение к дефолту, и `?sort=freshness`
    (именно так параметр назван в части документации) возвращал порядок по силе, написав
    в ответе `"sort": "strength"`. Клиент получал не то, что просил, без признака ошибки.
    """
    for path in ("/api/v2/news", "/api/v2/engine/stories", "/api/v2/engine/trends"):
        response = engine_client.get(f"{path}?channel=shadow&sort=freshness")
        assert response.status_code == 422, path
        assert "freshness" in response.text

    assert engine_client.get("/api/v2/news?channel=shadow&view=collapsed").status_code == 422


def test_supported_sort_and_view_still_pass(engine_client: TestClient) -> None:
    """Валидация не должна закрыть законные значения."""
    for sort in ("strength", "fresh", "oldest", "engagement"):
        assert engine_client.get(f"/api/v2/news?channel=shadow&sort={sort}").status_code == 200
    for view in ("stories", "items"):
        assert engine_client.get(f"/api/v2/news?channel=shadow&view={view}").status_code == 200


def test_news_response_states_which_projection_it_returned(engine_client: TestClient) -> None:
    """`total` значит разное при разных `view` — ответ обязан говорить, какой отдан."""
    stories = engine_client.get("/api/v2/news?channel=shadow&view=stories").json()
    items = engine_client.get("/api/v2/news?channel=shadow&view=items").json()

    assert stories["view"] == "stories"
    assert items["view"] == "items"
    # При view=items схлопывания нет, поэтому total совпадает с числом сырых материалов.
    assert items["total"] == items["item_total"]
    assert stories["total"] <= stories["item_total"]


def test_today_sort_form_does_not_pin_the_reader_to_a_date(engine_client: TestClient) -> None:
    """Форма сортировки не имеет права закрепить читателя на текущем дне.

    В скрытое поле подставлялась `radar.date`, а она заполнена всегда (откат на максимум
    дат релиза). Поэтому первое же «Применить» превращало /today в /today?date=<тот день>,
    и читатель оставался на нём, не видя следующую публикацию.
    """
    page = engine_client.get("/today").text

    form = page.split('action="/today"', 1)[1].split("</form>", 1)[0]
    assert 'name="date"' not in form

    # Явно запрошенная дата, наоборот, обязана сохраняться при смене сортировки.
    pinned = engine_client.get("/today?date=2026-07-29").text
    pinned_form = pinned.split('action="/today"', 1)[1].split("</form>", 1)[0]
    assert 'name="date" value="2026-07-29"' in pinned_form


def test_runs_page_shows_the_calendar_coverage_strip(engine_client: TestClient) -> None:
    """Пропуск дня обязан быть виден на операционной странице, а не только в CLI.

    Раньше о нём узнавали ручным `collect --coverage` или живым SQL — то есть уже при
    разборе странного релиза.
    """
    page = engine_client.get("/runs")

    assert page.status_code == 200
    assert "Покрытие по дням" in page.text
    assert "coverage-strip" in page.text
    # Разрез по источникам виден на каждом дне: «4 из 5» не говорит, что чинить.
    assert "coverage-day-sources" in page.text


def test_runs_page_expands_publishers_behind_each_adapter(
    engine_client: TestClient, tmp_path: Path
) -> None:
    """`ladder` одной строкой «186 материалов» скрывает девять изданий, `rss` — двенадцать.

    Разрез по изданиям — это то место, где видно, что день потерял конкретное издание,
    а адаптер при этом отчитался `ok`.
    """
    corpus = get_db(tmp_path / "compass.db")
    upsert_run(
        corpus,
        run_id="2026-07-29:broad",
        snapshot_date="2026-07-29",
        profile="broad",
        status="complete",
        started_at="2026-07-29T07:00:00Z",
        finished_at="2026-07-29T08:00:00Z",
    )
    save_source_health(
        corpus,
        "2026-07-29:broad",
        [
            SourceHealth(
                source_id="ladder", provider="ladder", cluster="mainstream", status="ok", count=186
            ),
            SourceHealth(
                source_id="wired:tech", provider="wired", cluster="tech", status="ok", count=21
            ),
            SourceHealth(
                source_id="time:mainstream",
                provider="time",
                cluster="mainstream",
                status="ok",
                count=23,
            ),
        ],
    )
    corpus.commit()
    corpus.close()

    page = engine_client.get("/runs")

    assert page.status_code == 200
    assert "publisher-block" in page.text
    # Издания, давшие материал, названы поимённо…
    assert "wired" in page.text
    # …и те, которых не было, тоже: иначе пропуск остался бы незамеченным.
    assert "не дало материала" in page.text
