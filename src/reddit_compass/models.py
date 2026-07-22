"""Data-модели Reddit-мониторинга: карточки постов и комментариев."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utc_from_timestamp(ts: float | int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


@dataclass(frozen=True)
class CommentCard:
    comment_id: str
    author: str
    score: int
    body: str
    created_utc: str | None = None
    is_submitter: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PostCard:
    subreddit: str
    post_id: str
    title: str
    author: str
    created_utc: str | None
    score: int
    upvote_ratio: float
    num_comments: int
    url: str
    selftext: str
    link_flair_text: str | None
    is_self: bool
    permalink: str
    monitoring_type: str
    snapshot_date: str
    keyword: str | None = None
    top_comments: list[CommentCard] = field(default_factory=list)
    crosspost_parents: list[str] = field(default_factory=list)
    is_video: bool = False
    over_18: bool = False
    stickied: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["top_comments"] = [c.to_dict() for c in self.top_comments]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @property
    def full_url(self) -> str:
        if self.url.startswith("https://"):
            return self.url
        return f"https://www.reddit.com{self.permalink}"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PostCard:
        """Строит PostCard из сырого dict (JSONL), восстанавливая CommentCard в top_comments."""
        data = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        raw_comments = data.get("top_comments") or []
        data["top_comments"] = [
            c
            if isinstance(c, CommentCard)
            else CommentCard(
                **{k: v for k, v in c.items() if k in CommentCard.__dataclass_fields__}
            )
            for c in raw_comments
        ]
        return cls(**data)


@dataclass
class TrackedThreadState:
    url: str
    post_id: str
    subreddit: str
    title: str
    score: int
    num_comments: int
    last_checked: str
    new_comments_since_last: int = 0
    score_delta: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class ViralitySignal:
    post_id: str
    title: str
    original_subreddit: str
    crossposted_to: list[str]
    total_score: int
    total_comments: int
    signal_type: str  # "crosspost" | "score_surge" | "multi_subreddit"
    detected_at: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
