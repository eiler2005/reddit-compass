"""UI routes: /today, /stories/{id}, /explore, /runs.

Jinja2 templates с autoescape. Security headers.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from collections.abc import Generator
from dataclasses import asdict
from math import log1p
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import DEFAULT_PROFILE
from ..intelligence.engine import (
    DEFAULT_ENGINE_DB_PATH,
    engine_db,
    get_current_publication,
    get_data_release,
    get_publication,
    label_engine_target,
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


_LIFECYCLE_LABELS = {
    "new": "новое",
    "growing": "растёт",
    "stable": "стабильно",
    "fading": "остывает",
    "resurfacing": "вернулось",
    "insufficient_history": "мало истории",
}

_LIFECYCLE_HINTS = {
    "new": "Впервые заметно в текущем окне.",
    "growing": "Скорость выше предыдущего baseline.",
    "stable": "Держится без резкого ускорения.",
    "fading": "Новых подтверждений стало меньше.",
    "resurfacing": "Сюжет вернулся после паузы.",
    "insufficient_history": (
        "Истории пока меньше нужного окна; динамика не классифицируется как рост/падение."
    ),
}

_SCOPE_LABELS = {
    "cross_source": "Reddit + СМИ",
    "community_only": "только сообщества",
    "mainstream_only": "только СМИ",
}

_REVIEW_LABELS = {
    "confirmed": "проверено",
    "pending": "машинный кандидат",
    "rejected": "отклонено",
    "legacy": "legacy",
}

_PROVIDER_LABELS = {
    "reddit": "Reddit",
    "hn": "Hacker News",
    "hackernews": "Hacker News",
    "producthunt": "Product Hunt",
    "reuters": "Reuters",
    "bbc": "BBC",
    "guardian": "Guardian",
    "nytimes": "NYT",
    "nyt": "NYT",
    "financial_times": "FT",
    "ft": "FT",
    "techcrunch": "TechCrunch",
    "the_verge": "The Verge",
    "verge": "The Verge",
    "ars_technica": "Ars Technica",
    "wired": "Wired",
}

_READING_DOMAIN_WEIGHTS = {
    "ai_technology": 26.0,
    "labor_career": 23.0,
    "business_markets": 23.0,
    "security_privacy": 20.0,
    "society_politics": 17.0,
    "world_geopolitics": 16.0,
    "finance_consumer": 15.0,
    "culture_media": 12.0,
    "science_health_education": 10.0,
    "climate_energy_infrastructure": 9.0,
    "sports": 5.0,
    "other": -5.0,
}

_READING_PROVIDER_WEIGHTS = {
    "reuters": 14.0,
    "financial_times": 14.0,
    "ft": 14.0,
    "nytimes": 13.0,
    "nyt": 13.0,
    "bbc": 12.0,
    "guardian": 12.0,
    "wired": 11.0,
    "techcrunch": 10.0,
    "the_verge": 10.0,
    "verge": 10.0,
    "ars_technica": 10.0,
    "hn": 9.0,
    "hackernews": 9.0,
    # Reddit is a useful early/community signal, but raw engagement must not
    # crowd out reported or primary-source material in the daily reading list.
    "reddit": 7.0,
    "producthunt": 7.0,
}

_READING_CLUSTER_WEIGHTS = {
    "business": 8.0,
    "mainstream": 8.0,
    "technology": 7.0,
    "voices": 7.0,
    "product": 6.0,
    "culture": 5.0,
}


def _decorate_today_trend(trend: dict[str, object], analysis_query: str) -> dict[str, object]:
    enriched = dict(trend)
    lifecycle = str(enriched.get("lifecycle") or "insufficient_history")
    source_scope = str(enriched.get("source_scope") or "")
    review_status = str(enriched.get("review_status") or "pending")
    trend_id = str(enriched.get("trend_id") or "")
    confidence_raw = enriched.get("confidence")
    confidence = float(confidence_raw) if isinstance(confidence_raw, int | float | str) else 0.0
    enriched["url"] = (
        f"/trends/{trend_id}?{analysis_query}" if trend_id else f"/trends?{analysis_query}"
    )
    enriched["lifecycle_label"] = _LIFECYCLE_LABELS.get(lifecycle, lifecycle)
    enriched["lifecycle_hint"] = _LIFECYCLE_HINTS.get(lifecycle, "")
    enriched["source_scope_label"] = _SCOPE_LABELS.get(source_scope, source_scope)
    enriched["review_label"] = _REVIEW_LABELS.get(review_status, review_status)
    enriched["confidence_pct"] = round(confidence * 100)
    return enriched


def _json_dict_value(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list_value(raw: object, fallback: list[str] | None = None) -> list[str]:
    if isinstance(raw, list):
        return [str(value) for value in raw]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(value) for value in parsed]
    return list(fallback or [])


def _provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, provider.replace("_", " ").title())


def _domain_label(domain_id: str) -> str:
    return BROAD_DOMAINS[domain_id].label_ru if domain_id in BROAD_DOMAINS else domain_id


def _float_metric(raw: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return 0.0


def _normalized_reading_key(title: str, primary_url: str, secondary_url: str) -> str:
    url = primary_url or secondary_url
    if url:
        parsed = urlsplit(url)
        return f"{parsed.netloc.lower()}{parsed.path.rstrip('/').lower()}"
    return " ".join(title.lower().split())[:120]


def _numeric_dict_value(raw: dict[str, Any], key: str) -> float:
    value = raw.get(key, 0.0)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _is_low_signal_reading_title(title: str) -> bool:
    """Keep recurring community housekeeping out of the human reading queue."""
    title_lower = title.lower()
    routine_markers = (
        "game thread",
        "daily discussion",
        "megathread",
        "daily questions",
        "weekly discussion",
        "what are you playing",
    )
    words = [word for word in title_lower.split() if any(char.isalpha() for char in word)]
    return len(words) < 3 or any(marker in title_lower for marker in routine_markers)


def _item_reading_score(
    *,
    row: sqlite3.Row,
    domain_ids: list[str],
    project_scores: dict[str, Any],
    raw_engagement: dict[str, Any],
    radar_date: str,
) -> float:
    provider = str(row["provider"] or "").lower()
    source_cluster = str(row["source_cluster"] or "").lower()
    content_scope = str(row["content_scope"] or "headline")
    title = str(row["title"] or "")
    score = max(
        (_READING_DOMAIN_WEIGHTS.get(domain_id, 0.0) for domain_id in domain_ids),
        default=0.0,
    )
    score += _READING_PROVIDER_WEIGHTS.get(provider, 4.0)
    score += _READING_CLUSTER_WEIGHTS.get(source_cluster, 2.0)
    score += min(
        28.0,
        0.25 * _numeric_dict_value(project_scores, "rbc")
        + 0.18 * _numeric_dict_value(project_scores, "book")
        + 0.12 * _numeric_dict_value(project_scores, "business"),
    )
    if provider in {"reddit", "hn", "hackernews", "producthunt"}:
        # Engagement is measured differently across platforms.  It is a small
        # within-channel tiebreaker here, never a global popularity contest.
        score += min(
            8.0,
            log1p(_float_metric(raw_engagement, "score", "points", "votes")) * 1.0,
        )
        score += min(
            5.0,
            log1p(_float_metric(raw_engagement, "comments", "comment_count", "num_comments")) * 0.9,
        )
    if content_scope == "full":
        score += 5.0
    elif content_scope == "excerpt":
        score += 3.0
    elif content_scope == "abstract":
        score += 2.0
    if str(row["snapshot_date"] or "") == radar_date:
        score += 8.0
    if int(row["story_source_count"] or 0) > 1:
        score += 5.0
    if _is_low_signal_reading_title(title):
        score -= 35.0
    return score


def _build_today_reading_list(
    conn: sqlite3.Connection, radar: dict[str, object], *, limit: int = 20
) -> list[dict[str, object]]:
    data_release_id = str(radar.get("data_release_id") or "")
    story_release_id = str(radar.get("story_release_id") or "")
    radar_date = str(radar.get("date") or "")
    if not data_release_id or not story_release_id:
        return []
    facet_row = conn.execute(
        "SELECT facet_release_id FROM story_releases WHERE story_release_id = ?",
        (story_release_id,),
    ).fetchone()
    if facet_row is None:
        return []
    rows = conn.execute(
        """
        SELECT
            i.item_id,
            i.provider,
            i.source_cluster,
            i.source_section,
            i.title,
            i.canonical_url,
            i.discussion_url,
            i.target_url,
            i.published_at,
            i.observed_at,
            i.snapshot_date,
            i.content_scope,
            i.raw_engagement,
            i.domain_ids AS item_domain_ids,
            f.domain_ids AS facet_domain_ids,
            COALESCE(si.story_id, '') AS story_id,
            COALESCE(s.project_scores, '{}') AS project_scores,
            COALESCE(s.source_count, 0) AS story_source_count
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
        LIMIT 10000
        """,
        (facet_row["facet_release_id"], story_release_id, story_release_id, data_release_id),
    ).fetchall()
    candidates: list[dict[str, object]] = []
    for row in rows:
        title = str(row["title"] or "").strip()
        if not title:
            continue
        provider = str(row["provider"] or "")
        discussion_url = _safe_url(str(row["discussion_url"] or ""))
        target_url = _safe_url(str(row["target_url"] or ""))
        canonical_url = _safe_url(str(row["canonical_url"] or ""))
        if provider == "reddit":
            # A Reddit link-post should lead to the underlying material first;
            # the discussion stays one click away.  Self posts still open at
            # the Reddit discussion URL.
            primary_url = target_url or discussion_url or canonical_url
            secondary_url = (
                discussion_url if discussion_url and discussion_url != primary_url else ""
            )
            secondary_label = "Обсуждение" if secondary_url else ""
        else:
            primary_url = canonical_url or target_url
            secondary_url = (
                discussion_url if discussion_url and discussion_url != primary_url else ""
            )
            secondary_label = "Reddit" if secondary_url else ""
        if not primary_url:
            continue
        domain_ids = _json_list_value(row["facet_domain_ids"] or row["item_domain_ids"], ["other"])
        project_scores = _json_dict_value(row["project_scores"])
        raw_engagement = _json_dict_value(row["raw_engagement"])
        score = _item_reading_score(
            row=row,
            domain_ids=domain_ids,
            project_scores=project_scores,
            raw_engagement=raw_engagement,
            radar_date=radar_date,
        )
        candidates.append(
            {
                "item_id": row["item_id"],
                "title": title,
                "provider": provider,
                "provider_label": _provider_label(provider),
                "source_cluster": row["source_cluster"],
                "source_section": row["source_section"],
                "primary_url": primary_url,
                "secondary_url": secondary_url,
                "secondary_label": secondary_label,
                "story_id": row["story_id"],
                "story_source_count": int(row["story_source_count"] or 0),
                "domain_ids": domain_ids[:3],
                "domain_labels": [_domain_label(domain_id) for domain_id in domain_ids[:3]],
                "published_at": row["published_at"] or row["observed_at"] or row["snapshot_date"],
                "score": round(score, 1),
                "reason": " / ".join(
                    value
                    for value in [
                        "профиль РБК" if _numeric_dict_value(project_scores, "rbc") >= 70 else "",
                        "книга" if _numeric_dict_value(project_scores, "book") >= 60 else "",
                        "Reddit-сигнал" if provider == "reddit" else "",
                        "cross-source story" if int(row["story_source_count"] or 0) > 1 else "",
                    ]
                    if value
                )
                or "ежедневное чтение",
                # One story should appear once even when it has a Reddit
                # discussion plus several articles.  URLs are the fallback
                # when the current StoryRelease has not joined the item yet.
                "_dedupe_key": str(row["story_id"] or "")
                or _normalized_reading_key(title, target_url or canonical_url, primary_url),
                "_primary_domain": domain_ids[0] if domain_ids else "other",
                "_is_current_day": str(row["snapshot_date"] or "") == radar_date,
            }
        )

    def sort_key(item: dict[str, object]) -> tuple[float, str]:
        score_raw = item.get("score", 0.0)
        score = float(score_raw) if isinstance(score_raw, int | float | str) else 0.0
        return (-score, str(item.get("title", "")))

    candidates.sort(key=sort_key)
    selected: list[dict[str, object]] = []
    seen: set[str] = set()
    provider_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    reddit_count = 0
    # A daily reading list is current-day first; a smaller collection falls
    # back to the rest of the immutable release to retain a useful 20 items.
    ordered_candidates = [item for item in candidates if bool(item["_is_current_day"])] + [
        item for item in candidates if not bool(item["_is_current_day"])
    ]
    for item in ordered_candidates:
        key = str(item["_dedupe_key"])
        provider = str(item["provider"])
        primary_domain = str(item["_primary_domain"])
        if key in seen:
            continue
        if provider == "reddit" and reddit_count >= 6:
            continue
        if provider_counts.get(provider, 0) >= 4:
            continue
        if domain_counts.get(primary_domain, 0) >= 5:
            continue
        seen.add(key)
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        domain_counts[primary_domain] = domain_counts.get(primary_domain, 0) + 1
        if provider == "reddit":
            reddit_count += 1
        item.pop("_dedupe_key", None)
        item.pop("_primary_domain", None)
        item.pop("_is_current_day", None)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def _load_today_engine_radar(
    conn: sqlite3.Connection, *, date: str | None, profile: str
) -> Any | None:
    """Resolve the published (or evaluated preview) broad radar for Today."""
    from .v2 import _engine_radar, _resolve_latest_evaluated_preview

    publication = get_current_publication(conn, "broad")
    if publication:
        release = get_data_release(conn, publication.data_release_id)
    else:
        preview = _resolve_latest_evaluated_preview(conn, channel="broad")
        release = preview[1] if preview else None
    engine_date = date or (max(release.dates) if release and release.dates else None)
    if not engine_date:
        return None
    return _engine_radar(
        conn,
        date=engine_date,
        profile=profile,
        mode="broad",
        domain=None,
        channel="broad",
        publication_id=None,
    )


def _build_today_dashboard(
    radar: dict[str, object], changes: list[dict[str, object]], analysis_query: str
) -> dict[str, object]:
    run_raw = radar.get("run")
    run = cast(dict[str, Any], run_raw) if isinstance(run_raw, dict) else {}
    shelves_raw = radar.get("shelves")
    shelves = cast(dict[str, Any], shelves_raw) if isinstance(shelves_raw, dict) else {}
    all_trends = [
        trend
        for trends in shelves.values()
        if isinstance(trends, list)
        for trend in trends
        if isinstance(trend, dict)
    ]
    source_coverage_raw = run.get("source_coverage")
    source_coverage = (
        cast(dict[str, Any], source_coverage_raw) if isinstance(source_coverage_raw, dict) else {}
    )
    matrix_raw = radar.get("matrix")
    matrix = matrix_raw if isinstance(matrix_raw, list) else []
    source_clusters = sorted(
        {
            str(row.get("source_cluster"))
            for row in matrix
            if isinstance(row, dict) and row.get("source_cluster")
        }
    )
    domains_raw = radar.get("domains")
    domains = domains_raw if isinstance(domains_raw, list) else []
    top_domains: list[dict[str, object]] = []
    for domain in domains[:8]:
        if not isinstance(domain, dict) or not domain.get("domain_id"):
            continue
        domain_id = str(domain["domain_id"])
        domain_query = urlencode({"channel": "broad", "domain": domain_id})
        enriched_domain = dict(domain)
        enriched_domain["news_url"] = f"/news?{domain_query}"
        enriched_domain["stories_url"] = f"/stories?{domain_query}"
        enriched_domain["trends_url"] = f"/trends?{domain_query}"
        enriched_domain["radar_url"] = f"/runs/{radar.get('date')}/radar?{domain_query}"
        top_domains.append(enriched_domain)
    release_dates = run.get("release_dates", [])
    if not isinstance(release_dates, list):
        release_dates = []
    status_notes: list[dict[str, str]] = []
    if radar.get("input_status") == "partial":
        status_notes.append(
            {
                "title": "Partial",
                "text": (
                    "Выпуск опубликован с неполным входным сбором. Аналитика доступна, "
                    "но покрытие источников нужно читать как частичное."
                ),
            }
        )
    if radar.get("history_status") == "insufficient_history":
        status_notes.append(
            {
                "title": "Мало истории",
                "text": (
                    "Engine пока не накопил достаточно последовательных релизов, поэтому "
                    "не рисует честные growing/fading и помечает карточки как кандидаты."
                ),
            }
        )
    if radar.get("preview"):
        status_notes.append(
            {
                "title": "Preview",
                "text": "Показан latest evaluated release, а не production RadarPublication.",
            }
        )
    if radar.get("serving_previous_publication"):
        status_notes.append(
            {
                "title": "Предыдущая публикация",
                "text": (
                    "Для выбранной даты нет отдельной публикации, показана последняя проверенная."
                ),
            }
        )

    return {
        "item_count": int(run.get("item_count") or 0) if isinstance(run, dict) else 0,
        "source_count": len(source_coverage),
        "source_cluster_count": len(source_clusters),
        "trend_count": len(all_trends),
        "cross_source_trend_count": sum(
            1
            for trend in all_trends
            if str(trend.get("source_scope") or "") == "cross_source"
            or int(trend.get("source_count") or 0) > 1
        ),
        "confirmed_trend_count": sum(
            1 for trend in all_trends if str(trend.get("review_status") or "") == "confirmed"
        ),
        "candidate_trend_count": sum(
            1 for trend in all_trends if str(trend.get("review_status") or "pending") != "confirmed"
        ),
        "change_count": len(changes),
        "top_domains": top_domains,
        "source_clusters": source_clusters,
        "release_window": " → ".join([str(release_dates[0]), str(release_dates[-1])])
        if release_dates
        else "",
        "status_notes": status_notes,
        "quick_links": [
            ("News", f"/news?{analysis_query}", "сырые материалы"),
            ("Stories", f"/stories?{analysis_query}", "конкретные события"),
            ("Trends", f"/trends?{analysis_query}", "паттерны поверх событий"),
            ("Reddit Pulse", "/pulse", "сигналы сообществ"),
            ("Radar", f"/runs/{radar.get('date')}/radar?{analysis_query}", "полный workspace"),
        ],
    }


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
        engine_conn = open_engine_readonly(engine_path)
        try:
            published_radar = _load_today_engine_radar(engine_conn, date=date, profile=profile)
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
            analysis_query = _analysis_query("broad", None)
            radar_payload = published_radar.model_dump()
            decorated_changes = [
                _decorate_today_trend(dict(trend), analysis_query) for trend in changes
            ]
            return templates.TemplateResponse(
                request=request,
                name="engine_today.html",
                context={
                    "radar": radar_payload,
                    "changes": decorated_changes,
                    "dashboard": _build_today_dashboard(
                        radar_payload,
                        decorated_changes,
                        analysis_query,
                    ),
                    "reading_endpoint": "/ui/today-reading?"
                    + urlencode({"date": radar_payload["date"], "profile": profile}),
                    "analysis_query": analysis_query,
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


@router.get("/ui/today-reading")
async def today_reading_feed(
    date: str | None = None,
    profile: str = DEFAULT_PROFILE,
) -> dict[str, object]:
    """Compact, safe JSON feed for the progressive Today reading queue."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return {"date": date or "", "items": []}
    engine_conn = open_engine_readonly(engine_path)
    try:
        radar = _load_today_engine_radar(engine_conn, date=date, profile=profile)
        if radar is None:
            return {"date": date or "", "items": []}
        radar_payload = radar.model_dump()
        return {
            "date": radar_payload["date"],
            "items": _build_today_reading_list(engine_conn, radar_payload, limit=20),
        }
    except HTTPException:
        # Today itself can still render its publication/preview state.  Do not
        # turn an unavailable optional reading feed into a broken dashboard.
        return {"date": date or "", "items": []}
    finally:
        engine_conn.close()


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
        _safe_url,
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
                    "discussion_url": _safe_url(r["discussion_url"]),
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


@router.post("/ui/engine/feedback")
async def engine_feedback_endpoint(
    target_kind: str = Form("story"),
    target_id: str = Form(""),
    release_id: str = Form(""),
    value: str = Form("useful"),
    return_to: str = Form("/today"),
    csrf_token: str = Form(""),
) -> Response:
    """Фаза 7: обратная связь в один клик (полезно/мусор) → engine_labels.

    Ежедневное использование пополняет golden set без отдельной разметки.
    """

    if not _validate_csrf_token(csrf_token):
        return Response(status_code=403)
    if not return_to.startswith("/"):
        return_to = "/today"
    if target_kind not in {"story", "trend"} or not target_id or not release_id:
        return RedirectResponse(url=return_to, status_code=303)
    label = "useful" if value == "useful" else "useless"
    engine_path = _engine_path()
    engine_conn = engine_db(engine_path)
    try:
        label_engine_target(
            engine_conn,
            target_kind=target_kind,
            target_id=target_id,
            release_id=release_id,
            label=label,  # type: ignore[arg-type]
            note="ui_feedback",
        )
    finally:
        engine_conn.close()
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
        pulse_summary = None
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
            if published_radar is not None:
                from .v2 import _engine_pulse_signals, _latest_signal_release, _safe_url

                sig_id = _latest_signal_release(
                    engine_conn,
                    data_release_id=published_radar.data_release_id,
                    date=date,
                )
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
                            "discussion_url": _safe_url(r["discussion_url"]),
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
        finally:
            engine_conn.close()
        if published_radar is not None:
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
