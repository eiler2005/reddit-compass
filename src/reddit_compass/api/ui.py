"""UI routes: /today, /stories/{id}, /explore, /runs.

Jinja2 templates с autoescape. Security headers.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from collections.abc import Generator
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import DEFAULT_PROFILE
from ..intelligence.engine import (
    DEFAULT_ENGINE_DB_PATH,
    get_current_publication,
    get_data_release,
    get_publication,
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


def _engine_path() -> Path:
    return Path(os.environ.get("RC_ENGINE_DB_PATH", str(DEFAULT_ENGINE_DB_PATH)))


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


def _analysis_query(channel: str = "broad", publication_id: str | None = None) -> str:
    params = {"channel": channel}
    if publication_id:
        params["publication_id"] = publication_id
    return urlencode(params)


@router.get("/today", response_class=HTMLResponse)
async def today_page(
    request: Request,
    date: str | None = None,
    profile: str = DEFAULT_PROFILE,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Главная страница: briefing на сегодня."""
    engine_path = _engine_path()
    if engine_path.exists():
        from .v2 import _engine_radar

        engine_conn = open_engine_readonly(engine_path)
        try:
            publication = get_current_publication(engine_conn, "broad")
            release = (
                get_data_release(engine_conn, publication.data_release_id) if publication else None
            )
            engine_date = date or (max(release.dates) if release and release.dates else None)
            published_radar = (
                _engine_radar(
                    engine_conn,
                    date=engine_date,
                    profile=profile,
                    mode="broad",
                    domain=None,
                    channel="broad",
                    publication_id=None,
                )
                if engine_date
                else None
            )
        finally:
            engine_conn.close()

        if published_radar is not None:
            lifecycle_order = (
                "growing",
                "new",
                "resurfacing",
                "stable",
                "insufficient_history",
                "fading",
            )
            changes = [
                trend
                for lifecycle in lifecycle_order
                for trend in published_radar.shelves.get(lifecycle, [])
            ][:10]
            return templates.TemplateResponse(
                request=request,
                name="engine_today.html",
                context={
                    "radar": published_radar.model_dump(),
                    "changes": changes,
                },
            )

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


@router.get("/news", response_class=HTMLResponse)
async def news_page(
    request: Request,
    date: str | None = None,
    domain: str | None = None,
    provider: str | None = None,
    source_cluster: str | None = None,
    q: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """News inbox: raw published items from immutable DataRelease."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_news

    engine_conn = open_engine_readonly(engine_path)
    try:
        news = _engine_news(
            engine_conn,
            channel=channel,
            publication_id=publication_id,
            date=date,
            domain=domain,
            provider=provider,
            source_cluster=source_cluster,
            q=q,
            page=max(page, 1),
            page_size=50,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="news.html",
        context={
            "news": news.model_dump(),
            "filters": {
                "date": date or "",
                "domain": domain or "",
                "provider": provider or "",
                "source_cluster": source_cluster or "",
                "q": q or "",
            },
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
        },
    )


@router.get("/stories", response_class=HTMLResponse)
async def stories_page(
    request: Request,
    domain: str | None = None,
    q: str | None = None,
    project_id: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Published story workspace: concrete events, not raw news."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_stories

    engine_conn = open_engine_readonly(engine_path)
    try:
        stories = _engine_stories(
            engine_conn,
            channel=channel,
            publication_id=publication_id,
            domain=domain,
            q=q,
            project_id=project_id,
            page=max(page, 1),
            page_size=50,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="engine_stories.html",
        context={
            "stories": stories.model_dump(),
            "filters": {"domain": domain or "", "q": q or "", "project_id": project_id or ""},
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
        },
    )


@router.get("/trends", response_class=HTMLResponse)
async def trends_page(
    request: Request,
    domain: str | None = None,
    lifecycle: str | None = None,
    review_status: str | None = None,
    project_id: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Published trend workspace: recurring patterns over stories."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_trends

    engine_conn = open_engine_readonly(engine_path)
    try:
        trends = _engine_trends(
            engine_conn,
            channel=channel,
            publication_id=publication_id,
            domain=domain,
            lifecycle=lifecycle,
            review_status=review_status,
            project_id=project_id,
            page=max(page, 1),
            page_size=50,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="engine_trends.html",
        context={
            "trends": trends.model_dump(),
            "filters": {
                "domain": domain or "",
                "lifecycle": lifecycle or "",
                "review_status": review_status or "",
                "project_id": project_id or "",
            },
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
        },
    )


@router.get("/pulse", response_class=HTMLResponse)
async def pulse_page(
    request: Request,
    sort: str = "pulse",
    signal_type: str | None = None,
    subreddit: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Reddit Pulse: Reddit-native community signals with raw engagement."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine DB не найдена."},
            status_code=404,
        )
    from .v2 import (
        _engine_pulse_signals,
        _latest_signal_release,
    )

    engine_conn = open_engine_readonly(engine_path)
    try:
        sig_id = _latest_signal_release(engine_conn)
        if not sig_id:
            return templates.TemplateResponse(
                request=request,
                name="empty.html",
                context={"message": "Нет Reddit Pulse данных."},
                status_code=404,
            )
        page_size = 30
        offset = (max(page, 1) - 1) * page_size
        signals, total = _engine_pulse_signals(
            engine_conn,
            sig_id,
            signal_type=signal_type,
            subreddit=subreddit,
            sort=sort,
            limit=page_size,
            offset=offset,
        )
        # Summary for KPI + categorized shelves (only on page 1, no filters)
        pulse_summary = None
        if page == 1 and not signal_type and not subreddit:
            top_pulse, _ = _engine_pulse_signals(engine_conn, sig_id, limit=8)
            top_pain, _ = _engine_pulse_signals(
                engine_conn, sig_id, signal_type="pain_point", limit=5
            )
            top_ai, _ = _engine_pulse_signals(
                engine_conn, sig_id, signal_type="ai_capability", limit=5
            )
            gap_rows = engine_conn.execute(
                "SELECT cs.signal_id, cs.item_id, cs.subreddit, "
                "cs.signal_type, cs.title, cs.discussion_url, "
                "cs.pulse_score, "
                "COALESCE(json_extract(ri.raw_engagement, '$.score'), 0) "
                "as reddit_score, "
                "COALESCE(json_extract(ri.raw_engagement, '$.comments'), 0) "
                "as reddit_comments, "
                "COALESCE(json_extract(ri.raw_engagement, "
                "'$.upvote_ratio'), 0) as upvote_ratio "
                "FROM community_signals cs "
                "LEFT JOIN release_items ri "
                "ON ri.item_id = cs.item_id "
                "AND ri.release_id = (SELECT data_release_id "
                "FROM signal_releases WHERE signal_release_id = ?) "
                "WHERE cs.signal_release_id = ? "
                "AND cs.pulse_score >= 60 "
                "AND cs.mainstream_coverage_count < 2 "
                "ORDER BY cs.pulse_score DESC LIMIT 5",
                (sig_id, sig_id),
            ).fetchall()
            mainstream_gap = [
                {
                    "signal_id": r["signal_id"],
                    "item_id": r["item_id"],
                    "subreddit": r["subreddit"],
                    "signal_type": r["signal_type"],
                    "title": r["title"],
                    "discussion_url": r["discussion_url"],
                    "pulse_score": r["pulse_score"],
                    "reddit_score": r["reddit_score"],
                    "reddit_comments": r["reddit_comments"],
                    "upvote_ratio": r["upvote_ratio"],
                }
                for r in gap_rows
            ]
            # By-type counts
            type_rows = engine_conn.execute(
                "SELECT signal_type, COUNT(*) as cnt "
                "FROM community_signals WHERE signal_release_id = ? "
                "GROUP BY signal_type ORDER BY cnt DESC",
                (sig_id,),
            ).fetchall()
            by_type = {r["signal_type"]: r["cnt"] for r in type_rows}
            pulse_summary = {
                "signal_release_id": sig_id,
                "total_signals": total,
                "by_type": by_type,
                "top_pulse": top_pulse,
                "top_pain": top_pain,
                "top_ai": top_ai,
                "mainstream_gap": mainstream_gap,
            }
        # Available signal types for dropdown
        type_rows = engine_conn.execute(
            "SELECT DISTINCT signal_type FROM community_signals "
            "WHERE signal_release_id = ? ORDER BY signal_type",
            (sig_id,),
        ).fetchall()
        signal_types = [r["signal_type"] for r in type_rows]
    finally:
        engine_conn.close()

    total_pages = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(
        request=request,
        name="engine_pulse.html",
        context={
            "signals": signals,
            "total": total,
            "page": max(page, 1),
            "page_size": page_size,
            "total_pages": total_pages,
            "pulse_summary": pulse_summary,
            "filters": {
                "sort": sort,
                "signal_type": signal_type or "",
                "subreddit": subreddit or "",
            },
            "signal_types": signal_types,
            "sort_options": [
                ("pulse", "Pulse score"),
                ("score", "Reddit score"),
                ("comments", "Comments"),
                ("velocity", "Velocity"),
                ("ratio", "Upvote ratio"),
            ],
        },
    )


@router.get("/trends/{trend_id}", response_class=HTMLResponse)
async def trend_detail_page(
    request: Request,
    trend_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
) -> HTMLResponse:
    """Published trend detail: pattern, member stories and evidence."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_trend_detail

    engine_conn = open_engine_readonly(engine_path)
    try:
        trend = _engine_trend_detail(
            engine_conn,
            trend_id=trend_id,
            channel=channel,
            publication_id=publication_id,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="engine_trend_detail.html",
        context={
            "trend": trend.model_dump(),
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
        },
    )


@router.get("/projects", response_class=HTMLResponse)
async def projects_redirect(
    channel: str = "broad",
    publication_id: str | None = None,
) -> RedirectResponse:
    return RedirectResponse(
        f"/projects/rbc?{_analysis_query(channel, publication_id)}",
        status_code=302,
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_lens_page(
    request: Request,
    project_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
) -> HTMLResponse:
    """Project lens over published stories and trends."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_project_lens

    engine_conn = open_engine_readonly(engine_path)
    try:
        lens = _engine_project_lens(
            engine_conn,
            project_id=project_id,
            channel=channel,
            publication_id=publication_id,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="project_lens.html",
        context={
            "lens": lens.model_dump(),
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
        },
    )


@router.get("/stories/{story_id}", response_class=HTMLResponse)
async def story_page(
    request: Request,
    story_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Страница story: timeline, evidence, research state."""
    engine_path = _engine_path()
    if engine_path.exists():
        from .v2 import _engine_story_detail

        engine_conn = open_engine_readonly(engine_path)
        try:
            engine_story = _engine_story_detail(
                engine_conn,
                story_id=story_id,
                channel=channel,
                publication_id=publication_id,
            )
        except HTTPException as exc:
            engine_story = None
            if exc.status_code != 404:
                return templates.TemplateResponse(
                    request=request,
                    name="empty.html",
                    context={"message": str(exc.detail)},
                    status_code=exc.status_code,
                )
        finally:
            engine_conn.close()
        if engine_story is not None:
            return templates.TemplateResponse(
                request=request,
                name="engine_story_detail.html",
                context={
                    "story": engine_story.model_dump(),
                    "channel": channel,
                    "publication_id": publication_id or "",
                    "analysis_query": _analysis_query(channel, publication_id),
                },
            )

    story_data = get_story(conn, story_id)
    if story_data is None:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": f"Story {story_id} не найден."},
            status_code=404,
        )

    from ..intelligence.models import Story

    legacy_story = Story(
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
    for item_id in legacy_story.item_ids:
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
        story=legacy_story,
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

        # Multi-item and cross-source counts
        multi_item = conn.execute(
            "SELECT COUNT(*) FROM story_metrics WHERE run_id = ? AND item_count >= 2",
            (run_id,),
        ).fetchone()[0]
        cross_source = conn.execute(
            "SELECT COUNT(*) FROM story_metrics WHERE run_id = ? AND source_count >= 2",
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
                multi_item_count=multi_item,
                cross_source_count=cross_source,
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


@router.get("/engine", response_class=HTMLResponse)
async def engine_page(request: Request) -> HTMLResponse:
    """Read-only control plane for immutable engine releases."""
    path = _engine_path()
    if not path.exists():
        return templates.TemplateResponse(
            request=request,
            name="engine.html",
            context={
                "available": False,
                "data_releases": [],
                "story_releases": [],
                "trend_releases": [],
                "publications": [],
                "current_publication": None,
            },
        )

    conn = open_engine_readonly(path)
    try:
        story_releases = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM story_releases ORDER BY created_at DESC"
            ).fetchall()
        ]
        trend_releases = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM trend_releases ORDER BY created_at DESC"
            ).fetchall()
        ]
        current = get_current_publication(conn, "broad")
        context = {
            "available": True,
            "data_releases": [asdict(release) for release in list_data_releases(conn)],
            "story_releases": story_releases,
            "trend_releases": trend_releases,
            "publications": [asdict(publication) for publication in list_publications(conn)],
            "current_publication": asdict(current) if current else None,
        }
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="engine.html",
        context=context,
    )


@router.get("/dashboard", include_in_schema=False)
async def dashboard_redirect() -> RedirectResponse:
    """Legacy redirect: /dashboard → /today."""
    return RedirectResponse(url="/today", status_code=302)


@router.get("/radar", response_class=HTMLResponse)
async def radar_redirect(
    channel: str = "broad",
    publication_id: str | None = None,
    conn: sqlite3.Connection = Depends(_get_db),
) -> RedirectResponse:
    """Redirect на последний доступный Radar."""
    from .query_service import resolve_latest_run

    engine_path = _engine_path()
    if engine_path.exists():
        engine_conn = open_engine_readonly(engine_path)
        try:
            publication = (
                get_publication(engine_conn, publication_id)
                if publication_id
                else get_current_publication(engine_conn, channel)
            )
            if publication:
                release = get_data_release(engine_conn, publication.data_release_id)
                if release and release.dates:
                    return RedirectResponse(
                        url=(
                            f"/runs/{max(release.dates)}/radar?"
                            f"{_analysis_query(channel, publication_id)}"
                        ),
                        status_code=302,
                    )
        finally:
            engine_conn.close()

    date = resolve_latest_run(conn)
    if date is None:
        return RedirectResponse(url="/today", status_code=302)
    return RedirectResponse(
        url=f"/runs/{date}/radar?{_analysis_query(channel, publication_id)}",
        status_code=302,
    )


@router.get("/runs/{date}/radar", response_class=HTMLResponse)
async def radar_page(
    request: Request,
    date: str,
    profile: str = DEFAULT_PROFILE,
    mode: str = "broad",
    domain: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
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

    engine_path = _engine_path()
    if engine_path.exists():
        from .v2 import _engine_radar

        engine_conn = open_engine_readonly(engine_path)
        try:
            published_radar = _engine_radar(
                engine_conn,
                date=date,
                profile=profile,
                mode=mode,
                domain=domain,
                channel=channel,
                publication_id=publication_id,
            )
        finally:
            engine_conn.close()
        if published_radar is not None:
            # Fetch Reddit Pulse summary for the Radar page
            pulse_summary = None
            try:
                from .v2 import _engine_pulse_signals, _latest_signal_release

                sig_id = _latest_signal_release(engine_conn)
                if sig_id:
                    top_pulse, _ = _engine_pulse_signals(engine_conn, sig_id, limit=8)
                    top_pain, _ = _engine_pulse_signals(
                        engine_conn,
                        sig_id,
                        signal_type="pain_point",
                        limit=5,
                    )
                    top_ai, _ = _engine_pulse_signals(
                        engine_conn,
                        sig_id,
                        signal_type="ai_capability",
                        limit=5,
                    )
                    # Mainstream gap: high pulse, low coverage
                    gap_rows = engine_conn.execute(
                        "SELECT signal_id, item_id, subreddit, "
                        "signal_type, title, discussion_url, "
                        "target_url, pulse_score, subreddit_percentile, "
                        "comment_velocity, discussion_depth, "
                        "cross_subreddit_repetition, novelty, "
                        "domain_ids_json, theme_ids_json, "
                        "pain_points_json "
                        "FROM community_signals "
                        "WHERE signal_release_id = ? "
                        "AND pulse_score >= 60 "
                        "AND mainstream_coverage_count < 2 "
                        "ORDER BY pulse_score DESC LIMIT 5",
                        (sig_id,),
                    ).fetchall()
                    mainstream_gap = [
                        {
                            "signal_id": r["signal_id"],
                            "item_id": r["item_id"],
                            "subreddit": r["subreddit"],
                            "signal_type": r["signal_type"],
                            "title": r["title"],
                            "discussion_url": r["discussion_url"],
                            "pulse_score": r["pulse_score"],
                        }
                        for r in gap_rows
                    ]
                    pulse_summary = {
                        "signal_release_id": sig_id,
                        "top_pulse": top_pulse,
                        "top_pain": top_pain,
                        "top_ai": top_ai,
                        "mainstream_gap": mainstream_gap,
                    }
            except Exception:
                pass  # Pulse data is optional
            return templates.TemplateResponse(
                request=request,
                name="engine_radar.html",
                context={
                    "radar": published_radar.model_dump(),
                    "analysis_query": _analysis_query(channel, publication_id),
                    "pulse": pulse_summary,
                },
            )

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
