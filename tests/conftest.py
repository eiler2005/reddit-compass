"""Общие фикстуры тестов."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from reddit_compass.models import PostCard


@pytest.fixture
def make_card() -> Callable[..., PostCard]:
    """Фабрика PostCard с разумными дефолтами; переопределяй нужные поля через kwargs."""

    def _make(**over: Any) -> PostCard:
        base: dict[str, Any] = {
            "subreddit": "artificial",
            "post_id": "p1",
            "title": "t",
            "author": "a",
            "created_utc": None,
            "score": 10,
            "upvote_ratio": 0.9,
            "num_comments": 5,
            "url": "https://www.reddit.com/x",
            "selftext": "",
            "link_flair_text": None,
            "is_self": True,
            "permalink": "/r/artificial/comments/p1/x/",
            "monitoring_type": "hot",
            "snapshot_date": "2026-07-22",
        }
        base.update(over)
        return PostCard(**base)

    return _make
