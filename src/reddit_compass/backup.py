"""Резервные копии: то, что нельзя пересчитать, и то, что дорого пересчитывать.

Копировать всё подряд — неправильный ответ. `trend_engine.db` весит 2.9 ГБ, и
подавляющая часть его объёма — производные таблицы релизов, которые пересчитываются из
двух дешёвых слоёв. Ежедневная копия такого размера стоит места и времени, а защищает
ровно то же, что копия на 70 МБ.

Слои по цене восстановления:

* **`compass.db` (~66 МБ) — невосстановим.** Прошлые окна Reddit и новостных лент
  пересобрать нельзя: их больше нет ни в одном публичном API. Утрата окончательна;
* **кэши LLM (`story_schemas`, `llm_reviews`, `actor_aliases`)** — восстановимы, но за
  деньги: это оплаченные ответы модели. Меняются медленно, поэтому копия еженедельная;
* **релизы, публикации, членства** — производные от первых двух. Не копируются.

Отдельно `qwen_usage.db`: он крошечный, но по нему считается потолок расхода. Потеря
леджера означает, что охранник прочитает пустую историю как «ничего не потрачено».

Копии кладутся **вне** тома с данными: внутри него они разделили бы судьбу оригинала.
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DAILY_DATABASES = ("compass.db", "qwen_usage.db")
CACHE_TABLES = ("story_schemas", "llm_reviews", "actor_aliases")
CACHE_SOURCE = "trend_engine.db"
KEEP_DAILY = 14
KEEP_WEEKLY = 8


@dataclass(frozen=True)
class BackupResult:
    path: Path
    source: str
    bytes_written: int
    rows: int = 0


def _gzip_into(source: Path, target: Path) -> int:
    with source.open("rb") as raw, gzip.open(target, "wb", compresslevel=6) as packed:
        shutil.copyfileobj(raw, packed)
    return target.stat().st_size


def copy_database(source: Path, dest_dir: Path) -> BackupResult:
    """Снимок целой базы через ``VACUUM INTO``.

    ``VACUUM INTO`` читает согласованный снимок под read-транзакцией, поэтому копия
    снимается с работающей базы и не ждёт остановки конвейера. Простое копирование
    файла такой гарантии не даёт: ночной прогон пишет в те же файлы, и копия пришлась
    бы на середину транзакции.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{source.stem}.db.gz"
    conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        with tempfile.TemporaryDirectory(dir=str(dest_dir)) as staging:
            plain = Path(staging) / source.name
            conn.execute("VACUUM INTO ?", (str(plain),))
            written = _gzip_into(plain, target)
    finally:
        conn.close()
    return BackupResult(path=target, source=source.name, bytes_written=written)


def copy_cache_tables(
    source: Path,
    dest_dir: Path,
    tables: tuple[str, ...] = CACHE_TABLES,
) -> BackupResult:
    """Только оплаченные ответы модели, без производных таблиц релизов."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / "llm_cache.db.gz"
    rows = 0
    with tempfile.TemporaryDirectory(dir=str(dest_dir)) as staging:
        plain = Path(staging) / "llm_cache.db"
        conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            conn.execute("ATTACH DATABASE ? AS backup", (str(plain),))
            for table in tables:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
                ).fetchone()
                if exists is None:
                    continue
                conn.execute(f"CREATE TABLE backup.{table} AS SELECT * FROM main.{table}")
                rows += int(conn.execute(f"SELECT COUNT(*) FROM backup.{table}").fetchone()[0])
            conn.commit()
            conn.execute("DETACH DATABASE backup")
        finally:
            conn.close()
        written = _gzip_into(plain, target)
    return BackupResult(path=target, source=source.name, bytes_written=written, rows=rows)


def prune(parent: Path, keep: int) -> list[Path]:
    """Оставляет ``keep`` самых свежих датированных каталогов.

    Удаляются только каталоги вида ``YYYY-MM-DD``: если оператор положил рядом что-то
    своё, ротация это не тронет.
    """
    if not parent.exists():
        return []
    dated = sorted(
        (path for path in parent.iterdir() if path.is_dir() and _is_dated(path.name)),
        reverse=True,
    )
    removed = []
    for path in dated[keep:]:
        shutil.rmtree(path)
        removed.append(path)
    return removed


def _is_dated(name: str) -> bool:
    try:
        datetime.strptime(name, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def run_backup(
    data_dir: Path,
    dest_root: Path,
    *,
    now: datetime | None = None,
    weekly: bool | None = None,
) -> dict[str, object]:
    """Ежедневный снимок невосстановимого плюс еженедельный — оплаченного."""
    moment = now or datetime.now(UTC)
    stamp = moment.strftime("%Y-%m-%d")
    # Понедельник: кэши меняются медленно, ежедневная копия 15 тысяч ответов не окупает
    # места. Явный аргумент оставлен, чтобы это можно было запустить руками.
    do_weekly = moment.weekday() == 0 if weekly is None else weekly

    results: list[BackupResult] = []
    daily_dir = dest_root / "daily" / stamp
    for name in DAILY_DATABASES:
        source = data_dir / name
        if source.exists():
            results.append(copy_database(source, daily_dir))

    weekly_result: BackupResult | None = None
    if do_weekly and (data_dir / CACHE_SOURCE).exists():
        weekly_result = copy_cache_tables(data_dir / CACHE_SOURCE, dest_root / "weekly" / stamp)
        results.append(weekly_result)

    pruned = prune(dest_root / "daily", KEEP_DAILY) + prune(dest_root / "weekly", KEEP_WEEKLY)
    return {
        "date": stamp,
        "weekly": do_weekly,
        "files": [
            {"source": item.source, "path": str(item.path), "bytes": item.bytes_written}
            for item in results
        ],
        "total_bytes": sum(item.bytes_written for item in results),
        "cache_rows": weekly_result.rows if weekly_result else 0,
        "pruned": [str(path) for path in pruned],
    }
