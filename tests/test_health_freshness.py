"""Свежесть данных в ``/health``: три состояния, а не два."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from reddit_compass.api.health import DEFAULT_MAX_DATA_AGE_HOURS, data_freshness
from reddit_compass.intelligence.engine import engine_db

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

# Имя таблицы публикаций проверяется отдельным тестом на настоящей схеме движка.
# Здесь оно повторено ради быстрых проверок арифметики возраста.
PUBLICATIONS_TABLE = "radar_publications"


def _conn_with_publication(created_at: str | None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"CREATE TABLE {PUBLICATIONS_TABLE} (publication_id TEXT, channel TEXT, created_at TEXT)"
    )
    if created_at is not None:
        conn.execute(
            f"INSERT INTO {PUBLICATIONS_TABLE} VALUES (?, ?, ?)",
            ("publication_test", "shadow", created_at),
        )
    conn.commit()
    return conn


def test_freshness_reads_the_real_engine_schema(tmp_path: Path) -> None:
    """Проверка обязана работать на схеме движка, а не на выдуманной рядом.

    Первая версия читала таблицу `publications`, которой не существует: настоящая
    называется `radar_publications`. Тесты на самодельной таблице были зелёными, а на
    боевой базе `/health` молча отвечал `unknown` — то есть ровно ту неизвестность,
    которую должен был устранить.
    """
    conn = engine_db(tmp_path / "trend_engine.db")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert PUBLICATIONS_TABLE in tables

    conn.execute(
        f"""INSERT INTO {PUBLICATIONS_TABLE}
            (publication_id, channel, data_release_id, story_release_id,
             trend_release_id, input_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            "publication_real",
            "shadow",
            "release_test",
            "stories_test",
            "trends_test",
            "complete",
            (NOW - timedelta(hours=2)).isoformat(),
        ),
    )
    conn.commit()

    report = data_freshness(conn, now=NOW)
    assert report["data_status"] == "ok"
    assert report["last_publication_id"] == "publication_real"


def test_fresh_publication_is_ok() -> None:
    conn = _conn_with_publication((NOW - timedelta(hours=4)).isoformat())
    assert data_freshness(conn, now=NOW)["data_status"] == "ok"


def test_single_missed_night_is_still_ok() -> None:
    """`flock -n` пропускает прогон, а не ставит в очередь — одиночный пропуск штатен.

    Порог обязан переживать одну пропущенную ночь, иначе сигнал начнёт срабатывать на
    нормальном поведении расписания и его перестанут читать.
    """
    conn = _conn_with_publication((NOW - timedelta(hours=26)).isoformat())
    assert data_freshness(conn, now=NOW)["data_status"] == "ok"
    assert DEFAULT_MAX_DATA_AGE_HOURS > 24


def test_two_missed_nights_are_stale() -> None:
    conn = _conn_with_publication((NOW - timedelta(hours=50)).isoformat())
    report = data_freshness(conn, now=NOW)
    assert report["data_status"] == "stale"
    assert report["age_hours"] == 50.0


def test_empty_database_is_unknown_not_stale() -> None:
    """Свежая установка и тестовая база не обязаны выглядеть аварией."""
    assert data_freshness(None, now=NOW)["data_status"] == "unknown"
    assert data_freshness(_conn_with_publication(None), now=NOW)["data_status"] == "unknown"


def test_missing_table_does_not_raise() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert data_freshness(conn, now=NOW)["data_status"] == "unknown"


def test_unparsable_timestamp_is_unknown() -> None:
    conn = _conn_with_publication("не дата")
    assert data_freshness(conn, now=NOW)["data_status"] == "unknown"
