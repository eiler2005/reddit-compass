"""Резервные копии: что копируется, что нет и что переживает ротацию."""

from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from reddit_compass.backup import (
    KEEP_DAILY,
    copy_cache_tables,
    copy_database,
    prune,
    run_backup,
)

MONDAY = datetime(2026, 8, 10, 17, 0, tzinfo=UTC)
TUESDAY = datetime(2026, 8, 11, 17, 0, tzinfo=UTC)


def _make_db(path: Path, rows: int = 5) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items (item_id TEXT, payload TEXT)")
    conn.executemany(
        "INSERT INTO items VALUES (?, ?)", [(f"item_{i}", "x" * 200) for i in range(rows)]
    )
    conn.commit()
    conn.close()


def _make_engine_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    for table in ("story_schemas", "llm_reviews", "actor_aliases"):
        conn.execute(f"CREATE TABLE {table} (key TEXT, value TEXT)")
        conn.executemany(f"INSERT INTO {table} VALUES (?, ?)", [(f"k{i}", "v") for i in range(3)])
    # Производная таблица: восстанавливается пересчётом, копировать её незачем.
    conn.execute("CREATE TABLE engine_trends (trend_id TEXT)")
    conn.execute("INSERT INTO engine_trends VALUES ('trend_1')")
    conn.commit()
    conn.close()


def test_copy_database_produces_a_readable_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "compass.db"
    _make_db(source, rows=20)

    result = copy_database(source, tmp_path / "out")

    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(result.path.read_bytes()))
    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 20
    conn.close()


def test_snapshot_is_taken_from_a_live_database(tmp_path: Path) -> None:
    """Копия снимается с открытой базы, а не требует остановки конвейера.

    Ночной прогон пишет в те же файлы. `VACUUM INTO` читает согласованный снимок под
    read-транзакцией; простое копирование файла пришлось бы на середину чужой записи.
    """
    source = tmp_path / "compass.db"
    _make_db(source)
    live = sqlite3.connect(source)
    live.execute("INSERT INTO items VALUES ('open_txn', 'y')")
    live.commit()

    result = copy_database(source, tmp_path / "out")

    assert result.bytes_written > 0
    live.close()


def test_cache_backup_takes_paid_answers_and_skips_derived_tables(tmp_path: Path) -> None:
    source = tmp_path / "trend_engine.db"
    _make_engine_db(source)

    result = copy_cache_tables(source, tmp_path / "weekly")

    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(result.path.read_bytes()))
    conn = sqlite3.connect(restored)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"story_schemas", "llm_reviews", "actor_aliases"}
    assert result.rows == 9
    conn.close()


def test_weekly_cache_runs_on_monday_only(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _make_db(data / "compass.db")
    _make_engine_db(data / "trend_engine.db")
    dest = tmp_path / "backups"

    monday = run_backup(data, dest, now=MONDAY)
    tuesday = run_backup(data, dest, now=TUESDAY)

    assert monday["weekly"] is True
    assert tuesday["weekly"] is False
    assert (dest / "weekly" / "2026-08-10" / "llm_cache.db.gz").exists()
    assert not (dest / "weekly" / "2026-08-11").exists()


def test_derived_engine_database_is_never_copied_whole(tmp_path: Path) -> None:
    """2.9 ГБ производных таблиц не попадают в ежедневную копию."""
    data = tmp_path / "data"
    data.mkdir()
    _make_db(data / "compass.db")
    _make_engine_db(data / "trend_engine.db")

    report = run_backup(data, tmp_path / "backups", now=TUESDAY)

    assert [item["source"] for item in report["files"]] == ["compass.db"]  # type: ignore[index,union-attr]


def test_prune_keeps_recent_and_ignores_foreign_directories(tmp_path: Path) -> None:
    parent = tmp_path / "daily"
    parent.mkdir()
    for day in range(1, KEEP_DAILY + 4):
        (parent / f"2026-07-{day:02}").mkdir()
    (parent / "operator-notes").mkdir()

    removed = prune(parent, KEEP_DAILY)

    survivors = {path.name for path in parent.iterdir()}
    assert len(removed) == 3
    assert "operator-notes" in survivors
    assert "2026-07-01" not in survivors
    assert f"2026-07-{KEEP_DAILY + 3:02}" in survivors
