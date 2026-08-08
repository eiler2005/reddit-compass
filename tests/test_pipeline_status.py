"""Полоса конвейера: факт отдельно от расписания.

Схемы берутся у самого кода (`get_db`, `engine_db`), а не создаются рядом руками.
Вчерашний дефект `/health` был ровно таким: тест строил таблицу `publications`,
которой в движке не существует, был зелёным, а на бою проверка молча деградировала.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from reddit_compass.api.pipeline_status import (
    STATE_DONE,
    STATE_LATE,
    STATE_PENDING,
    pipeline_status,
)
from reddit_compass.db import get_db
from reddit_compass.intelligence.engine import engine_db
from reddit_compass.intelligence.migrations import migrate

TODAY = "2026-08-08"
MID_CYCLE = datetime(2026, 8, 8, 16, 18, tzinfo=UTC)
BEFORE_ENGINE = datetime(2026, 8, 8, 15, 30, tzinfo=UTC)
LONG_AFTER = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)


def _corpus(tmp_path: Path, *, status: str = "complete") -> object:
    conn = get_db(tmp_path / "compass.db")
    # `runs` и `source_health` заводит миграция схемы сбора, а не базовый `get_db`.
    migrate(conn)
    conn.execute(
        "INSERT INTO runs (run_id, snapshot_date, profile, status, started_at, finished_at,"
        " schema_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"{TODAY}:broad",
            TODAY,
            "broad",
            status,
            f"{TODAY}T13:00:02Z",
            f"{TODAY}T15:15:03Z",
            "2",
        ),
    )
    # Сводные строки адаптеров и построчные ленты лежат в одной таблице и дают одну и ту
    # же сумму каждая — как на бою 8 августа. Полоса обязана считать только сводные.
    for source, count in (("reddit", 2076), ("rss", 536), ("hackernews", 230)):
        conn.execute(
            "INSERT INTO source_health (run_id, source_id, provider, cluster, status, count)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (f"{TODAY}:broad", source, source, "news", "ok", count),
        )
    for feed, count in (("bbc:business", 14), ("arstechnica:top", 20), ("reddit:tech", 2808)):
        conn.execute(
            "INSERT INTO source_health (run_id, source_id, provider, cluster, status, count)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (f"{TODAY}:broad", feed, feed.split(":")[0], "news", "ok", count),
        )
    conn.commit()
    return conn


def _engine_with(tmp_path: Path, *, stories: bool = False, trends: bool = False) -> object:
    conn = engine_db(tmp_path / "trend_engine.db")
    conn.execute(
        "INSERT INTO data_releases (release_id, profile, dates_json, run_ids_json,"
        " source_db_path, source_db_checksum, input_checksum, input_status,"
        " source_coverage_json, config_hash, status, item_count, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "release_x",
            "broad",
            "[]",
            "[]",
            "/data/compass.db",
            "c",
            "c",
            "complete",
            "{}",
            "h",
            "finalized",
            3060,
            f"{TODAY}T16:00:02Z",
        ),
    )
    if stories:
        conn.execute(
            "INSERT INTO story_releases (story_release_id, facet_release_id, method,"
            " params_hash, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("stories_x", "facets_x", "schema_v3", "h", "evaluated", f"{TODAY}T16:00:15Z"),
        )
    if trends:
        conn.execute(
            "INSERT INTO trend_releases (trend_release_id, story_release_id, method, window,"
            " params_hash, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("trends_x", "stories_x", "schema_v3", "7d", "h", "evaluated", f"{TODAY}T16:26:00Z"),
        )
    conn.commit()
    return conn


def test_finished_collection_is_a_fact_with_its_own_time(tmp_path: Path) -> None:
    report = pipeline_status(None, _corpus(tmp_path), now=BEFORE_ENGINE)
    collect = report["stages"][0]
    assert collect["state"] == STATE_DONE
    assert collect["at"] == "15:15"
    # Только сводные строки: ленты дублируют ту же сумму и удвоили бы её читателю.
    assert "2842 материалов" in collect["detail"]
    assert "источников 3/3" in collect["detail"]


def test_mid_cycle_shows_done_stages_and_never_claims_work_in_progress(tmp_path: Path) -> None:
    """Середина цикла видна по артефактам: Data и Stories есть, Trends ещё нет.

    Замер на живом прогоне 8 августа в 16:18 показал ровно такую картину. Незавершённая
    стадия обязана остаться ожиданием: изнутри БД неотличимо, идёт она прямо сейчас или
    упала минуту назад, и выдавать одно за другое нельзя.
    """
    report = pipeline_status(_engine_with(tmp_path, stories=True), _corpus(tmp_path), now=MID_CYCLE)
    by_key = {stage["key"]: stage for stage in report["stages"]}

    assert by_key["freeze"]["state"] == STATE_DONE
    assert by_key["stories"]["state"] == STATE_DONE
    assert by_key["trends"]["state"] == STATE_PENDING
    assert by_key["publish"]["state"] == STATE_PENDING
    assert all(stage["state"] != "running" for stage in report["stages"])


def test_overdue_stage_is_late_not_pending(tmp_path: Path) -> None:
    """Через три часа после срока «ожидается» превращается в ложь."""
    report = pipeline_status(_engine_with(tmp_path), _corpus(tmp_path), now=LONG_AFTER)
    by_key = {stage["key"]: stage for stage in report["stages"]}
    assert by_key["trends"]["state"] == STATE_LATE
    assert by_key["publish"]["state"] == STATE_LATE


def test_yesterday_artifacts_do_not_colour_today_green(tmp_path: Path) -> None:
    """Отсчёт от полуночи UTC: вчерашний успех не выдаётся за сегодняшний."""
    conn = engine_db(tmp_path / "trend_engine.db")
    conn.execute(
        "INSERT INTO data_releases (release_id, profile, dates_json, run_ids_json,"
        " source_db_path, source_db_checksum, input_checksum, input_status,"
        " source_coverage_json, config_hash, status, item_count, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "release_old",
            "broad",
            "[]",
            "[]",
            "/data/compass.db",
            "c",
            "c",
            "complete",
            "{}",
            "h",
            "finalized",
            10,
            "2026-08-07T16:00:00Z",
        ),
    )
    conn.commit()

    report = pipeline_status(conn, _corpus(tmp_path), now=MID_CYCLE)
    assert {s["key"]: s["state"] for s in report["stages"]}["freeze"] == STATE_PENDING


def test_manual_broad_switch_is_stated_plainly(tmp_path: Path) -> None:
    report = pipeline_status(_engine_with(tmp_path), _corpus(tmp_path), now=MID_CYCLE)
    assert "вручную" in report["channel_note"]
