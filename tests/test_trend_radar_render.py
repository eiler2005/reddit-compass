"""Тесты legacy Trend Radar (`signals.render_trend_radar`).

Файл `tests/test_radar.py` был удалён вместе с `api/dashboard.py`, который он покрывал.
Но `render_trend_radar` остался живым кодом — его вызывают команды `radar` и `report`
(`cli.py`), — и после удаления он оказался без единого теста. Эти проверки возвращают
покрытие ровно на живую часть; тесты снятого `dashboard.py` не восстанавливаются.
"""

from __future__ import annotations

import json
from pathlib import Path

from reddit_compass.signals import render_trend_radar


def _make_post(
    post_id: str = "p1",
    title: str = "Test Post",
    subreddit: str = "test",
    score: int = 100,
    source: str = "reddit",
) -> dict:
    return {
        "post_id": post_id,
        "title": title,
        "subreddit": subreddit,
        "score": score,
        "author": "tester",
        "created_utc": "2026-07-25T12:00:00Z",
        "upvote_ratio": 0.9,
        "num_comments": 5,
        "url": f"https://example.com/{post_id}",
        "selftext": "",
        "link_flair_text": None,
        "is_self": True,
        "permalink": f"/r/{subreddit}/comments/{post_id}",
        "monitoring_type": "hot",
        "snapshot_date": "2026-07-25",
        "source": source,
    }


def _write(snap: Path, name: str, posts: list[dict]) -> None:
    snap.joinpath(name).write_text(
        "\n".join(json.dumps(post, ensure_ascii=False) for post in posts),
        encoding="utf-8",
    )


def test_trend_radar_includes_ladder_and_producthunt(tmp_path: Path) -> None:
    """Ladder и ProductHunt — полноправные источники радара, а не только Reddit."""
    snap = tmp_path / "2026-07-25"
    snap.mkdir()
    _write(snap, "ladder.jsonl", [_make_post("l1", "NYT AI Article", "nytimes", 0, "ladder")])
    _write(
        snap,
        "producthunt.jsonl",
        [_make_post("ph1", "Cool AI Tool", "producthunt", 50, "producthunt")],
    )

    radar = render_trend_radar(snap, "2026-07-25")

    assert "Ladder: 1" in radar
    assert "PH: 1" in radar
    assert "NYT AI Article" in radar
    assert "Cool AI Tool" in radar
    assert "ProductHunt" in radar


def test_trend_radar_counts_every_source_in_mega_trends(tmp_path: Path) -> None:
    """Сводка считает все источники разом: Reddit, Hacker News и Ladder."""
    snap = tmp_path / "2026-07-25"
    snap.mkdir()
    _write(snap, "posts.jsonl", [_make_post("r1", "Reddit Post", "artificial", 1000)])
    _write(snap, "hackernews.jsonl", [_make_post("h1", "HN Post", "hackernews", 500, "hackernews")])
    _write(snap, "ladder.jsonl", [_make_post("l1", "Ladder Post", "nytimes", 0, "ladder")])

    radar = render_trend_radar(snap, "2026-07-25")

    assert "Reddit Post" in radar
    assert "HN Post" in radar
    assert "Ladder Post" in radar
    assert "3 единиц" in radar


def test_trend_radar_renders_empty_snapshot_without_failing(tmp_path: Path) -> None:
    """Пустой снапшот не должен ронять команду — радар просто пустой."""
    snap = tmp_path / "2026-07-25"
    snap.mkdir()

    radar = render_trend_radar(snap, "2026-07-25")

    assert isinstance(radar, str)
    assert "2026-07-25" in radar
