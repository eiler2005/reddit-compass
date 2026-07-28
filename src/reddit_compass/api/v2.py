"""API v2: source-agnostic endpoints для stories, briefings, items.

Все v2 endpoints используют существующий Bearer auth.
Naming: virality_events (crosspost/surge), item_signals (LLM), stories, briefings.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import DEFAULT_PROFILE
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
    selected_domain: str | None = None
    run: dict[str, Any]
    domains: list[dict[str, Any]]
    matrix: list[dict[str, Any]]
    shelves: dict[str, list[dict[str, Any]]]


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
    conn: sqlite3.Connection = Depends(_get_db),
) -> RadarOut:
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
