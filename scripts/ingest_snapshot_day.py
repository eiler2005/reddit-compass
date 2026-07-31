"""Re-ingest a day's collected JSONL snapshots into the corpus compass.db.

Idempotent (upserts). Used when the collector wrote JSONL but the corpus DB row for
the day is missing (e.g. Reddit collected on a Mac and merged in via the snapshot dir).
Run inside the container so /data paths resolve; mount the host dir at /hosttmp to drop
in a Reddit posts.jsonl collected elsewhere.

Usage (inside container): python ingest_snapshot_day.py <snapshot_date> [profile]
"""

import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from reddit_compass.intelligence.compat import load_legacy_jsonl
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import Observation
from reddit_compass.intelligence.repository import (
    upsert_items,
    upsert_observations,
    upsert_run,
)

FILE_MAP = {
    "reddit": "posts.jsonl",
    "hackernews": "hackernews.jsonl",
    "rss": "rss.jsonl",
    "ladder": "ladder.jsonl",
    "producthunt": "producthunt.jsonl",
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def main() -> None:
    date = sys.argv[1]
    profile = sys.argv[2] if len(sys.argv) > 2 else "broad"
    snap_dir = Path("/data/snapshots") / date
    snap_dir.mkdir(parents=True, exist_ok=True)

    host_reddit = Path("/hosttmp/posts_reddit_30.jsonl")
    if host_reddit.exists():
        shutil.copy(host_reddit, snap_dir / "posts.jsonl")

    conn = sqlite3.connect("/data/compass.db")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    run_id = f"{date}:{profile}"
    started = now_iso()
    upsert_run(
        conn,
        run_id=run_id,
        snapshot_date=date,
        profile=profile,
        status="running",
        started_at=started,
    )
    conn.commit()

    total = 0
    for source, fname in FILE_MAP.items():
        path = snap_dir / fname
        if not path.exists():
            continue
        items, _ = load_legacy_jsonl(path, fname, now_iso())
        if not items:
            continue
        upsert_items(conn, items)
        observations = [
            Observation(
                run_id=run_id,
                item_id=it.item_id,
                observed_at=it.observed_at or now_iso(),
            )
            for it in items
        ]
        upsert_observations(conn, observations)
        conn.commit()
        total += len(items)
        print(f"  ingested {source}: {len(items)}")

    upsert_run(
        conn,
        run_id=run_id,
        snapshot_date=date,
        profile=profile,
        status="complete",
        started_at=started,
        finished_at=now_iso(),
    )
    conn.commit()
    conn.close()
    print(f"DONE run_id={run_id} total_items={total}")


if __name__ == "__main__":
    main()
