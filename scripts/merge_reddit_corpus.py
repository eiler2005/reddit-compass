"""Safe merge of a locally-collected Reddit-only compass.db into the VPS corpus DB.

Usage: python rc_merge_reddit.py <target_db> <source_db> <run_id>

Uses ATTACH so we never overwrite the target DB. Only Reddit items are merged
(filtered by provider), plus observations and source_health for the given run_id.
All inserts are OR IGNORE / OR REPLACE, so re-runs are idempotent and the VPS's
mainstream rows (collected separately) are untouched.
"""

import sqlite3
import sys


def main() -> None:
    target, source, run_id = sys.argv[1], sys.argv[2], sys.argv[3]
    conn = sqlite3.connect(target)
    conn.execute("ATTACH DATABASE ? AS src", (source,))

    conn.execute(
        "INSERT OR IGNORE INTO main.items SELECT * FROM src.items WHERE provider = 'reddit'"
    )
    n_items = conn.execute("SELECT changes()").fetchone()[0]

    conn.execute(
        "INSERT OR IGNORE INTO main.observations SELECT * FROM src.observations WHERE run_id = ?",
        (run_id,),
    )
    n_obs = conn.execute("SELECT changes()").fetchone()[0]

    main_tables = {
        r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")
    }
    src_tables = {
        r[0] for r in conn.execute("SELECT name FROM src.sqlite_master WHERE type='table'")
    }
    n_health = 0
    if "source_health" in main_tables and "source_health" in src_tables:
        conn.execute(
            "INSERT OR REPLACE INTO main.source_health "
            "SELECT * FROM src.source_health WHERE run_id = ?",
            (run_id,),
        )
        n_health = conn.execute("SELECT changes()").fetchone()[0]

    conn.commit()
    conn.close()
    print(f"merged run_id={run_id} items={n_items} obs={n_obs} health={n_health}")


if __name__ == "__main__":
    main()
