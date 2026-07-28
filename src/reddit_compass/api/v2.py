"""API v2: source-agnostic endpoints для stories, briefings, items.

Все v2 endpoints используют существующий Bearer auth.
Naming: virality_events (crosspost/surge), item_signals (LLM), stories, briefings.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..intelligence.migrations import migrate
from ..intelligence.repository import (
    get_briefing,
    get_research_state,
    get_story,
    query_stories,
    update_research_state,
)

router = APIRouter(prefix="/api/v2", tags=["v2"])


def _get_db() -> Generator[sqlite3.Connection, None, None]:
    db_path = Path(os.environ.get("RC_DB_PATH", "data/compass.db"))
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        migrate(conn)
    except sqlite3.OperationalError:
        pass  # read-only DB (API container)
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


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("/briefings/{date}", response_model=BriefingOut)
def get_briefing_endpoint(
    date: str,
    profile: str = "ai-native",
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
    theme: str | None = None,
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
        q=q,
        theme=theme,
        direction=direction,
        confidence=confidence,
        sort=sort,
        page=page,
        page_size=page_size,
    )

    return PaginatedStories(
        items=[
            StoryOut(
                story_id=s["story_id"],
                canonical_key=s["canonical_key"],
                title=s["title"],
                summary_ru=s.get("summary_ru", ""),
                theme_ids=[],
                first_seen=s.get("first_seen", ""),
                last_seen=s.get("last_seen", ""),
                item_count=s.get("item_count", 0),
                source_count=s.get("source_count", 0),
                trend_score=s.get("trend_score", 0),
                confidence=s.get("confidence", "low"),
                direction=s.get("direction", "new"),
            )
            for s in stories
        ],
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
        first_seen=story_data.get("first_seen", ""),
        last_seen=story_data.get("last_seen", ""),
        item_ids=story_data.get("item_ids", []),
        metrics=story_data.get("metrics", []),
    )


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
    conn: sqlite3.Connection = Depends(_get_db),
) -> list[SourceHealthOut]:
    """Source health для run."""
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
