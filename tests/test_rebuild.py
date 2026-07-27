"""Тесты rebuild из snapshots (intelligence/rebuild.py)."""

from __future__ import annotations

from pathlib import Path

from reddit_compass.db import get_db
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.rebuild import rebuild_from_snapshots
from reddit_compass.intelligence.repository import get_research_state, update_research_state
from reddit_compass.models import PostCard


def _make_card(post_id: str = "p1", subreddit: str = "artificial", **kw) -> PostCard:
    base = {
        "subreddit": subreddit,
        "post_id": post_id,
        "title": "Test post",
        "author": "tester",
        "created_utc": "2026-07-27T10:00:00Z",
        "score": 100,
        "upvote_ratio": 0.95,
        "num_comments": 42,
        "url": "https://example.com/article",
        "selftext": "",
        "link_flair_text": None,
        "is_self": False,
        "permalink": f"/r/{subreddit}/comments/{post_id}/test/",
        "monitoring_type": "hot",
        "snapshot_date": "2026-07-27",
    }
    base.update(kw)
    return PostCard(**base)


def _write_jsonl(path: Path, cards: list[PostCard]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(c.to_json() for c in cards), encoding="utf-8")


def test_rebuild_empty_dir(tmp_path: Path):
    conn = get_db(tmp_path / "test.db")
    migrate(conn)
    stats = rebuild_from_snapshots(conn, tmp_path / "snapshots")
    assert stats == {"dates": 0, "items": 0, "skipped": 0}
    conn.close()


def test_rebuild_single_date(tmp_path: Path):
    snap_dir = tmp_path / "snapshots" / "2026-07-27"
    cards = [_make_card(post_id=f"p{i}") for i in range(5)]
    _write_jsonl(snap_dir / "posts.jsonl", cards)

    conn = get_db(tmp_path / "test.db")
    migrate(conn)
    stats = rebuild_from_snapshots(conn, snap_dir.parent)

    assert stats["dates"] == 1
    assert stats["items"] == 5
    assert stats["skipped"] == 0

    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 5

    runs = conn.execute("SELECT * FROM runs").fetchall()
    assert len(runs) == 1
    assert runs[0]["snapshot_date"] == "2026-07-27"
    conn.close()


def test_rebuild_idempotent(tmp_path: Path):
    snap_dir = tmp_path / "snapshots" / "2026-07-27"
    cards = [_make_card(post_id=f"p{i}") for i in range(3)]
    _write_jsonl(snap_dir / "posts.jsonl", cards)

    conn = get_db(tmp_path / "test.db")
    migrate(conn)

    stats1 = rebuild_from_snapshots(conn, snap_dir.parent)
    stats2 = rebuild_from_snapshots(conn, snap_dir.parent)

    assert stats1["items"] == stats2["items"]

    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 3

    runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert runs == 1
    conn.close()


def test_rebuild_multiple_dates(tmp_path: Path):
    for date in ("2026-07-25", "2026-07-26", "2026-07-27"):
        snap_dir = tmp_path / "snapshots" / date
        cards = [_make_card(post_id=f"{date}-p{i}", snapshot_date=date) for i in range(2)]
        _write_jsonl(snap_dir / "posts.jsonl", cards)

    conn = get_db(tmp_path / "test.db")
    migrate(conn)
    stats = rebuild_from_snapshots(conn, tmp_path / "snapshots")

    assert stats["dates"] == 3
    assert stats["items"] == 6
    conn.close()


def test_rebuild_target_date(tmp_path: Path):
    for date in ("2026-07-25", "2026-07-26"):
        snap_dir = tmp_path / "snapshots" / date
        cards = [_make_card(post_id=f"{date}-p1", snapshot_date=date)]
        _write_jsonl(snap_dir / "posts.jsonl", cards)

    conn = get_db(tmp_path / "test.db")
    migrate(conn)
    stats = rebuild_from_snapshots(conn, tmp_path / "snapshots", target_date="2026-07-26")

    assert stats["dates"] == 1
    assert stats["items"] == 1

    runs = conn.execute("SELECT snapshot_date FROM runs").fetchall()
    assert [r[0] for r in runs] == ["2026-07-26"]
    conn.close()


def test_rebuild_research_state_survives(tmp_path: Path):
    snap_dir = tmp_path / "snapshots" / "2026-07-27"
    cards = [_make_card(post_id="p1")]
    _write_jsonl(snap_dir / "posts.jsonl", cards)

    conn = get_db(tmp_path / "test.db")
    migrate(conn)
    rebuild_from_snapshots(conn, snap_dir.parent)

    update_research_state(conn, "story_test123", saved=True, status="read", note="Important")
    conn.commit()

    rebuild_from_snapshots(conn, snap_dir.parent)

    state = get_research_state(conn, "story_test123")
    assert state is not None
    assert state.saved is True
    assert state.status == "read"
    assert state.note == "Important"
    conn.close()


def test_rebuild_broken_jsonl_skipped(tmp_path: Path):
    snap_dir = tmp_path / "snapshots" / "2026-07-27"
    snap_dir.mkdir(parents=True)
    valid = _make_card(post_id="good").to_json()
    (snap_dir / "posts.jsonl").write_text(f"{valid}\n{{broken\n{valid}\n", encoding="utf-8")

    conn = get_db(tmp_path / "test.db")
    migrate(conn)
    stats = rebuild_from_snapshots(conn, snap_dir.parent)

    assert stats["items"] == 2
    assert stats["skipped"] == 1
    conn.close()


def test_rebuild_all_source_families(tmp_path: Path):
    snap_dir = tmp_path / "snapshots" / "2026-07-27"
    _write_jsonl(snap_dir / "posts.jsonl", [_make_card(post_id="reddit1")])
    _write_jsonl(
        snap_dir / "hackernews.jsonl",
        [
            _make_card(
                post_id="hn1", subreddit="hackernews", url="https://news.ycombinator.com/item?id=1"
            )
        ],
    )
    _write_jsonl(
        snap_dir / "rss.jsonl",
        [_make_card(post_id="", subreddit="bbc", url="https://bbc.com/news/123")],
    )
    _write_jsonl(
        snap_dir / "ladder.jsonl",
        [_make_card(post_id="", subreddit="nytimes", url="https://nytimes.com/article")],
    )
    _write_jsonl(
        snap_dir / "producthunt.jsonl",
        [
            _make_card(
                post_id="ph1", subreddit="producthunt", url="https://producthunt.com/posts/test"
            )
        ],
    )

    conn = get_db(tmp_path / "test.db")
    migrate(conn)
    stats = rebuild_from_snapshots(conn, snap_dir.parent)

    assert stats["items"] == 5

    providers = {row[0] for row in conn.execute("SELECT DISTINCT provider FROM items").fetchall()}
    assert providers == {"reddit", "hackernews", "bbc", "nytimes", "producthunt"}
    conn.close()
