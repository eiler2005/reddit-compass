"""API v2: source-agnostic endpoints для stories, briefings, items.

Все v2 endpoints используют существующий Bearer auth.
Naming: virality_events (crosspost/surge), item_signals (LLM), stories, briefings.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Generator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import DEFAULT_PROFILE
from ..intelligence.engine import (
    DEFAULT_ENGINE_DB_PATH,
    compare_engine_versions,
    get_current_publication,
    get_data_release,
    get_publication,
    get_story_release,
    get_trend_release,
    inspect_story_release,
    inspect_trend_release,
    list_data_releases,
    list_publications,
    open_engine_readonly,
)
from ..intelligence.repository import (
    get_briefing,
    get_research_state,
    get_story,
    query_stories,
    update_research_state,
)
from ..intelligence.taxonomy import BROAD_DOMAINS
from .query_service import (
    build_domain_matrix,
    build_domain_summaries,
    build_goal_relevance_rankings,
    build_run_summary,
    build_trend_shelves,
)

router = APIRouter(prefix="/api/v2", tags=["v2"])


def _get_db() -> Generator[sqlite3.Connection, None, None]:
    db_path = Path(os.environ.get("RC_DB_PATH", "data/compass.db"))
    if os.access(db_path, os.W_OK):
        conn = sqlite3.connect(db_path, check_same_thread=False)
    else:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _get_engine_db() -> Generator[sqlite3.Connection | None, None, None]:
    path = Path(os.environ.get("RC_ENGINE_DB_PATH", str(DEFAULT_ENGINE_DB_PATH)))
    if not path.exists():
        yield None
        return
    conn = open_engine_readonly(path)
    try:
        yield conn
    finally:
        conn.close()


# ── Schemas ────────────────────────────────────────────────────────────────


class StoryOut(BaseModel):
    story_id: str
    canonical_key: str
    title: str
    summary_ru: str = ""
    theme_ids: list[str] = Field(default_factory=list)
    domain_ids: list[str] = Field(default_factory=list)
    trend_id: str = ""
    lifecycle: str = "new"
    project_scores: dict[str, int] = Field(default_factory=dict)
    first_seen: str = ""
    last_seen: str = ""
    item_count: int = 0
    source_count: int = 0
    trend_score: float = 0.0
    confidence: str = "low"
    direction: str = "new"


class StoryDetailOut(StoryOut):
    item_ids: list[str] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)


class PaginatedStories(BaseModel):
    items: list[StoryOut]
    total: int
    page: int
    page_size: int


class BriefingOut(BaseModel):
    schema_version: int
    run_id: str
    date: str
    profile: str
    status: str
    generated_at: str
    top_changes: list[dict[str, Any]] = Field(default_factory=list)
    watchlist: list[dict[str, Any]] = Field(default_factory=list)
    pain_points: list[dict[str, Any]] = Field(default_factory=list)
    column_ideas: list[dict[str, Any]] = Field(default_factory=list)
    narrative_shifts: list[dict[str, Any]] = Field(default_factory=list)


class RunOut(BaseModel):
    run_id: str
    snapshot_date: str
    profile: str
    status: str
    started_at: str
    finished_at: str | None = None


class SourceHealthOut(BaseModel):
    source_id: str
    provider: str
    cluster: str
    status: str
    count: int = 0
    duration_sec: float = 0.0
    error_code: str | None = None
    message: str = ""


class DomainOut(BaseModel):
    domain_id: str
    label_ru: str
    label_en: str


class RadarOut(BaseModel):
    date: str
    profile: str
    mode: str
    channel: str = "broad"
    selected_domain: str | None = None
    run: dict[str, Any]
    domains: list[dict[str, Any]]
    matrix: list[dict[str, Any]]
    shelves: dict[str, list[dict[str, Any]]]
    collection_run_id: str = ""
    data_release_id: str = ""
    story_release_id: str = ""
    trend_release_id: str = ""
    publication_id: str = ""
    engine_version: str = ""
    history_status: str = "legacy"
    input_status: str = ""
    published_at: str = ""
    serving_previous_publication: bool = False


class NewsItemOut(BaseModel):
    item_id: str
    provider: str
    source_cluster: str
    source_section: str = ""
    title: str
    summary_ru: str = ""
    excerpt: str = ""
    canonical_url: str = ""
    discussion_url: str = ""
    target_url: str = ""
    published_at: str = ""
    observed_at: str = ""
    snapshot_date: str = ""
    content_scope: str = "headline"
    domain_ids: list[str] = Field(default_factory=list)
    theme_ids: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    story_id: str = ""
    story_title: str = ""
    membership_reason: str = ""


class PaginatedNews(BaseModel):
    items: list[NewsItemOut]
    total: int
    page: int
    page_size: int
    publication_id: str
    data_release_id: str
    story_release_id: str
    trend_release_id: str


class PublishedStoryOut(BaseModel):
    story_id: str
    canonical_key: str
    title: str
    summary_ru: str = ""
    domain_ids: list[str] = Field(default_factory=list)
    theme_ids: list[str] = Field(default_factory=list)
    project_scores: dict[str, int] = Field(default_factory=dict)
    first_seen: str = ""
    last_seen: str = ""
    confidence: str = "low"
    source_count: int = 0
    item_count: int = 0
    evidence_items: list[NewsItemOut] = Field(default_factory=list)


class PublishedStoryDetailOut(PublishedStoryOut):
    trends: list[TrendOut] = Field(default_factory=list)
    publication_id: str = ""
    data_release_id: str = ""
    story_release_id: str = ""
    trend_release_id: str = ""


class PaginatedPublishedStories(BaseModel):
    items: list[PublishedStoryOut]
    total: int
    page: int
    page_size: int
    publication_id: str
    data_release_id: str
    story_release_id: str


class TrendOut(BaseModel):
    trend_id: str
    title: str
    pattern: str
    domain_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    lifecycle: str = "insufficient_history"
    source_scope: str = ""
    first_seen: str = ""
    last_seen: str = ""
    story_count: int = 0
    source_count: int = 0
    project_scores: dict[str, int] = Field(default_factory=dict)
    evidence_story_ids: list[str] = Field(default_factory=list)
    counterpoints: list[str] = Field(default_factory=list)
    review_status: str = "pending"
    stories: list[PublishedStoryOut] = Field(default_factory=list)


class TrendDetailOut(TrendOut):
    publication_id: str = ""
    data_release_id: str = ""
    story_release_id: str = ""
    trend_release_id: str = ""


class PaginatedTrends(BaseModel):
    items: list[TrendOut]
    total: int
    page: int
    page_size: int
    publication_id: str
    data_release_id: str
    story_release_id: str
    trend_release_id: str
    history_status: str


class ProjectLensOut(BaseModel):
    project_id: str
    publication_id: str
    data_release_id: str
    story_release_id: str
    trend_release_id: str
    trends: list[TrendOut] = Field(default_factory=list)
    stories: list[PublishedStoryOut] = Field(default_factory=list)


class ResearchStatePatch(BaseModel):
    saved: bool | None = None
    status: str | None = None
    note: str | None = None


class ResearchStateOut(BaseModel):
    story_id: str
    saved: bool
    status: str
    note: str
    updated_at: str


def _json_list(raw: Any, fallback: list[str] | None = None) -> list[str]:
    if isinstance(raw, list):
        return [str(v) for v in raw]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    return list(fallback or [])


def _json_int_dict(raw: Any) -> dict[str, int]:
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        return {}
    return {str(key): int(value or 0) for key, value in data.items()}


def _require_engine(conn: sqlite3.Connection | None) -> sqlite3.Connection:
    if conn is None:
        raise HTTPException(status_code=404, detail="Trend Engine database is not available")
    return conn


def _safe_url(url: str | None) -> str:
    if not url:
        return ""
    value = str(url)
    if value.startswith(("http://", "https://")):
        return value
    return ""


def _json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row_value(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def _resolve_publication(
    conn: sqlite3.Connection,
    *,
    channel: str,
    publication_id: str | None,
) -> tuple[Any, Any, Any, Any]:
    publication = (
        get_publication(conn, publication_id)
        if publication_id
        else get_current_publication(conn, channel)
    )
    if publication is None:
        detail = (
            f"Engine publication {publication_id} not found"
            if publication_id
            else f"No published engine version for channel {channel}"
        )
        raise HTTPException(status_code=404, detail=detail)
    data_release = get_data_release(conn, publication.data_release_id)
    story_release = get_story_release(conn, publication.story_release_id)
    trend_release = get_trend_release(conn, publication.trend_release_id)
    if data_release is None or story_release is None or trend_release is None:
        raise HTTPException(status_code=409, detail="Published engine version is incomplete")
    return publication, data_release, story_release, trend_release


def _matches_text(row: sqlite3.Row, query: str | None) -> bool:
    if not query:
        return True
    needle = query.lower()
    haystack = " ".join(
        str(_row_value(row, key, ""))
        for key in ("title", "summary_ru", "facet_summary_ru", "excerpt", "provider")
    ).lower()
    return needle in haystack


def _published_news_rows(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    facet_release_id: str,
    story_release_id: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            i.item_id,
            i.provider,
            i.source_cluster,
            i.source_section,
            i.title,
            i.summary_ru,
            i.excerpt,
            i.canonical_url,
            i.discussion_url,
            i.target_url,
            i.published_at,
            i.observed_at,
            i.snapshot_date,
            i.content_scope,
            i.domain_ids AS item_domain_ids,
            f.domain_ids AS facet_domain_ids,
            f.theme_ids,
            f.pain_points,
            f.summary_ru AS facet_summary_ru,
            COALESCE(si.story_id, '') AS story_id,
            COALESCE(si.membership_reason, '') AS membership_reason,
            COALESCE(s.title, '') AS story_title
        FROM release_items AS i
        LEFT JOIN item_facets AS f
          ON f.facet_release_id = ?
         AND f.item_id = i.item_id
        LEFT JOIN engine_story_items AS si
          ON si.story_release_id = ?
         AND si.item_id = i.item_id
        LEFT JOIN engine_stories AS s
          ON s.story_release_id = ?
         AND s.story_id = si.story_id
        WHERE i.release_id = ?
        ORDER BY COALESCE(i.published_at, i.observed_at, i.snapshot_date) DESC, i.item_id
        """,
        (facet_release_id, story_release_id, story_release_id, data_release_id),
    ).fetchall()


def _news_item_out(row: sqlite3.Row) -> NewsItemOut:
    domain_ids = _json_list(
        _row_value(row, "facet_domain_ids", "") or _row_value(row, "item_domain_ids", ""),
        fallback=["other"],
    )
    return NewsItemOut(
        item_id=str(row["item_id"]),
        provider=str(row["provider"] or ""),
        source_cluster=str(row["source_cluster"] or ""),
        source_section=str(row["source_section"] or ""),
        title=str(row["title"] or ""),
        summary_ru=str(_row_value(row, "facet_summary_ru", "") or row["summary_ru"] or ""),
        excerpt=str(row["excerpt"] or ""),
        canonical_url=_safe_url(str(row["canonical_url"] or "")),
        discussion_url=_safe_url(str(row["discussion_url"] or "")),
        target_url=_safe_url(str(row["target_url"] or "")),
        published_at=str(row["published_at"] or ""),
        observed_at=str(row["observed_at"] or ""),
        snapshot_date=str(row["snapshot_date"] or ""),
        content_scope=str(row["content_scope"] or "headline"),
        domain_ids=domain_ids,
        theme_ids=_json_list(_row_value(row, "theme_ids", "")),
        pain_points=_json_list(_row_value(row, "pain_points", "")),
        story_id=str(_row_value(row, "story_id", "")),
        story_title=str(_row_value(row, "story_title", "")),
        membership_reason=str(_row_value(row, "membership_reason", "")),
    )


def _filter_news_rows(
    rows: list[sqlite3.Row],
    *,
    date: str | None,
    domain: str | None,
    provider: str | None,
    source_cluster: str | None,
    q: str | None,
) -> list[sqlite3.Row]:
    filtered: list[sqlite3.Row] = []
    for row in rows:
        domain_ids = _json_list(
            _row_value(row, "facet_domain_ids", "") or _row_value(row, "item_domain_ids", ""),
            fallback=["other"],
        )
        if (
            date
            and row["snapshot_date"] != date
            and not str(row["published_at"] or "").startswith(date)
        ):
            continue
        if domain and domain not in domain_ids:
            continue
        if provider and str(row["provider"]) != provider:
            continue
        if source_cluster and str(row["source_cluster"]) != source_cluster:
            continue
        if not _matches_text(row, q):
            continue
        filtered.append(row)
    return filtered


def _engine_news(
    conn: sqlite3.Connection,
    *,
    channel: str = "broad",
    publication_id: str | None = None,
    date: str | None = None,
    domain: str | None = None,
    provider: str | None = None,
    source_cluster: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedNews:
    publication, data_release, story_release, trend_release = _resolve_publication(
        conn,
        channel=channel,
        publication_id=publication_id,
    )
    rows = _published_news_rows(
        conn,
        data_release_id=data_release.release_id,
        facet_release_id=story_release.facet_release_id,
        story_release_id=story_release.story_release_id,
    )
    filtered = _filter_news_rows(
        rows,
        date=date,
        domain=domain,
        provider=provider,
        source_cluster=source_cluster,
        q=q,
    )
    start = (page - 1) * page_size
    page_rows = filtered[start : start + page_size]
    return PaginatedNews(
        items=[_news_item_out(row) for row in page_rows],
        total=len(filtered),
        page=page,
        page_size=page_size,
        publication_id=publication.publication_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
    )


def _story_evidence(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    facet_release_id: str,
    story_release_id: str,
    story_id: str,
    limit: int = 5,
) -> list[NewsItemOut]:
    rows = conn.execute(
        """
        SELECT
            i.item_id,
            i.provider,
            i.source_cluster,
            i.source_section,
            i.title,
            i.summary_ru,
            i.excerpt,
            i.canonical_url,
            i.discussion_url,
            i.target_url,
            i.published_at,
            i.observed_at,
            i.snapshot_date,
            i.content_scope,
            i.domain_ids AS item_domain_ids,
            f.domain_ids AS facet_domain_ids,
            f.theme_ids,
            f.pain_points,
            f.summary_ru AS facet_summary_ru,
            si.story_id,
            si.membership_reason,
            ? AS story_title
        FROM engine_story_items AS si
        JOIN release_items AS i
          ON i.release_id = ?
         AND i.item_id = si.item_id
        LEFT JOIN item_facets AS f
          ON f.facet_release_id = ?
         AND f.item_id = i.item_id
        WHERE si.story_release_id = ?
          AND si.story_id = ?
        ORDER BY
            si.membership_score DESC,
            COALESCE(i.published_at, i.observed_at, i.snapshot_date) DESC
        LIMIT ?
        """,
        ("", data_release_id, facet_release_id, story_release_id, story_id, limit),
    ).fetchall()
    return [_news_item_out(row) for row in rows]


def _published_story_out(
    row: sqlite3.Row,
    *,
    evidence_items: list[NewsItemOut] | None = None,
) -> PublishedStoryOut:
    return PublishedStoryOut(
        story_id=str(row["story_id"]),
        canonical_key=str(row["canonical_key"] or ""),
        title=str(row["title"] or ""),
        summary_ru=str(row["summary_ru"] or ""),
        domain_ids=_json_list(row["domain_ids"], fallback=["other"]),
        theme_ids=_json_list(row["theme_ids"]),
        project_scores=_json_int_dict(row["project_scores"]),
        first_seen=str(row["first_seen"] or ""),
        last_seen=str(row["last_seen"] or ""),
        confidence=str(row["confidence"] or "low"),
        source_count=int(row["source_count"] or 0),
        item_count=int(row["item_count"] or 0),
        evidence_items=evidence_items or [],
    )


def _engine_stories(
    conn: sqlite3.Connection,
    *,
    channel: str = "broad",
    publication_id: str | None = None,
    domain: str | None = None,
    q: str | None = None,
    project_id: str | None = None,
    include_items: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedPublishedStories:
    publication, data_release, story_release, _trend_release = _resolve_publication(
        conn,
        channel=channel,
        publication_id=publication_id,
    )
    rows = conn.execute(
        """
        SELECT *
        FROM engine_stories
        WHERE story_release_id = ?
        ORDER BY source_count DESC, item_count DESC, last_seen DESC, story_id
        """,
        (story_release.story_release_id,),
    ).fetchall()
    filtered: list[sqlite3.Row] = []
    for row in rows:
        domain_ids = _json_list(row["domain_ids"], fallback=["other"])
        project_scores = _json_int_dict(row["project_scores"])
        if domain and domain not in domain_ids:
            continue
        if project_id and project_scores.get(project_id, 0) <= 0:
            continue
        if q:
            haystack = " ".join(
                [str(row["title"] or ""), str(row["summary_ru"] or ""), " ".join(domain_ids)]
            ).lower()
            if q.lower() not in haystack:
                continue
        filtered.append(row)
    if project_id:
        filtered.sort(
            key=lambda row: (
                -_json_int_dict(row["project_scores"]).get(project_id, 0),
                -int(row["source_count"] or 0),
                -int(row["item_count"] or 0),
                str(row["story_id"]),
            )
        )
    start = (page - 1) * page_size
    page_rows = filtered[start : start + page_size]
    stories = []
    for row in page_rows:
        evidence = (
            _story_evidence(
                conn,
                data_release_id=data_release.release_id,
                facet_release_id=story_release.facet_release_id,
                story_release_id=story_release.story_release_id,
                story_id=str(row["story_id"]),
            )
            if include_items
            else []
        )
        stories.append(_published_story_out(row, evidence_items=evidence))
    return PaginatedPublishedStories(
        items=stories,
        total=len(filtered),
        page=page,
        page_size=page_size,
        publication_id=publication.publication_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
    )


def _trend_out(
    row: sqlite3.Row,
    *,
    stories: list[PublishedStoryOut] | None = None,
) -> TrendOut:
    return TrendOut(
        trend_id=str(row["trend_id"]),
        title=str(row["name_ru"] or ""),
        pattern=str(row["pattern"] or ""),
        domain_ids=_json_list(row["domain_ids"]),
        confidence=float(row["confidence"] or 0.0),
        lifecycle=str(row["lifecycle"] or "insufficient_history"),
        source_scope=str(row["source_scope"] or ""),
        first_seen=str(row["first_seen"] or ""),
        last_seen=str(row["last_seen"] or ""),
        story_count=int(row["story_count"] or 0),
        source_count=int(row["source_count"] or 0),
        project_scores=_json_int_dict(row["project_scores"]),
        evidence_story_ids=_json_list(row["evidence_story_ids"]),
        counterpoints=_json_list(row["counterpoints"]),
        review_status=str(_row_value(row, "review_status", "pending") or "pending"),
        stories=stories or [],
    )


def _trend_stories(
    conn: sqlite3.Connection,
    *,
    story_release_id: str,
    trend_release_id: str,
    trend_id: str,
    limit: int = 6,
) -> list[PublishedStoryOut]:
    rows = conn.execute(
        """
        SELECT s.*
        FROM engine_trend_stories AS ts
        JOIN engine_stories AS s
          ON s.story_release_id = ?
         AND s.story_id = ts.story_id
        WHERE ts.trend_release_id = ?
          AND ts.trend_id = ?
        ORDER BY ts.membership_score DESC, s.source_count DESC, s.last_seen DESC
        LIMIT ?
        """,
        (story_release_id, trend_release_id, trend_id, limit),
    ).fetchall()
    return [_published_story_out(row) for row in rows]


def _engine_trends(
    conn: sqlite3.Connection,
    *,
    channel: str = "broad",
    publication_id: str | None = None,
    domain: str | None = None,
    lifecycle: str | None = None,
    review_status: str | None = None,
    project_id: str | None = None,
    include_stories: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedTrends:
    publication, data_release, story_release, trend_release = _resolve_publication(
        conn,
        channel=channel,
        publication_id=publication_id,
    )
    rows = conn.execute(
        """
        SELECT *
        FROM engine_trends
        WHERE trend_release_id = ?
        ORDER BY confidence DESC, story_count DESC, source_count DESC, trend_id
        """,
        (trend_release.trend_release_id,),
    ).fetchall()
    filtered: list[sqlite3.Row] = []
    for row in rows:
        domain_ids = _json_list(row["domain_ids"])
        project_scores = _json_int_dict(row["project_scores"])
        if domain and domain not in domain_ids:
            continue
        if lifecycle and row["lifecycle"] != lifecycle:
            continue
        if review_status and _row_value(row, "review_status", "pending") != review_status:
            continue
        if project_id and project_scores.get(project_id, 0) <= 0:
            continue
        filtered.append(row)
    if project_id:
        filtered.sort(
            key=lambda row: (
                -_json_int_dict(row["project_scores"]).get(project_id, 0),
                -float(row["confidence"] or 0.0),
                -int(row["story_count"] or 0),
                str(row["trend_id"]),
            )
        )
    start = (page - 1) * page_size
    page_rows = filtered[start : start + page_size]
    trends = []
    for row in page_rows:
        stories = (
            _trend_stories(
                conn,
                story_release_id=story_release.story_release_id,
                trend_release_id=trend_release.trend_release_id,
                trend_id=str(row["trend_id"]),
            )
            if include_stories
            else []
        )
        trends.append(_trend_out(row, stories=stories))
    return PaginatedTrends(
        items=trends,
        total=len(filtered),
        page=page,
        page_size=page_size,
        publication_id=publication.publication_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
        history_status=trend_release.history_status,
    )


def _engine_project_lens(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
    limit: int = 20,
) -> ProjectLensOut:
    trends = _engine_trends(
        conn,
        channel=channel,
        publication_id=publication_id,
        project_id=project_id,
        page_size=limit,
    )
    stories = _engine_stories(
        conn,
        channel=channel,
        publication_id=publication_id,
        project_id=project_id,
        page_size=limit,
    )
    return ProjectLensOut(
        project_id=project_id,
        publication_id=trends.publication_id,
        data_release_id=trends.data_release_id,
        story_release_id=trends.story_release_id,
        trend_release_id=trends.trend_release_id,
        trends=trends.items,
        stories=stories.items,
    )


def _engine_story_detail(
    conn: sqlite3.Connection,
    *,
    story_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
) -> PublishedStoryDetailOut:
    publication, data_release, story_release, trend_release = _resolve_publication(
        conn,
        channel=channel,
        publication_id=publication_id,
    )
    row = conn.execute(
        """
        SELECT *
        FROM engine_stories
        WHERE story_release_id = ?
          AND story_id = ?
        """,
        (story_release.story_release_id, story_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Published story {story_id} not found")
    evidence = _story_evidence(
        conn,
        data_release_id=data_release.release_id,
        facet_release_id=story_release.facet_release_id,
        story_release_id=story_release.story_release_id,
        story_id=story_id,
        limit=20,
    )
    trend_rows = conn.execute(
        """
        SELECT t.*
        FROM engine_trend_stories AS ts
        JOIN engine_trends AS t
          ON t.trend_release_id = ?
         AND t.trend_id = ts.trend_id
        WHERE ts.trend_release_id = ?
          AND ts.story_id = ?
        ORDER BY t.confidence DESC, t.story_count DESC, t.trend_id
        """,
        (trend_release.trend_release_id, trend_release.trend_release_id, story_id),
    ).fetchall()
    base = _published_story_out(row, evidence_items=evidence)
    return PublishedStoryDetailOut(
        **base.model_dump(),
        trends=[_trend_out(trend_row) for trend_row in trend_rows],
        publication_id=publication.publication_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
    )


def _engine_trend_detail(
    conn: sqlite3.Connection,
    *,
    trend_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
) -> TrendDetailOut:
    publication, data_release, story_release, trend_release = _resolve_publication(
        conn,
        channel=channel,
        publication_id=publication_id,
    )
    row = conn.execute(
        """
        SELECT *
        FROM engine_trends
        WHERE trend_release_id = ?
          AND trend_id = ?
        """,
        (trend_release.trend_release_id, trend_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Published trend {trend_id} not found")
    story_rows = conn.execute(
        """
        SELECT s.*
        FROM engine_trend_stories AS ts
        JOIN engine_stories AS s
          ON s.story_release_id = ?
         AND s.story_id = ts.story_id
        WHERE ts.trend_release_id = ?
          AND ts.trend_id = ?
        ORDER BY ts.membership_score DESC, s.source_count DESC, s.last_seen DESC
        """,
        (story_release.story_release_id, trend_release.trend_release_id, trend_id),
    ).fetchall()
    stories = [
        _published_story_out(
            story_row,
            evidence_items=_story_evidence(
                conn,
                data_release_id=data_release.release_id,
                facet_release_id=story_release.facet_release_id,
                story_release_id=story_release.story_release_id,
                story_id=str(story_row["story_id"]),
                limit=3,
            ),
        )
        for story_row in story_rows
    ]
    base = _trend_out(row, stories=stories)
    return TrendDetailOut(
        **base.model_dump(),
        publication_id=publication.publication_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
    )


def _engine_radar(
    conn: sqlite3.Connection,
    *,
    date: str,
    profile: str,
    mode: str,
    domain: str | None,
    channel: str,
    publication_id: str | None,
) -> RadarOut | None:
    publication = (
        get_publication(conn, publication_id)
        if publication_id
        else get_current_publication(conn, channel)
    )
    if publication is None:
        if publication_id:
            raise HTTPException(
                status_code=404,
                detail=f"Engine publication {publication_id} not found",
            )
        return None

    data_release = get_data_release(conn, publication.data_release_id)
    story_release = get_story_release(conn, publication.story_release_id)
    trend_release = get_trend_release(conn, publication.trend_release_id)
    if data_release is None or story_release is None or trend_release is None:
        raise HTTPException(status_code=409, detail="Published engine version is incomplete")

    selected_domain = domain
    if mode == "ai-native" and selected_domain is None:
        selected_domain = "ai_technology"

    trend_rows = conn.execute(
        """
        SELECT *
        FROM engine_trends
        WHERE trend_release_id = ?
        ORDER BY confidence DESC, story_count DESC, trend_id
        """,
        (trend_release.trend_release_id,),
    ).fetchall()
    shelves: dict[str, list[dict[str, Any]]] = {}
    for row in trend_rows:
        domain_ids = _json_list(row["domain_ids"], fallback=["other"])
        if selected_domain and selected_domain not in domain_ids:
            continue
        lifecycle = str(row["lifecycle"] or "insufficient_history")
        shelves.setdefault(lifecycle, []).append(
            {
                "trend_id": row["trend_id"],
                "title": row["name_ru"],
                "pattern": row["pattern"],
                "domain_ids": domain_ids,
                "confidence": float(row["confidence"] or 0.0),
                "lifecycle": lifecycle,
                "source_scope": row["source_scope"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "story_count": int(row["story_count"] or 0),
                "source_count": int(row["source_count"] or 0),
                "project_scores": _json_int_dict(row["project_scores"]),
                "evidence_story_ids": _json_list(row["evidence_story_ids"]),
                "counterpoints": _json_list(row["counterpoints"]),
                "review_status": dict(row).get("review_status", "legacy"),
            }
        )

    domain_counts: dict[str, dict[str, int]] = {}
    story_rows = conn.execute(
        """
        SELECT domain_ids, source_count, item_count
        FROM engine_stories
        WHERE story_release_id = ?
        """,
        (story_release.story_release_id,),
    ).fetchall()
    for row in story_rows:
        for domain_id in _json_list(row["domain_ids"], fallback=["other"]):
            bucket = domain_counts.setdefault(
                domain_id,
                {"story_count": 0, "item_count": 0, "source_count": 0},
            )
            bucket["story_count"] += 1
            bucket["item_count"] += int(row["item_count"] or 0)
            bucket["source_count"] += int(row["source_count"] or 0)

    matrix_counts: dict[tuple[str, str], int] = {}
    facet_rows = conn.execute(
        """
        SELECT f.domain_ids, i.source_cluster
        FROM item_facets AS f
        JOIN release_items AS i
          ON i.release_id = ?
         AND i.item_id = f.item_id
        WHERE f.facet_release_id = ?
        """,
        (data_release.release_id, story_release.facet_release_id),
    ).fetchall()
    for row in facet_rows:
        source_cluster = str(row["source_cluster"] or "unknown")
        for domain_id in _json_list(row["domain_ids"], fallback=["other"]):
            key = (domain_id, source_cluster)
            matrix_counts[key] = matrix_counts.get(key, 0) + 1

    release_row = conn.execute(
        "SELECT source_coverage_json FROM data_releases WHERE release_id = ?",
        (data_release.release_id,),
    ).fetchone()
    source_coverage = _json_int_dict(release_row["source_coverage_json"]) if release_row else {}
    release_dates = data_release.dates
    serving_previous = bool(release_dates and date not in release_dates)
    collection_run_id = ",".join(data_release.run_ids)

    return RadarOut(
        date=date,
        profile=profile,
        mode=mode,
        channel=channel,
        selected_domain=domain,
        run={
            "run_id": collection_run_id,
            "status": data_release.input_status,
            "item_count": data_release.item_count,
            "source_coverage": source_coverage,
            "release_dates": release_dates,
            "serving_previous_publication": serving_previous,
        },
        domains=[
            {
                "domain_id": domain_id,
                "label_ru": BROAD_DOMAINS[domain_id].label_ru
                if domain_id in BROAD_DOMAINS
                else domain_id,
                **counts,
            }
            for domain_id, counts in sorted(
                domain_counts.items(),
                key=lambda item: (-item[1]["story_count"], item[0]),
            )
        ],
        matrix=[
            {
                "domain_id": domain_id,
                "source_cluster": source_cluster,
                "item_count": count,
            }
            for (domain_id, source_cluster), count in sorted(matrix_counts.items())
        ],
        shelves=shelves,
        collection_run_id=collection_run_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
        publication_id=publication.publication_id,
        engine_version=f"{story_release.method}/{trend_release.method}",
        history_status=trend_release.history_status,
        input_status=data_release.input_status,
        published_at=publication.created_at,
        serving_previous_publication=serving_previous,
    )


def _story_out(s: dict[str, Any]) -> StoryOut:
    return StoryOut(
        story_id=s["story_id"],
        canonical_key=s["canonical_key"],
        title=s["title"],
        summary_ru=s.get("summary_ru", ""),
        theme_ids=_json_list(s.get("theme_ids")),
        domain_ids=_json_list(s.get("domain_ids"), fallback=["other"]),
        trend_id=s.get("trend_id") or s.get("metric_trend_id", ""),
        lifecycle=s.get("lifecycle") or s.get("metric_lifecycle") or s.get("direction", "new"),
        project_scores=_json_int_dict(s.get("project_scores") or s.get("metric_project_scores")),
        first_seen=s.get("first_seen", ""),
        last_seen=s.get("last_seen", ""),
        item_count=s.get("item_count", 0),
        source_count=s.get("source_count", 0),
        trend_score=s.get("trend_score", 0),
        confidence=s.get("confidence", "low"),
        direction=s.get("direction", "new"),
    )


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("/briefings/{date}", response_model=BriefingOut)
def get_briefing_endpoint(
    date: str,
    profile: str = DEFAULT_PROFILE,
    conn: sqlite3.Connection = Depends(_get_db),
) -> BriefingOut:
    """Briefing за дату."""
    briefing = get_briefing(conn, date, profile)
    if briefing is None:
        raise HTTPException(status_code=404, detail=f"Briefing not found for {date}")

    return BriefingOut(
        schema_version=briefing.schema_version,
        run_id=briefing.run_id,
        date=briefing.date,
        profile=briefing.profile,
        status=briefing.status,
        generated_at=briefing.generated_at,
        top_changes=[
            {
                "story_id": bs.story.story_id,
                "title": bs.story.title,
                "trend_score": bs.metric.trend_score,
                "direction": bs.metric.direction,
            }
            for bs in briefing.top_changes
        ],
        watchlist=[
            {
                "story_id": bs.story.story_id,
                "title": bs.story.title,
                "trend_score": bs.metric.trend_score,
            }
            for bs in briefing.watchlist
        ],
        pain_points=[{"text": gp.text} for gp in briefing.pain_points],
        column_ideas=[{"text": gp.text} for gp in briefing.column_ideas],
        narrative_shifts=[{"text": gp.text} for gp in briefing.narrative_shifts],
    )


@router.get("/stories", response_model=PaginatedStories)
def list_stories(
    q: str | None = None,
    date: str | None = None,
    profile: str | None = None,
    theme: str | None = None,
    candidate_theme: str | None = None,
    domain: str | None = None,
    pain: str | None = None,
    provider: str | None = None,
    source_cluster: str | None = None,
    direction: str | None = None,
    confidence: str | None = None,
    sort: str = "trend_score",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    conn: sqlite3.Connection = Depends(_get_db),
) -> PaginatedStories:
    """Список stories с фильтрами."""
    stories, total = query_stories(
        conn,
        date=date,
        profile=profile,
        q=q,
        theme=theme,
        candidate_theme=candidate_theme,
        domain=domain,
        pain=pain,
        provider=provider,
        source_cluster=source_cluster,
        direction=direction,
        confidence=confidence,
        sort=sort,
        page=page,
        page_size=page_size,
    )

    return PaginatedStories(
        items=[_story_out(s) for s in stories],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stories/{story_id}", response_model=StoryDetailOut)
def get_story_endpoint(
    story_id: str,
    conn: sqlite3.Connection = Depends(_get_db),
) -> StoryDetailOut:
    """Story по ID."""
    story_data = get_story(conn, story_id)
    if story_data is None:
        raise HTTPException(status_code=404, detail=f"Story {story_id} not found")

    return StoryDetailOut(
        story_id=story_data["story_id"],
        canonical_key=story_data["canonical_key"],
        title=story_data["title"],
        summary_ru=story_data.get("summary_ru", ""),
        theme_ids=story_data.get("theme_ids", []),
        domain_ids=story_data.get("domain_ids", []),
        trend_id=story_data.get("trend_id", ""),
        lifecycle=story_data.get("lifecycle", "new"),
        project_scores=story_data.get("project_scores", {}),
        first_seen=story_data.get("first_seen", ""),
        last_seen=story_data.get("last_seen", ""),
        item_ids=story_data.get("item_ids", []),
        metrics=story_data.get("metrics", []),
    )


@router.get("/domains", response_model=list[DomainOut])
def list_domains() -> list[DomainOut]:
    return [
        DomainOut(
            domain_id=domain.domain_id,
            label_ru=domain.label_ru,
            label_en=domain.label_en,
        )
        for domain in BROAD_DOMAINS.values()
    ]


@router.get("/radar/{date}", response_model=RadarOut)
def get_radar_endpoint(
    date: str,
    profile: str = DEFAULT_PROFILE,
    mode: str = "broad",
    domain: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    conn: sqlite3.Connection = Depends(_get_db),
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> RadarOut:
    if engine_conn is not None:
        published_radar = _engine_radar(
            engine_conn,
            date=date,
            profile=profile,
            mode=mode,
            domain=domain,
            channel=channel,
            publication_id=publication_id,
        )
        if published_radar is not None:
            return published_radar

    run = build_run_summary(conn, date, profile)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found for {date}")
    run_id = run.run_id
    shelf_domain = domain
    if mode == "ai-native" and shelf_domain is None:
        shelf_domain = "ai_technology"
    shelves = build_trend_shelves(conn, run_id, domain=shelf_domain)
    return RadarOut(
        date=date,
        profile=profile,
        mode=mode,
        selected_domain=domain,
        run=run.__dict__,
        domains=[d.__dict__ for d in build_domain_summaries(conn, run_id)],
        matrix=build_domain_matrix(conn, run_id),
        shelves={
            shelf_id: [story.__dict__ for story in stories] for shelf_id, stories in shelves.items()
        },
    )


@router.get("/trends", response_model=PaginatedStories)
def list_trends(
    date: str | None = None,
    profile: str = DEFAULT_PROFILE,
    window: str = "7d",
    domain: str | None = None,
    candidate_theme: str | None = None,
    pain: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    conn: sqlite3.Connection = Depends(_get_db),
) -> PaginatedStories:
    del window
    stories, total = query_stories(
        conn,
        date=date,
        profile=profile,
        domain=domain,
        candidate_theme=candidate_theme,
        pain=pain,
        sort="trend_score",
        page=page,
        page_size=page_size,
    )
    return PaginatedStories(
        items=[_story_out(s) for s in stories],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/trends/{trend_id}", response_model=StoryDetailOut)
def get_trend_endpoint(
    trend_id: str,
    conn: sqlite3.Connection = Depends(_get_db),
) -> StoryDetailOut:
    row = conn.execute(
        "SELECT story_id FROM stories WHERE trend_id = ? LIMIT 1",
        (trend_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Trend {trend_id} not found")
    return get_story_endpoint(row["story_id"], conn)


@router.get("/projects/{project_id}/radar", response_model=list[StoryOut])
def get_project_radar(
    project_id: str,
    date: str,
    profile: str = DEFAULT_PROFILE,
    conn: sqlite3.Connection = Depends(_get_db),
) -> list[StoryOut]:
    run = build_run_summary(conn, date, profile)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found for {date}")
    rankings = build_goal_relevance_rankings(conn, run.run_id, [project_id], limit=20)
    return [
        StoryOut(
            story_id=story.story_id,
            canonical_key="",
            title=story.title,
            summary_ru=story.summary_ru,
            domain_ids=story.domain_ids,
            item_count=story.item_count,
            source_count=story.source_count,
            trend_score=story.trend_score,
            confidence=story.confidence,
            direction=story.direction,
        )
        for story in rankings.get(project_id, [])
    ]


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    limit: int = Query(default=30, ge=1, le=100),
    conn: sqlite3.Connection = Depends(_get_db),
) -> list[RunOut]:
    """Список runs."""
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY snapshot_date DESC LIMIT ?", (limit,)
    ).fetchall()

    return [
        RunOut(
            run_id=row["run_id"],
            snapshot_date=row["snapshot_date"],
            profile=row["profile"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
        for row in rows
    ]


@router.get("/source-health", response_model=list[SourceHealthOut])
def list_source_health(
    run_id: str | None = None,
    date: str | None = None,
    profile: str = DEFAULT_PROFILE,
    by: str = "source",
    conn: sqlite3.Connection = Depends(_get_db),
) -> list[SourceHealthOut]:
    """Source health для run."""
    del by
    if date and not run_id:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE snapshot_date = ? AND profile = ?",
            (date, profile),
        ).fetchone()
        run_id = row["run_id"] if row else None
    if run_id:
        rows = conn.execute("SELECT * FROM source_health WHERE run_id = ?", (run_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM source_health ORDER BY run_id DESC LIMIT 100").fetchall()

    return [
        SourceHealthOut(
            source_id=row["source_id"],
            provider=row["provider"],
            cluster=row["cluster"],
            status=row["status"],
            count=row["count"],
            duration_sec=row["duration_sec"],
            error_code=row["error_code"],
            message=row["message"],
        )
        for row in rows
    ]


@router.get("/news", response_model=PaginatedNews)
def list_published_news(
    date: str | None = None,
    domain: str | None = None,
    provider: str | None = None,
    source_cluster: str | None = None,
    q: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> PaginatedNews:
    """Raw published corpus items: news/inbox layer, not stories or trends."""
    return _engine_news(
        _require_engine(engine_conn),
        channel=channel,
        publication_id=publication_id,
        date=date,
        domain=domain,
        provider=provider,
        source_cluster=source_cluster,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.get("/engine/stories", response_model=PaginatedPublishedStories)
def list_published_engine_stories(
    domain: str | None = None,
    q: str | None = None,
    project_id: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    include_items: bool = True,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> PaginatedPublishedStories:
    """Concrete event clusters from the current published StoryRelease."""
    return _engine_stories(
        _require_engine(engine_conn),
        channel=channel,
        publication_id=publication_id,
        domain=domain,
        q=q,
        project_id=project_id,
        include_items=include_items,
        page=page,
        page_size=page_size,
    )


@router.get("/engine/stories/{story_id}", response_model=PublishedStoryDetailOut)
def get_published_engine_story(
    story_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> PublishedStoryDetailOut:
    """Published story detail with evidence and containing trends."""
    return _engine_story_detail(
        _require_engine(engine_conn),
        story_id=story_id,
        channel=channel,
        publication_id=publication_id,
    )


@router.get("/engine/trends", response_model=PaginatedTrends)
def list_published_engine_trends(
    domain: str | None = None,
    lifecycle: str | None = None,
    review_status: str | None = None,
    project_id: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    include_stories: bool = True,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> PaginatedTrends:
    """Recurring patterns over stories from the current published TrendRelease."""
    return _engine_trends(
        _require_engine(engine_conn),
        channel=channel,
        publication_id=publication_id,
        domain=domain,
        lifecycle=lifecycle,
        review_status=review_status,
        project_id=project_id,
        include_stories=include_stories,
        page=page,
        page_size=page_size,
    )


@router.get("/engine/trends/{trend_id}", response_model=TrendDetailOut)
def get_published_engine_trend(
    trend_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> TrendDetailOut:
    """Published trend detail with member stories and evidence."""
    return _engine_trend_detail(
        _require_engine(engine_conn),
        trend_id=trend_id,
        channel=channel,
        publication_id=publication_id,
    )


@router.get("/projects/{project_id}/lens", response_model=ProjectLensOut)
def get_engine_project_lens(
    project_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> ProjectLensOut:
    """Project lens over published trends and stories, e.g. book or RBC."""
    return _engine_project_lens(
        _require_engine(engine_conn),
        project_id=project_id,
        channel=channel,
        publication_id=publication_id,
        limit=limit,
    )


@router.get("/engine/releases")
def list_engine_releases(
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> dict[str, list[dict[str, Any]]]:
    """List immutable corpus releases and their derived analysis versions."""
    conn = _require_engine(engine_conn)
    data_releases = [asdict(release) for release in list_data_releases(conn)]
    facet_releases = [
        dict(row)
        for row in conn.execute("SELECT * FROM facet_releases ORDER BY created_at DESC").fetchall()
    ]
    story_releases = [
        {
            **dict(row),
            "metrics": json.loads(row["metrics_json"] or "{}"),
        }
        for row in conn.execute("SELECT * FROM story_releases ORDER BY created_at DESC").fetchall()
    ]
    trend_releases = [
        {
            **dict(row),
            "metrics": json.loads(row["metrics_json"] or "{}"),
        }
        for row in conn.execute("SELECT * FROM trend_releases ORDER BY created_at DESC").fetchall()
    ]
    return {
        "data_releases": data_releases,
        "facet_releases": facet_releases,
        "story_releases": story_releases,
        "trend_releases": trend_releases,
    }


@router.get("/engine/story-releases/{story_release_id}")
def get_engine_story_release(
    story_release_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> dict[str, Any]:
    conn = _require_engine(engine_conn)
    if get_story_release(conn, story_release_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Story release {story_release_id} not found",
        )
    return inspect_story_release(conn, story_release_id, limit=limit)


@router.get("/engine/trend-releases/{trend_release_id}")
def get_engine_trend_release(
    trend_release_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> dict[str, Any]:
    conn = _require_engine(engine_conn)
    if get_trend_release(conn, trend_release_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Trend release {trend_release_id} not found",
        )
    return inspect_trend_release(conn, trend_release_id, limit=limit)


@router.get("/engine/publications")
def list_engine_publications(
    channel: str | None = None,
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> list[dict[str, Any]]:
    conn = _require_engine(engine_conn)
    return [asdict(publication) for publication in list_publications(conn, channel)]


@router.get("/engine/compare")
def compare_engine_releases(
    left: str,
    right: str,
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> dict[str, Any]:
    conn = _require_engine(engine_conn)
    try:
        return compare_engine_versions(conn, left, right)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/stories/{story_id}/research-state", response_model=ResearchStateOut)
def patch_research_state(
    story_id: str,
    patch: ResearchStatePatch,
    conn: sqlite3.Connection = Depends(_get_db),
) -> ResearchStateOut:
    """Обновление research state."""
    from datetime import UTC, datetime

    updated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    state = update_research_state(
        conn,
        story_id,
        saved=patch.saved,
        status=patch.status,
        note=patch.note,
        updated_at=updated_at,
    )
    conn.commit()

    return ResearchStateOut(
        story_id=state.story_id,
        saved=state.saved,
        status=state.status,
        note=state.note,
        updated_at=state.updated_at,
    )


@router.get("/stories/{story_id}/research-state", response_model=ResearchStateOut | None)
def get_research_state_endpoint(
    story_id: str,
    conn: sqlite3.Connection = Depends(_get_db),
) -> ResearchStateOut | None:
    """Получение research state."""
    state = get_research_state(conn, story_id)
    if state is None:
        return None

    return ResearchStateOut(
        story_id=state.story_id,
        saved=state.saved,
        status=state.status,
        note=state.note,
        updated_at=state.updated_at,
    )
