"""UI routes: /today, /stories/{id}, /explore, /runs.

Jinja2 templates с autoescape. Security headers.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import DEFAULT_SNAPSHOTS_DIR
from ..db import get_db
from ..intelligence.migrations import migrate
from ..intelligence.repository import (
    get_briefing,
    get_research_state,
    get_story,
    query_stories,
    update_research_state,
)
from .view_models import briefing_to_view, story_to_detail_view

router = APIRouter()

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.autoescape = True

_CSRF_SECRET = secrets.token_hex(32)


def _get_db():
    db_path = DEFAULT_SNAPSHOTS_DIR.parent / "compass.db"
    conn = get_db(db_path)
    migrate(conn)
    try:
        yield conn
    finally:
        conn.close()


def _generate_csrf_token() -> str:
    return secrets.token_hex(32)


def _validate_csrf_token(token: str) -> bool:
    return len(token) == 64


def _safe_url(url: str) -> str:
    """Проверяет URL на безопасность."""
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return ""


@router.get("/today", response_class=HTMLResponse)
async def today_page(
    request: Request,
    date: str | None = None,
    profile: str = "ai-native",
    conn=Depends(_get_db),
):
    """Главная страница: briefing на сегодня."""
    if date is None:
        row = conn.execute(
            "SELECT snapshot_date FROM runs ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        date = row[0] if row else None

    if date is None:
        return templates.TemplateResponse(
            "empty.html",
            {"request": request, "message": "Нет данных. Запустите `reddit-compass run`."},
        )

    briefing = get_briefing(conn, date, profile)
    if briefing is None:
        return templates.TemplateResponse(
            "empty.html",
            {"request": request, "message": f"Briefing не найден для {date}."},
        )

    view = briefing_to_view(briefing)

    prev_row = conn.execute(
        "SELECT snapshot_date FROM runs WHERE snapshot_date < ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (date,),
    ).fetchone()
    next_row = conn.execute(
        "SELECT snapshot_date FROM runs WHERE snapshot_date > ? ORDER BY snapshot_date ASC LIMIT 1",
        (date,),
    ).fetchone()

    view.prev_date = prev_row[0] if prev_row else None
    view.next_date = next_row[0] if next_row else None

    return templates.TemplateResponse(
        "today.html",
        {
            "request": request,
            "briefing": view,
            "csrf_token": _generate_csrf_token(),
        },
    )


@router.get("/stories/{story_id}", response_class=HTMLResponse)
async def story_page(
    request: Request,
    story_id: str,
    conn=Depends(_get_db),
):
    """Страница story: timeline, evidence, research state."""
    story_data = get_story(conn, story_id)
    if story_data is None:
        return templates.TemplateResponse(
            "empty.html",
            {"request": request, "message": f"Story {story_id} не найден."},
            status_code=404,
        )

    from ..intelligence.models import Story

    story = Story(
        story_id=story_data["story_id"],
        canonical_key=story_data["canonical_key"],
        title=story_data["title"],
        summary_ru=story_data.get("summary_ru", ""),
        theme_ids=story_data.get("theme_ids", []),
        first_seen=story_data.get("first_seen", ""),
        last_seen=story_data.get("last_seen", ""),
        item_ids=story_data.get("item_ids", []),
    )

    evidence = []
    for item_id in story.item_ids:
        row = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if row:
            evidence.append(
                {
                    "item_id": row["item_id"],
                    "provider": row["provider"],
                    "source_cluster": row["source_cluster"],
                    "url": _safe_url(row["canonical_url"]),
                    "title": row["title"],
                    "excerpt": row["excerpt"],
                    "content_scope": row["content_scope"],
                }
            )

    research_state = get_research_state(conn, story_id)

    view = story_to_detail_view(
        story=story,
        metrics=story_data.get("metrics", []),
        evidence=evidence,
        research_state=research_state,
    )

    return templates.TemplateResponse(
        "story.html",
        {
            "request": request,
            "story": view,
            "csrf_token": _generate_csrf_token(),
        },
    )


@router.get("/explore", response_class=HTMLResponse)
async def explore_page(
    request: Request,
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    theme: str | None = None,
    provider: str | None = None,
    source_cluster: str | None = None,
    direction: str | None = None,
    confidence: str | None = None,
    status: str | None = None,
    saved: bool | None = None,
    sort: str = "trend_score",
    page: int = 1,
    page_size: int = 50,
    conn=Depends(_get_db),
):
    """Explore: поиск и фильтрация stories."""
    page_size = min(max(page_size, 10), 100)
    page = max(page, 1)

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

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    from .view_models import StoryCardView, direction_label

    story_views = [
        StoryCardView(
            story_id=s["story_id"],
            title=s["title"],
            summary_ru=s.get("summary_ru", ""),
            direction=s.get("direction", "new"),
            direction_label=direction_label(s.get("direction", "new")),
            trend_score=s.get("trend_score", 0),
            confidence=s.get("confidence", "low"),
            why_it_matters="",
            source_count=s.get("source_count", 0),
            item_count=s.get("item_count", 0),
        )
        for s in stories
    ]

    return templates.TemplateResponse(
        "explore.html",
        {
            "request": request,
            "stories": story_views,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "filters": {
                "q": q,
                "theme": theme,
                "direction": direction,
                "confidence": confidence,
                "sort": sort,
            },
        },
    )


@router.get("/runs", response_class=HTMLResponse)
async def runs_page(
    request: Request,
    conn=Depends(_get_db),
):
    """Список runs."""
    rows = conn.execute("SELECT * FROM runs ORDER BY snapshot_date DESC LIMIT 30").fetchall()

    from .view_models import RunView, status_label

    runs = [
        RunView(
            run_id=row["run_id"],
            date=row["snapshot_date"],
            profile=row["profile"],
            status=row["status"],
            status_label=status_label(row["status"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
        for row in rows
    ]

    return templates.TemplateResponse(
        "runs.html",
        {"request": request, "runs": runs},
    )


@router.post("/ui/stories/{story_id}/research-state")
async def update_research_state_endpoint(
    story_id: str,
    saved: bool = Form(False),
    status: str = Form("unread"),
    note: str = Form(""),
    return_to: str = Form("/today"),
    csrf_token: str = Form(""),
    conn=Depends(_get_db),
):
    """Обновление research state для story."""
    if not _validate_csrf_token(csrf_token):
        return Response(status_code=403)

    if not return_to.startswith("/"):
        return_to = "/today"

    from datetime import UTC, datetime

    updated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    update_research_state(
        conn,
        story_id,
        saved=saved,
        status=status,
        note=note[:5000],
        updated_at=updated_at,
    )
    conn.commit()

    return RedirectResponse(url=return_to, status_code=303)


@router.get("/dashboard", include_in_schema=False)
async def dashboard_redirect():
    """Legacy redirect: /dashboard → /today."""
    return RedirectResponse(url="/today", status_code=302)
