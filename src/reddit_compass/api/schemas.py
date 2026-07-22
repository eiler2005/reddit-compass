"""Pydantic-схемы для API (request/response models)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    client_id: str
    client_secret: str
    grant_type: str = "client_credentials"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600


class SnapshotOut(BaseModel):
    id: int
    date: str
    created_at: str
    posts_count: int
    source: str


class PostOut(BaseModel):
    id: int
    post_id: str
    subreddit: str
    title: str
    author: str
    score: int
    num_comments: int
    upvote_ratio: float
    url: str
    permalink: str
    monitoring_type: str
    keyword: str | None = None
    source: str
    created_utc: str | None = None


class SignalOut(BaseModel):
    id: int
    post_id: str
    title: str
    signal_type: str
    total_score: int
    total_comments: int
    original_subreddit: str
    crossposted_to: str
    detected_at: str
    url: str


class StatsOut(BaseModel):
    total_snapshots: int
    total_posts: int
    total_signals: int
    latest_snapshot: str | None
    top_subreddits: list[dict[str, object]] = Field(default_factory=list)


class PaginatedPosts(BaseModel):
    items: list[PostOut]
    total: int
    limit: int
    offset: int
