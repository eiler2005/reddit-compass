"""UI routes: /today, /stories/{id}, /explore, /runs.

Jinja2 templates с autoescape. Security headers.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from collections.abc import Generator
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import DEFAULT_PROFILE
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
    profile: str = DEFAULT_PROFILE,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Главная страница: briefing на сегодня."""
    from .query_service import (
        build_freshness_line,
        build_run_summary,
        build_source_coverage,
        build_theme_clouds,
    )

    if date is None:
        row = conn.execute(
            "SELECT snapshot_date FROM runs ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        date = row[0] if row else None

    if date is None:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Нет данных. Запустите `reddit-compass run`."},
        )

    briefing = get_briefing(conn, date, profile)
    if briefing is None:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": f"Briefing не найден для {date}."},
        )

    view = briefing_to_view(briefing)

    # Run summary и source coverage
    run_summary = build_run_summary(conn, date, profile)
    source_coverage = build_source_coverage(conn, f"{date}:{profile}", date)
    freshness_line = build_freshness_line(run_summary) if run_summary else ""

    # Theme clouds
    from ..config import MonitorConfig

    config = MonitorConfig.from_profile(profile)
    theme_catalog = [{"id": t.id, "label": t.label} for t in config.themes]
    stable_themes, emerging_candidates, pain_point_cloud = build_theme_clouds(
        conn, f"{date}:{profile}", theme_catalog
    )
    view.stable_themes = stable_themes
    view.emerging_candidates = emerging_candidates
    view.pain_point_cloud = pain_point_cloud

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
        request=request,
        name="today.html",
        context={
            "briefing": view,
            "run_summary": run_summary,
            "source_coverage": source_coverage,
            "freshness_line": freshness_line,
            "csrf_token": _generate_csrf_token(),
        },
    )


@router.get("/stories/{story_id}", response_class=HTMLResponse)
async def story_page(
    request: Request,
    story_id: str,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Страница story: timeline, evidence, research state."""
    story_data = get_story(conn, story_id)
    if story_data is None:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": f"Story {story_id} не найден."},
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
        request=request,
        name="story.html",
        context={
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
    status: str | None = None,
    saved: bool | None = None,
    sort: str = "trend_score",
    page: int = 1,
    page_size: int = 50,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Explore: поиск и фильтрация stories."""
    page_size = min(max(page_size, 10), 100)
    page = max(page, 1)

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

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    pagination_params = {
        key: value
        for key, value in {
            "q": q,
            "date": date,
            "profile": profile,
            "theme": theme,
            "candidate_theme": candidate_theme,
            "domain": domain,
            "pain": pain,
            "provider": provider,
            "source_cluster": source_cluster,
            "direction": direction,
            "confidence": confidence,
            "status": status,
            "saved": str(saved).lower() if saved is not None else None,
            "sort": sort,
            "page_size": str(page_size),
        }.items()
        if value
    }

    def page_url(page_number: int) -> str:
        return f"/explore?{urlencode({**pagination_params, 'page': str(page_number)})}"

    from .view_models import StoryCardView, direction_label, provider_label

    # Batch-load primary evidence for each story
    story_ids = [s["story_id"] for s in stories]
    evidence_map: dict[str, dict[str, str]] = {}
    if story_ids:
        placeholders = ",".join("?" * len(story_ids))
        rows = conn.execute(
            f"""SELECT si.story_id, i.canonical_url, i.provider, i.title
                FROM story_items si
                JOIN items i ON si.item_id = i.item_id
                WHERE si.story_id IN ({placeholders})
                ORDER BY si.story_id,
                    CASE i.content_scope
                        WHEN 'full' THEN 0 WHEN 'excerpt' THEN 1
                        WHEN 'abstract' THEN 2 ELSE 3 END
                LIMIT 500""",
            story_ids,
        ).fetchall()
        for row in rows:
            sid = row["story_id"]
            if sid not in evidence_map:
                evidence_map[sid] = {
                    "url": row["canonical_url"],
                    "provider": row["provider"],
                    "provider_label": provider_label(row["provider"]),
                }

    story_views = []
    for s in stories:
        ev = evidence_map.get(s["story_id"], {})
        story_views.append(
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
                clusters=[source_cluster] if source_cluster else [],
                primary_evidence_url=ev.get("url", ""),
                primary_evidence_provider=ev.get("provider", ""),
                primary_evidence_provider_label=ev.get("provider_label", ""),
            )
        )

    return templates.TemplateResponse(
        request=request,
        name="explore.html",
        context={
            "stories": story_views,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "filters": {
                "q": q,
                "date": date,
                "profile": profile,
                "domain": domain,
                "pain": pain,
                "provider": provider,
                "source_cluster": source_cluster,
                "theme": theme,
                "candidate_theme": candidate_theme,
                "direction": direction,
                "confidence": confidence,
                "sort": sort,
            },
            "prev_url": page_url(page - 1) if page > 1 else "",
            "next_url": page_url(page + 1) if page < total_pages else "",
        },
    )


@router.get("/runs", response_class=HTMLResponse)
async def runs_page(
    request: Request,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Список runs."""
    rows = conn.execute("SELECT * FROM runs ORDER BY snapshot_date DESC LIMIT 30").fetchall()

    from .view_models import RunView, status_label

    runs = []
    for row in rows:
        run_id = row["run_id"]
        date = row["snapshot_date"]

        # Item count
        item_count = conn.execute(
            "SELECT COUNT(DISTINCT item_id) FROM items WHERE snapshot_date = ?",
            (date,),
        ).fetchone()[0]

        # Story count
        story_count = conn.execute(
            "SELECT COUNT(*) FROM story_metrics WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]

        runs.append(
            RunView(
                run_id=run_id,
                date=date,
                profile=row["profile"],
                status=row["status"],
                status_label=status_label(row["status"]),
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                item_count=item_count,
                story_count=story_count,
            )
        )

    return templates.TemplateResponse(
        request=request,
        name="runs.html",
        context={"runs": runs},
    )


@router.post("/ui/stories/{story_id}/research-state")
async def update_research_state_endpoint(
    story_id: str,
    saved: bool = Form(False),
    status: str = Form("unread"),
    note: str = Form(""),
    return_to: str = Form("/today"),
    csrf_token: str = Form(""),
    conn: sqlite3.Connection = Depends(_get_db),
) -> Response:
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
async def dashboard_redirect() -> RedirectResponse:
    """Legacy redirect: /dashboard → /today."""
    return RedirectResponse(url="/today", status_code=302)


@router.get("/radar", response_class=HTMLResponse)
async def radar_redirect(
    conn: sqlite3.Connection = Depends(_get_db),
) -> RedirectResponse:
    """Redirect на последний доступный Radar."""
    from .query_service import resolve_latest_run

    date = resolve_latest_run(conn)
    if date is None:
        return RedirectResponse(url="/today", status_code=302)
    return RedirectResponse(url=f"/runs/{date}/radar", status_code=302)


@router.get("/runs/{date}/radar", response_class=HTMLResponse)
async def radar_page(
    request: Request,
    date: str,
    profile: str = DEFAULT_PROFILE,
    mode: str = "broad",
    domain: str | None = None,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Полный аналитический Radar."""
    from ..config import MonitorConfig
    from .query_service import (
        build_domain_matrix,
        build_domain_summaries,
        build_goal_relevance_rankings,
        build_raw_popular_items,
        build_run_summary,
        build_source_coverage,
        build_theme_clouds,
        build_trend_shelves,
        build_trend_strength,
    )
    from .view_models import RadarPageView, domain_label

    run_id = f"{date}:{profile}"
    run_summary = build_run_summary(conn, date, profile)

    if run_summary is None:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": f"Run не найден для {date}."},
        )

    # Source coverage
    source_coverage = build_source_coverage(conn, run_id, date)

    # Briefing для stories
    briefing = get_briefing(conn, date, profile)
    top_changes = []
    mega_stories = []
    watchlist = []
    column_ideas = []
    narrative_shifts = []

    if briefing:
        view = briefing_to_view(briefing)
        top_changes = view.top_changes
        mega_stories = view.mega_stories
        watchlist = view.watchlist
        column_ideas = view.column_ideas
        narrative_shifts = view.narrative_shifts

    # Theme clouds
    config = MonitorConfig.from_profile(profile)
    theme_catalog = [{"id": t.id, "label": t.label} for t in config.themes]
    stable_themes, emerging_candidates, pain_point_cloud = build_theme_clouds(
        conn, run_id, theme_catalog
    )

    # Trend strength
    trend_strength_rows = build_trend_strength(conn, run_id)

    # Raw popular items
    raw_popular_items = build_raw_popular_items(conn, date, profile=profile)

    # Goal relevance rankings
    goals = [g.id for g in config.goals]
    goal_relevance_rankings = build_goal_relevance_rankings(conn, run_id, goals)
    domain_summaries = build_domain_summaries(conn, run_id)
    domain_matrix = build_domain_matrix(conn, run_id)
    shelf_domain = domain
    if mode == "ai-native" and shelf_domain is None:
        shelf_domain = "ai_technology"
    trend_shelves = build_trend_shelves(conn, run_id, domain=shelf_domain)

    # Prev/next dates
    prev_row = conn.execute(
        "SELECT snapshot_date FROM runs WHERE snapshot_date < ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (date,),
    ).fetchone()
    next_row = conn.execute(
        "SELECT snapshot_date FROM runs WHERE snapshot_date > ? ORDER BY snapshot_date ASC LIMIT 1",
        (date,),
    ).fetchone()

    radar = RadarPageView(
        date=date,
        profile=profile,
        mode=mode,
        selected_domain=domain,
        selected_domain_label=domain_label(domain) if domain else "",
        run=run_summary,
        source_coverage=source_coverage,
        domain_summaries=domain_summaries,
        domain_matrix=domain_matrix,
        trend_shelves=trend_shelves,
        top_changes=top_changes,
        mega_stories=mega_stories,
        watchlist=watchlist,
        stable_themes=stable_themes,
        emerging_candidates=emerging_candidates,
        pain_point_cloud=pain_point_cloud,
        goal_relevance_rankings=goal_relevance_rankings,
        trend_strength_rows=trend_strength_rows,
        column_ideas=column_ideas,
        narrative_shifts=narrative_shifts,
        raw_popular_items=raw_popular_items,
        prev_date=prev_row[0] if prev_row else None,
        next_date=next_row[0] if next_row else None,
    )

    return templates.TemplateResponse(
        request=request,
        name="radar.html",
        context={"radar": radar},
    )
