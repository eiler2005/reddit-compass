"""SQLite-хранилище: аддитивно к JSONL, для исторических запросов и API.

Файл: data/compass.db. JSONL остаётся форматом обмена; SQLite — для запросов
«все посты по AI за месяц», «топ по score за неделю», «тренды по неделям».
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from .models import PostCard, TrackedThreadState, ViralitySignal

logger = logging.getLogger("reddit_compass")

DEFAULT_DB_PATH = Path("data/compass.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    posts_count INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'reddit'
);

CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id),
    post_id         TEXT NOT NULL,
    subreddit       TEXT NOT NULL,
    title           TEXT NOT NULL,
    author          TEXT NOT NULL DEFAULT '[deleted]',
    score           INTEGER NOT NULL DEFAULT 0,
    num_comments    INTEGER NOT NULL DEFAULT 0,
    upvote_ratio    REAL NOT NULL DEFAULT 0.0,
    url             TEXT NOT NULL DEFAULT '',
    permalink       TEXT NOT NULL DEFAULT '',
    selftext        TEXT NOT NULL DEFAULT '',
    monitoring_type TEXT NOT NULL DEFAULT 'hot',
    keyword         TEXT,
    source          TEXT NOT NULL DEFAULT 'reddit',
    created_utc     TEXT,
    UNIQUE(snapshot_id, post_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_rowid  INTEGER NOT NULL REFERENCES posts(id),
    comment_id  TEXT NOT NULL,
    author      TEXT NOT NULL DEFAULT '[deleted]',
    score       INTEGER NOT NULL DEFAULT 0,
    body        TEXT NOT NULL DEFAULT '',
    is_submitter INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS virality_signals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id      INTEGER NOT NULL REFERENCES snapshots(id),
    post_id          TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    signal_type      TEXT NOT NULL,
    total_score      INTEGER NOT NULL DEFAULT 0,
    total_comments   INTEGER NOT NULL DEFAULT 0,
    original_subreddit TEXT NOT NULL DEFAULT '',
    crossposted_to   TEXT NOT NULL DEFAULT '[]',
    detected_at      TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tracked_threads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id         INTEGER NOT NULL REFERENCES snapshots(id),
    url                 TEXT NOT NULL,
    post_id             TEXT NOT NULL,
    subreddit           TEXT NOT NULL DEFAULT '',
    title               TEXT NOT NULL DEFAULT '',
    score               INTEGER NOT NULL DEFAULT 0,
    num_comments        INTEGER NOT NULL DEFAULT 0,
    last_checked        TEXT NOT NULL DEFAULT '',
    new_comments        INTEGER NOT NULL DEFAULT 0,
    score_delta         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_posts_snapshot ON posts(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts(subreddit);
CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score DESC);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_rowid);
CREATE INDEX IF NOT EXISTS idx_signals_snapshot ON virality_signals(snapshot_id);
"""


def get_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Открывает (и мигрирует) SQLite-базу."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def save_snapshot(
    conn: sqlite3.Connection,
    snapshot_date: str,
    cards: list[PostCard],
    signals: list[ViralitySignal] | None = None,
    threads: list[TrackedThreadState] | None = None,
    source: str = "reddit",
) -> int:
    """Сохраняет snapshot в SQLite. Возвращает snapshot_id."""
    # Проверяем, существует ли snapshot для этой даты
    existing = conn.execute("SELECT id FROM snapshots WHERE date = ?", (snapshot_date,)).fetchone()
    if existing:
        snapshot_id: int = existing[0]
        conn.execute(
            "UPDATE snapshots SET posts_count = ?, source = ? WHERE id = ?",
            (len(cards), source, snapshot_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO snapshots (date, posts_count, source) VALUES (?, ?, ?)",
            (snapshot_date, len(cards), source),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        snapshot_id = new_id

    for card in cards:
        cur = conn.execute(
            """INSERT OR IGNORE INTO posts
               (snapshot_id, post_id, subreddit, title, author, score, num_comments,
                upvote_ratio, url, permalink, selftext, monitoring_type, keyword,
                source, created_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                card.post_id,
                card.subreddit,
                card.title,
                card.author,
                card.score,
                card.num_comments,
                card.upvote_ratio,
                card.url,
                card.permalink,
                card.selftext,
                card.monitoring_type,
                card.keyword,
                source,
                card.created_utc,
            ),
        )
        post_rowid = cur.lastrowid
        if post_rowid and card.top_comments:
            for c in card.top_comments:
                conn.execute(
                    """INSERT INTO comments
                       (post_rowid, comment_id, author, score, body, is_submitter)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (post_rowid, c.comment_id, c.author, c.score, c.body, int(c.is_submitter)),
                )

    for sig in signals or []:
        conn.execute(
            """INSERT INTO virality_signals
               (snapshot_id, post_id, title, signal_type, total_score, total_comments,
                original_subreddit, crossposted_to, detected_at, url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                sig.post_id,
                sig.title,
                sig.signal_type,
                sig.total_score,
                sig.total_comments,
                sig.original_subreddit,
                json.dumps(sig.crossposted_to, ensure_ascii=False),
                sig.detected_at,
                sig.url,
            ),
        )

    for t in threads or []:
        conn.execute(
            """INSERT INTO tracked_threads
               (snapshot_id, url, post_id, subreddit, title, score, num_comments,
                last_checked, new_comments, score_delta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                t.url,
                t.post_id,
                t.subreddit,
                t.title,
                t.score,
                t.num_comments,
                t.last_checked,
                t.new_comments_since_last,
                t.score_delta,
            ),
        )

    conn.commit()
    logger.info(
        "SQLite: snapshot %s сохранён (%d постов, %d сигналов, %d тредов)",
        snapshot_date,
        len(cards),
        len(signals or []),
        len(threads or []),
    )
    return snapshot_id


def query_snapshots(conn: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
    """Список snapshots (последние N)."""
    rows = conn.execute(
        "SELECT id, date, created_at, posts_count, source"
        " FROM snapshots ORDER BY date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def query_posts(
    conn: sqlite3.Connection,
    date: str | None = None,
    subreddit: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Запрос постов с фильтрами."""
    sql = "SELECT * FROM posts WHERE 1=1"
    params: list[Any] = []

    if date:
        sql += " AND snapshot_id IN (SELECT id FROM snapshots WHERE date = ?)"
        params.append(date)
    if subreddit:
        sql += " AND subreddit = ?"
        params.append(subreddit)
    if source:
        sql += " AND source = ?"
        params.append(source)

    sql += " ORDER BY score DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_signals(conn: sqlite3.Connection, date: str | None = None) -> list[dict[str, Any]]:
    """Virality signals for a date."""
    if date:
        rows = conn.execute(
            """SELECT vs.* FROM virality_signals vs
               JOIN snapshots s ON vs.snapshot_id = s.id WHERE s.date = ?
               ORDER BY vs.total_score DESC""",
            (date,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM virality_signals ORDER BY total_score DESC LIMIT 100"
        ).fetchall()
    return [dict(r) for r in rows]


def query_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Агрегированная статистика."""
    total_snapshots = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    total_signals = conn.execute("SELECT COUNT(*) FROM virality_signals").fetchone()[0]
    top_subreddits = conn.execute(
        """SELECT subreddit, COUNT(*) as cnt, AVG(score) as avg_score
           FROM posts GROUP BY subreddit ORDER BY cnt DESC LIMIT 10"""
    ).fetchall()
    latest = conn.execute("SELECT MAX(date) FROM snapshots").fetchone()[0]

    return {
        "total_snapshots": total_snapshots,
        "total_posts": total_posts,
        "total_signals": total_signals,
        "latest_snapshot": latest,
        "top_subreddits": [dict(r) for r in top_subreddits],
    }
