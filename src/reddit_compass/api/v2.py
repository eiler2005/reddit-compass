"""API v2: source-agnostic endpoints для stories, briefings, items.

Все v2 endpoints используют существующий Bearer auth.
Naming: virality_events (crosspost/surge), item_signals (LLM), stories, briefings.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Generator, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import DEFAULT_PROFILE
from ..intelligence.engine import (
    DEFAULT_ENGINE_DB_PATH,
    Publication,
    compare_engine_versions,
    get_current_publication,
    get_data_release,
    get_facet_release,
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
from .dates import sort_key
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
    preview: bool = False
    candidate_count: int = 0
    confirmed_count: int = 0


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
    # Сила материала должна быть видна и машине, и интерфейсу: для News
    # первичны охват сюжета и engagement, свежесть — следующий критерий.
    story_source_count: int = 0
    story_item_count: int = 0
    raw_score: int = 0
    raw_comments: int = 0


class PaginatedNews(BaseModel):
    items: list[NewsItemOut]
    # ``total`` is the number of entries in the selected view.  In the
    # default story view it is intentionally smaller than ``item_total``:
    # several raw materials may be evidence for the same Story.
    total: int = Field(
        description=(
            "Строк в текущей проекции: при view=stories это число после схлопывания "
            "по сюжету, при view=items — число сырых материалов."
        )
    )
    item_total: int = Field(
        default=0,
        description="Число сырых материалов до схлопывания; при view=items равно total.",
    )
    page: int
    page_size: int
    publication_id: str
    data_release_id: str
    story_release_id: str
    trend_release_id: str
    sort: str = Field(default="strength", description="Фактический порядок выдачи.")
    view: str = Field(
        default="stories",
        description="Фактическая проекция: stories (по сюжету) либо items (все материалы).",
    )
    preview: bool = False


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
    # Какие кластеры источников стоят за событием, с числом материалов в каждом.
    # Раньше наружу выходило только их количество, поэтому «о чём молчат СМИ» —
    # главное отличие продукта — интерфейс показать не мог.
    source_clusters: dict[str, int] = Field(default_factory=dict)
    evidence_items: list[NewsItemOut] = Field(default_factory=list)


class PublishedStoryDetailOut(PublishedStoryOut):
    trends: list[TrendOut] = Field(default_factory=list)
    publication_id: str = ""
    data_release_id: str = ""
    story_release_id: str = ""
    trend_release_id: str = ""
    preview: bool = False


class PaginatedPublishedStories(BaseModel):
    items: list[PublishedStoryOut]
    total: int
    page: int
    page_size: int
    publication_id: str
    data_release_id: str
    story_release_id: str
    sort: str = "strength"
    preview: bool = False


class TrendOut(BaseModel):
    trend_id: str
    title: str
    pattern: str
    # Русское имя из трендового ревью: оригинальное имя остаётся в title,
    # это показывается рядом как подпись.
    review_name_ru: str = ""
    domain_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    lifecycle: str = "insufficient_history"
    source_scope: str = ""
    first_seen: str = ""
    last_seen: str = ""
    story_count: int = 0
    source_count: int = 0
    # Состав кластеров источников: source_count говорит только «сколько»,
    # а полосе в интерфейсе нужно «каких и в какой пропорции».
    source_clusters: dict[str, int] = Field(default_factory=dict)
    project_scores: dict[str, int] = Field(default_factory=dict)
    evidence_story_ids: list[str] = Field(default_factory=list)
    counterpoints: list[str] = Field(default_factory=list)
    review_status: str = "pending"
    # Иерархия theme → key event: пустой parent_trend_id — корень. Дети приезжают вместе
    # с корнем, чтобы карточка списка показывала рубрику заголовком над событиями и не
    # требовала второго запроса.
    parent_trend_id: str = ""
    children: list[TrendOut] = Field(default_factory=list)
    # Сами акторы, а не только их число: выигрыш типизации иначе виден метрике и невиден
    # человеку. Для schema_v2 `source_count` — это как раз len(distinct_actors).
    distinct_actors: list[str] = Field(default_factory=list)
    stories: list[PublishedStoryOut] = Field(default_factory=list)


class TrendDetailOut(TrendOut):
    publication_id: str = ""
    data_release_id: str = ""
    story_release_id: str = ""
    trend_release_id: str = ""
    preview: bool = False


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
    sort: str = "strength"
    preview: bool = False


class ProjectLensOut(BaseModel):
    project_id: str
    publication_id: str
    data_release_id: str
    story_release_id: str
    trend_release_id: str
    preview: bool = False
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
) -> tuple[Any, Any, Any, Any, bool]:
    publication = (
        get_publication(conn, publication_id)
        if publication_id
        else get_current_publication(conn, channel)
    )
    if publication is None:
        if not publication_id:
            preview = _resolve_latest_evaluated_preview(conn, channel=channel)
            if preview is not None:
                return (*preview, True)
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
    return publication, data_release, story_release, trend_release, False


def _resolve_latest_evaluated_preview(
    conn: sqlite3.Connection,
    *,
    channel: str,
) -> tuple[Publication, Any, Any, Any] | None:
    """Resolve latest evaluated TrendRelease as read-only preview when no publication exists."""
    row = conn.execute(
        """
        SELECT trend_release_id
        FROM trend_releases
        WHERE status IN ('evaluated', 'published')
        ORDER BY created_at DESC, trend_release_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    trend_release = get_trend_release(conn, str(row["trend_release_id"]))
    if trend_release is None:
        return None
    story_release = get_story_release(conn, trend_release.story_release_id)
    if story_release is None:
        return None
    facet_release = get_facet_release(conn, story_release.facet_release_id)
    if facet_release is None:
        return None
    data_release = get_data_release(conn, facet_release.data_release_id)
    if data_release is None:
        return None
    publication = Publication(
        publication_id="",
        channel=channel,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
        input_status=data_release.input_status,
        previous_publication_id="",
        created_at=trend_release.created_at,
    )
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
            i.raw_engagement,
            i.domain_ids AS item_domain_ids,
            f.domain_ids AS facet_domain_ids,
            f.theme_ids,
            f.pain_points,
            f.summary_ru AS facet_summary_ru,
            COALESCE(si.story_id, '') AS story_id,
            COALESCE(si.membership_reason, '') AS membership_reason,
            COALESCE(s.title, '') AS story_title,
            COALESCE(s.source_count, 0) AS story_source_count,
            COALESCE(s.item_count, 0) AS story_item_count
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
    raw_engagement = _json_int_dict(_row_value(row, "raw_engagement", ""))
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
        story_source_count=int(_row_value(row, "story_source_count", 0) or 0),
        story_item_count=int(_row_value(row, "story_item_count", 0) or 0),
        raw_score=raw_engagement.get("score", raw_engagement.get("points", 0)),
        raw_comments=raw_engagement.get("comments", raw_engagement.get("comment_count", 0)),
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


# Stories и Trends хранят чистый `YYYY-MM-DD`, поэтому им хватает строкового потолка;
# у News формат задаёт провайдер, и там работает `api.dates.sort_key`.
_UNDATED_LAST = "9999-12-31"

_NEWS_SORTS = frozenset({"strength", "fresh", "engagement", "oldest"})
_NEWS_VIEWS = frozenset({"stories", "items"})
_STORY_SORTS = frozenset({"strength", "fresh", "volume", "oldest"})
_TREND_SORTS = frozenset({"strength", "fresh", "coverage", "oldest"})


def _safe_sort(value: str, allowed: frozenset[str], *, default: str = "strength") -> str:
    """Return a supported sort token without exposing a free-form SQL order.

    Приведение к дефолту — поведение для **UI**: устаревшая закладка со снятым
    параметром не должна ронять страницу. У API оно недопустимо, поэтому эндпоинты
    сначала валидируют вход через `_require_supported`.
    """
    return value if value in allowed else default


def _require_supported(name: str, value: str, allowed: frozenset[str]) -> str:
    """Проверить значение перечислимого query-параметра или ответить 422.

    Молчаливое приведение к дефолту на API означало, что `?sort=freshness` (именно так
    параметр назван в части документации) отдавал порядок по силе и в ответе писал
    `"sort": "strength"` — клиент получал не то, что просил, без единого признака ошибки.
    """
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=(f"Unsupported {name}={value!r}. Supported: {', '.join(sorted(allowed))}."),
        )
    return value


def _news_raw_date(row: sqlite3.Row) -> str:
    return str(row["published_at"] or row["observed_at"] or row["snapshot_date"] or "")


def _news_date(row: sqlite3.Row, *, ascending: bool = False) -> str:
    """Сопоставимый ключ даты: провайдеры отдают ISO-8601 и RFC 2822 вперемешку."""
    return sort_key(_news_raw_date(row), ascending=ascending)


def _news_strength(row: sqlite3.Row) -> tuple[int, int, int, int, str, str]:
    raw = _json_int_dict(_row_value(row, "raw_engagement", ""))
    return (
        int(_row_value(row, "story_source_count", 0) or 0),
        int(_row_value(row, "story_item_count", 0) or 0),
        raw.get("score", raw.get("points", 0)),
        raw.get("comments", raw.get("comment_count", 0)),
        _news_date(row),
        str(row["item_id"]),
    )


def _sort_news_rows(rows: list[sqlite3.Row], *, sort: str) -> list[sqlite3.Row]:
    """Sort a read-only News projection before selecting Story representatives."""
    selected_sort = _safe_sort(sort, _NEWS_SORTS)
    if selected_sort == "oldest":
        return sorted(rows, key=lambda row: (_news_date(row, ascending=True), str(row["item_id"])))
    if selected_sort == "fresh":
        return sorted(
            rows,
            key=lambda row: (_news_date(row), _news_strength(row), str(row["item_id"])),
            reverse=True,
        )
    if selected_sort == "engagement":

        def engagement(row: sqlite3.Row) -> tuple[int, int, int, int, str, str]:
            raw = _json_int_dict(_row_value(row, "raw_engagement", ""))
            return (
                raw.get("score", raw.get("points", 0)),
                raw.get("comments", raw.get("comment_count", 0)),
                int(_row_value(row, "story_source_count", 0) or 0),
                int(_row_value(row, "story_item_count", 0) or 0),
                _news_date(row),
                str(row["item_id"]),
            )

        return sorted(rows, key=engagement, reverse=True)
    return sorted(rows, key=_news_strength, reverse=True)


def _collapse_news_stories(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Keep the first (already ranked) material for every linked Story.

    News is still based on the immutable raw release.  This only changes the
    reader-facing projection: unlinked material remains visible and the API
    can expose every raw item with ``view=items``.
    """
    collapsed: list[sqlite3.Row] = []
    seen_stories: set[str] = set()
    for row in rows:
        story_id = str(_row_value(row, "story_id", ""))
        if story_id and story_id in seen_stories:
            continue
        if story_id:
            seen_stories.add(story_id)
        collapsed.append(row)
    return collapsed


def _sort_story_rows(rows: list[sqlite3.Row], *, sort: str) -> list[sqlite3.Row]:
    selected_sort = _safe_sort(sort, _STORY_SORTS)
    if selected_sort == "oldest":
        return sorted(
            rows,
            # `or _UNDATED_LAST`, а не `or ""`: пустая дата по возрастанию сортируется
            # первой, и «Сначала раннее» открывалось сюжетами вообще без даты.
            key=lambda row: (str(row["first_seen"] or _UNDATED_LAST), str(row["story_id"])),
        )
    if selected_sort == "fresh":
        return sorted(
            rows,
            key=lambda row: (
                str(row["last_seen"] or ""),
                int(row["source_count"] or 0),
                int(row["item_count"] or 0),
                str(row["story_id"]),
            ),
            reverse=True,
        )
    if selected_sort == "volume":
        return sorted(
            rows,
            key=lambda row: (
                int(row["item_count"] or 0),
                int(row["source_count"] or 0),
                str(row["last_seen"] or ""),
                str(row["story_id"]),
            ),
            reverse=True,
        )
    return sorted(
        rows,
        key=lambda row: (
            int(row["source_count"] or 0),
            int(row["item_count"] or 0),
            str(row["last_seen"] or ""),
            str(row["story_id"]),
        ),
        reverse=True,
    )


def _sort_trend_rows(rows: list[sqlite3.Row], *, sort: str) -> list[sqlite3.Row]:
    selected_sort = _safe_sort(sort, _TREND_SORTS)
    if selected_sort == "oldest":
        return sorted(
            rows,
            key=lambda row: (str(row["first_seen"] or _UNDATED_LAST), str(row["trend_id"])),
        )
    if selected_sort == "fresh":
        return sorted(
            rows,
            key=lambda row: (
                str(row["last_seen"] or ""),
                float(row["confidence"] or 0.0),
                int(row["source_count"] or 0),
                int(row["story_count"] or 0),
                str(row["trend_id"]),
            ),
            reverse=True,
        )
    if selected_sort == "coverage":
        return sorted(
            rows,
            key=lambda row: (
                int(row["source_count"] or 0),
                int(row["story_count"] or 0),
                float(row["confidence"] or 0.0),
                str(row["last_seen"] or ""),
                str(row["trend_id"]),
            ),
            reverse=True,
        )
    return sorted(
        rows,
        key=lambda row: (
            float(row["confidence"] or 0.0),
            int(row["source_count"] or 0),
            int(row["story_count"] or 0),
            str(row["last_seen"] or ""),
            str(row["trend_id"]),
        ),
        reverse=True,
    )


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
    sort: str = "strength",
    view: str = "stories",
    page: int = 1,
    page_size: int = 50,
) -> PaginatedNews:
    publication, data_release, story_release, trend_release, preview = _resolve_publication(
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
    selected_sort = _safe_sort(sort, _NEWS_SORTS)
    selected_view = view if view in {"stories", "items"} else "stories"
    item_total = len(filtered)
    ordered = _sort_news_rows(filtered, sort=selected_sort)
    # Default to one representative piece of evidence per already-linked
    # Story. This removes the visible copy/paste effect without mutating the
    # DataRelease or hiding unlinked incoming evidence.
    if selected_view == "stories":
        ordered = _collapse_news_stories(ordered)
    start = (page - 1) * page_size
    page_rows = ordered[start : start + page_size]
    return PaginatedNews(
        items=[_news_item_out(row) for row in page_rows],
        total=len(ordered),
        item_total=item_total,
        page=page,
        page_size=page_size,
        publication_id=publication.publication_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
        sort=selected_sort,
        view=selected_view,
        preview=preview,
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


def _story_source_clusters(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    story_release_id: str,
    story_ids: Sequence[str],
) -> dict[str, dict[str, int]]:
    """Состав кластеров источников по сюжетам одним запросом.

    Считается по всем материалам сюжета, а не по выборке evidence (там лимит 5):
    полоса источников обязана отражать реальный охват, иначе она врёт ровно
    в том месте, ради которого сделана.
    """
    if not story_ids:
        return {}
    placeholders = ",".join("?" for _ in story_ids)
    rows = conn.execute(
        f"""
        SELECT si.story_id, i.source_cluster, COUNT(*) AS cnt
        FROM engine_story_items si
        JOIN release_items i ON i.item_id = si.item_id AND i.release_id = ?
        WHERE si.story_release_id = ? AND si.story_id IN ({placeholders})
        GROUP BY si.story_id, i.source_cluster
        """,
        (data_release_id, story_release_id, *story_ids),
    ).fetchall()
    clusters: dict[str, dict[str, int]] = {}
    for row in rows:
        cluster = str(row["source_cluster"] or "")
        if not cluster:
            continue
        clusters.setdefault(str(row["story_id"]), {})[cluster] = int(row["cnt"] or 0)
    return clusters


def _published_story_out(
    row: sqlite3.Row,
    *,
    evidence_items: list[NewsItemOut] | None = None,
    source_clusters: dict[str, int] | None = None,
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
        source_clusters=source_clusters or {},
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
    sort: str = "strength",
    page: int = 1,
    page_size: int = 50,
) -> PaginatedPublishedStories:
    publication, data_release, story_release, _trend_release, preview = _resolve_publication(
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
    selected_sort = _safe_sort(sort, _STORY_SORTS)
    # A project lens has an explicit relevance contract. Keep that primary
    # default, while every user-selected sort remains authoritative.
    if project_id and selected_sort == "strength":
        filtered.sort(
            key=lambda row: (
                _json_int_dict(row["project_scores"]).get(project_id, 0),
                int(row["source_count"] or 0),
                int(row["item_count"] or 0),
                str(row["last_seen"] or ""),
                str(row["story_id"]),
            ),
            reverse=True,
        )
    else:
        filtered = _sort_story_rows(filtered, sort=selected_sort)
    start = (page - 1) * page_size
    page_rows = filtered[start : start + page_size]
    clusters_by_story = _story_source_clusters(
        conn,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        story_ids=[str(row["story_id"]) for row in page_rows],
    )
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
        stories.append(
            _published_story_out(
                row,
                evidence_items=evidence,
                source_clusters=clusters_by_story.get(str(row["story_id"]), {}),
            )
        )
    return PaginatedPublishedStories(
        items=stories,
        total=len(filtered),
        page=page,
        page_size=page_size,
        publication_id=publication.publication_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        sort=selected_sort,
        preview=preview,
    )


def _trend_source_clusters(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    story_release_id: str,
    trend_release_id: str,
    trend_ids: Sequence[str],
) -> dict[str, dict[str, int]]:
    """Состав кластеров источников по трендам одним запросом.

    Тренд хранит только ``source_count`` и ``source_scope``: сколько кластеров
    и попал ли он в оба лагеря. Для полосы нужен сам состав, поэтому он
    собирается по материалам входящих сюжетов.
    """
    if not trend_ids:
        return {}
    placeholders = ",".join("?" for _ in trend_ids)
    rows = conn.execute(
        f"""
        SELECT ts.trend_id, i.source_cluster, COUNT(*) AS cnt
        FROM engine_trend_stories ts
        JOIN engine_story_items si
          ON si.story_id = ts.story_id AND si.story_release_id = ?
        JOIN release_items i ON i.item_id = si.item_id AND i.release_id = ?
        WHERE ts.trend_release_id = ? AND ts.trend_id IN ({placeholders})
        GROUP BY ts.trend_id, i.source_cluster
        """,
        (story_release_id, data_release_id, trend_release_id, *trend_ids),
    ).fetchall()
    clusters: dict[str, dict[str, int]] = {}
    for row in rows:
        cluster = str(row["source_cluster"] or "")
        if not cluster:
            continue
        clusters.setdefault(str(row["trend_id"]), {})[cluster] = int(row["cnt"] or 0)
    return clusters


def _trend_out(
    row: sqlite3.Row,
    *,
    stories: list[PublishedStoryOut] | None = None,
    source_clusters: dict[str, int] | None = None,
    children: list[TrendOut] | None = None,
) -> TrendOut:
    return TrendOut(
        trend_id=str(row["trend_id"]),
        title=str(row["name_ru"] or ""),
        review_name_ru=str(_row_value(row, "review_name_ru", "") or ""),
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
        # Только через `_row_value`: `open_engine_readonly` не мигрирует, поэтому колонки
        # может не быть на БД, которой писатель ещё не касался (например, в окно деплоя).
        # По той же причине эти поля нельзя использовать в SQL WHERE/ORDER BY.
        parent_trend_id=str(_row_value(row, "parent_trend_id", "") or ""),
        distinct_actors=_json_list(_row_value(row, "distinct_actors", "[]")),
        children=list(children or []),
        source_clusters=source_clusters or {},
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


def _trend_domains_for_radar(
    conn: sqlite3.Connection,
    *,
    trend_release_id: str,
    story_release_id: str,
    trend_id: str,
    fallback: list[str],
) -> list[str]:
    """Return a small, evidence-derived domain set for Radar filtering.

    Stored trend domain arrays are intentionally broad in older releases and
    can contain nearly the whole taxonomy.  Radar needs the domains actually
    represented by the member stories, otherwise every rubric tab renders the
    same candidates.  Keep the three most frequent story domains and fall
    back to the stored value when the release has no membership rows.
    """
    try:
        rows = conn.execute(
            """
            SELECT s.domain_ids
            FROM engine_trend_stories AS ts
            JOIN engine_stories AS s
              ON s.story_release_id = ?
             AND s.story_id = ts.story_id
            WHERE ts.trend_release_id = ?
              AND ts.trend_id = ?
            """,
            (story_release_id, trend_release_id, trend_id),
        ).fetchall()
    except sqlite3.Error:
        return fallback or ["other"]

    counts: dict[str, int] = {}
    for row in rows:
        for domain_id in _json_list(row["domain_ids"], fallback=["other"]):
            counts[domain_id] = counts.get(domain_id, 0) + 1
    if not counts:
        return fallback or ["other"]
    return [domain_id for domain_id, _ in sorted(counts.items(), key=lambda p: (-p[1], p[0]))[:3]]


def _engine_trends(
    conn: sqlite3.Connection,
    *,
    channel: str = "broad",
    publication_id: str | None = None,
    domain: str | None = None,
    lifecycle: str | None = None,
    review_status: str | None = None,
    project_id: str | None = None,
    q: str | None = None,
    include_stories: bool = True,
    sort: str = "strength",
    page: int = 1,
    page_size: int = 50,
) -> PaginatedTrends:
    publication, data_release, story_release, trend_release, preview = _resolve_publication(
        conn,
        channel=channel,
        publication_id=publication_id,
    )
    rows = conn.execute(
        """
        SELECT *
        FROM engine_trends
        WHERE trend_release_id = ?
        ORDER BY confidence DESC, source_count DESC, story_count DESC, last_seen DESC, trend_id
        """,
        (trend_release.trend_release_id,),
    ).fetchall()
    # Верхний уровень — рубрики (корни), под каждой её конкретные события. Фильтрация в
    # Python, а не в SQL: колонки может не быть на немигрированной БД (см. `_trend_out`).
    children_by_parent: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        parent_id = str(_row_value(row, "parent_trend_id", "") or "")
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(row)
    filtered: list[sqlite3.Row] = []
    for row in rows:
        if str(_row_value(row, "parent_trend_id", "") or ""):
            continue
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
        if q:
            # Тренды искались только по точному domain_id. Найти паттерн
            # по слову в названии было нечем, хотя их около двух тысяч.
            haystack = f"{row['name_ru'] or ''} {row['pattern'] or ''}".lower()
            if q.lower() not in haystack:
                continue
        filtered.append(row)
    selected_sort = _safe_sort(sort, _TREND_SORTS)
    # Project relevance is the default ordering only inside a Project Lens;
    # explicit user choices such as "fresh" must still take effect.
    if project_id and selected_sort == "strength":
        filtered.sort(
            key=lambda row: (
                _json_int_dict(row["project_scores"]).get(project_id, 0),
                float(row["confidence"] or 0.0),
                int(row["source_count"] or 0),
                int(row["story_count"] or 0),
                str(row["last_seen"] or ""),
                str(row["trend_id"]),
            ),
            reverse=True,
        )
    else:
        filtered = _sort_trend_rows(filtered, sort=selected_sort)
    for children in children_by_parent.values():
        children[:] = _sort_trend_rows(children, sort=selected_sort)
    start = (page - 1) * page_size
    page_rows = filtered[start : start + page_size]
    clusters_by_trend = _trend_source_clusters(
        conn,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
        trend_ids=[str(row["trend_id"]) for row in page_rows],
    )
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
        trends.append(
            _trend_out(
                row,
                stories=stories,
                source_clusters=clusters_by_trend.get(str(row["trend_id"]), {}),
                children=[
                    _trend_out(child) for child in children_by_parent.get(str(row["trend_id"]), [])
                ],
            )
        )
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
        sort=selected_sort,
        preview=preview,
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
        preview=trends.preview or stories.preview,
    )


def _engine_story_detail(
    conn: sqlite3.Connection,
    *,
    story_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
) -> PublishedStoryDetailOut:
    publication, data_release, story_release, trend_release, preview = _resolve_publication(
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
    # Сюжет лежит и в рубрике, и в её конкретном событии. Показывать обе строки — значит
    # дважды сказать одно и то же, поэтому оставляем самую точную: если среди трендов
    # сюжета есть ребёнок, его родитель из списка уходит.
    parents_covered = {
        str(_row_value(trend_row, "parent_trend_id", "") or "") for trend_row in trend_rows
    }
    leaf_rows = [
        trend_row
        for trend_row in trend_rows
        if str(trend_row["trend_id"]) not in parents_covered - {""}
    ]
    base = _published_story_out(row, evidence_items=evidence)
    return PublishedStoryDetailOut(
        **base.model_dump(),
        trends=[_trend_out(trend_row) for trend_row in leaf_rows],
        publication_id=publication.publication_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
        preview=preview,
    )


def _engine_trend_detail(
    conn: sqlite3.Connection,
    *,
    trend_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
) -> TrendDetailOut:
    publication, data_release, story_release, trend_release, preview = _resolve_publication(
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
        LIMIT 8
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
    # Дети рубрики — её конкретные события; на странице тренда это drill-down.
    child_rows = [
        child
        for child in conn.execute(
            """SELECT * FROM engine_trends
            WHERE trend_release_id = ?
            ORDER BY confidence DESC, source_count DESC, story_count DESC,
                     last_seen DESC, trend_id""",
            (trend_release.trend_release_id,),
        ).fetchall()
        if str(_row_value(child, "parent_trend_id", "") or "") == trend_id
    ]
    base = _trend_out(row, stories=stories, children=[_trend_out(child) for child in child_rows])
    return TrendDetailOut(
        **base.model_dump(),
        publication_id=publication.publication_id,
        data_release_id=data_release.release_id,
        story_release_id=story_release.story_release_id,
        trend_release_id=trend_release.trend_release_id,
        preview=preview,
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
    try:
        publication, data_release, story_release, trend_release, preview = _resolve_publication(
            conn,
            channel=channel,
            publication_id=publication_id,
        )
    except HTTPException as exc:
        if exc.status_code == 404 and not publication_id:
            return None
        raise

    selected_domain = domain
    if mode == "ai-native" and selected_domain is None:
        selected_domain = "ai_technology"

    trend_rows = conn.execute(
        """
        SELECT *
        FROM engine_trends
        WHERE trend_release_id = ?
        ORDER BY confidence DESC, source_count DESC, story_count DESC, last_seen DESC, trend_id
        """,
        (trend_release.trend_release_id,),
    ).fetchall()
    # На полки идут только рубрики верхнего уровня. Иначе родитель и его собственный
    # ребёнок занимают две карточки из пяти на Today и говорят одно и то же.
    # Отбрасываем до подсчёта: счётчики в шапке обязаны описывать то, что на полках, —
    # иначе они считают тренды, которых на странице нет.
    trend_rows = [
        row for row in trend_rows if not str(_row_value(row, "parent_trend_id", "") or "")
    ]
    candidate_count = len(trend_rows)
    confirmed_count = sum(
        1
        for row in trend_rows
        if str(_row_value(row, "review_status", "pending") or "pending")
        in {"confirmed", "accepted"}
    )

    # A trend release can have several Qwen reviews over its lifetime.  Use
    # the newest review only, but keep it separate from ``review_status``:
    # evaluated candidates are useful in preview even when they are not yet
    # accepted for production publication.
    trend_reviews: dict[str, dict[str, Any]] = {}
    try:
        review_rows = conn.execute(
            """
            SELECT target_id, decision, valid, created_at, review_id
            FROM llm_reviews
            WHERE target_kind = 'trend'
            ORDER BY created_at DESC, review_id DESC
            """
        ).fetchall()
        for review_row in review_rows:
            target_id = str(review_row["target_id"])
            if target_id not in trend_reviews:
                trend_reviews[target_id] = {
                    "decision": str(review_row["decision"] or ""),
                    "valid": bool(review_row["valid"]),
                }
    except sqlite3.Error:
        # Older engine databases do not have the review table yet.  The
        # candidate view remains usable without the optional Qwen metadata.
        pass

    shelves: dict[str, list[dict[str, Any]]] = {}
    for row in trend_rows:
        stored_domain_ids = _json_list(row["domain_ids"], fallback=["other"])
        domain_ids = _trend_domains_for_radar(
            conn,
            trend_release_id=trend_release.trend_release_id,
            story_release_id=story_release.story_release_id,
            trend_id=str(row["trend_id"]),
            fallback=stored_domain_ids,
        )
        if selected_domain and selected_domain not in domain_ids:
            continue
        lifecycle = str(row["lifecycle"] or "insufficient_history")
        review_status = str(_row_value(row, "review_status", "pending") or "pending")
        review = trend_reviews.get(str(row["trend_id"]), {})
        llm_decision = str(review.get("decision") or "")
        if review_status in {"confirmed", "accepted"}:
            candidate_status = "confirmed"
        elif review.get("valid") and llm_decision == "coherent_trend":
            candidate_status = "qwen_coherent"
        elif review.get("valid") and llm_decision == "reject":
            candidate_status = "qwen_rejected"
        else:
            candidate_status = "pending"
        shelves.setdefault(lifecycle, []).append(
            {
                "trend_id": row["trend_id"],
                "title": row["name_ru"],
                "review_name_ru": str(_row_value(row, "review_name_ru", "") or ""),
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
                "review_status": review_status,
                "candidate_status": candidate_status,
                "llm_decision": llm_decision,
                "llm_valid": bool(review.get("valid")),
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
        selected_domain=selected_domain,
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
        preview=preview,
        candidate_count=candidate_count,
        confirmed_count=confirmed_count,
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
    sort: str = "strength",
    view: str = "stories",
    channel: str = "broad",
    publication_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> PaginatedNews:
    """Published News projection from the immutable release.

    ``view=stories`` (по умолчанию) отдаёт **по одному материалу на сюжет**: `total` —
    число строк после схлопывания, а `item_total` — число сырых материалов. Материалы
    без сюжета остаются как есть. ``view=items`` отдаёт каждый сырой материал, и тогда
    `total` равен `item_total`. Ответ всегда содержит фактические `view` и `sort`.
    """
    return _engine_news(
        _require_engine(engine_conn),
        channel=channel,
        publication_id=publication_id,
        date=date,
        domain=domain,
        provider=provider,
        source_cluster=source_cluster,
        q=q,
        sort=_require_supported("sort", sort, _NEWS_SORTS),
        view=_require_supported("view", view, _NEWS_VIEWS),
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
    sort: str = "strength",
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
        sort=_require_supported("sort", sort, _STORY_SORTS),
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
    sort: str = "strength",
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
        sort=_require_supported("sort", sort, _TREND_SORTS),
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


# ---------------------------------------------------------------------------
# Reddit Pulse endpoints
# ---------------------------------------------------------------------------


class PulseSignalOut(BaseModel):
    signal_id: str
    item_id: str
    subreddit: str
    signal_type: str
    title: str
    published_at: str = ""
    discussion_url: str = ""
    target_url: str = ""
    pulse_score: float = 0
    subreddit_percentile: float = 0
    comment_velocity: float = 0
    discussion_depth: float = 0
    cross_subreddit_repetition: float = 0
    novelty: float = 0
    domain_ids: list[str] = Field(default_factory=list)
    theme_ids: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    # Reddit-native raw engagement
    reddit_score: float = 0
    reddit_comments: float = 0
    upvote_ratio: float = 0
    is_self: bool = False
    link_flair_text: str = ""
    mainstream_coverage_count: int = 0
    perspective_gap: float = 0


class PulseListOut(BaseModel):
    signal_release_id: str
    total: int
    items: list[PulseSignalOut]


class PulseSummaryOut(BaseModel):
    signal_release_id: str
    total_signals: int
    by_type: dict[str, int]
    top_pulse: list[PulseSignalOut]
    top_pain: list[PulseSignalOut]
    top_ai: list[PulseSignalOut]
    mainstream_gap: list[PulseSignalOut]
    perspective_gap_available: bool = False


def _engine_pulse_signals(
    conn: sqlite3.Connection,
    signal_release_id: str,
    *,
    signal_type: str | None = None,
    subreddit: str | None = None,
    q: str | None = None,
    sort: str = "pulse",
    limit: int = 50,
    offset: int = 0,
    data_release_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    where = "cs.signal_release_id = ?"
    params: list[Any] = [signal_release_id]
    if signal_type:
        where += " AND cs.signal_type = ?"
        params.append(signal_type)
    if subreddit:
        where += " AND LOWER(cs.subreddit) = ?"
        params.append(subreddit.lower())
    if q:
        # Поиска по сигналам не было вовсе, хотя тысячи постов листаются
        # только постранично: найти конкретное обсуждение было нечем.
        where += " AND LOWER(cs.title) LIKE ?"
        params.append(f"%{q.lower()}%")

    # Resolve data_release_id for JOIN with release_items.
    if not data_release_id:
        try:
            dr_row = conn.execute(
                "SELECT data_release_id FROM signal_releases WHERE signal_release_id = ?",
                (signal_release_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return [], 0
        data_release_id = str(dr_row[0]) if dr_row else ""

    join_clause = " LEFT JOIN release_items ri ON ri.item_id = cs.item_id AND ri.release_id = ?"
    # JOIN placeholder appears before WHERE placeholders in the SQL text.
    params = [data_release_id or "", *params]

    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM community_signals cs WHERE {where}",
            [signal_release_id]
            + ([signal_type] if signal_type else [])
            + ([subreddit.lower()] if subreddit else [])
            + ([f"%{q.lower()}%"] if q else []),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return [], 0

    sort_map = {
        "pulse": "cs.pulse_score DESC",
        "score": "COALESCE(json_extract(ri.raw_engagement, '$.score'), 0) DESC",
        "comments": "COALESCE(json_extract(ri.raw_engagement, '$.comments'), 0) DESC",
        "velocity": "cs.comment_velocity DESC",
        "ratio": "COALESCE(json_extract(ri.raw_engagement, '$.upvote_ratio'), 0) DESC",
    }
    # При равной силе сигнала не оставляем порядок БД случайным: свежий пост
    # идёт выше.  Явный пользовательский sort сохраняет приоритет, но тоже
    # получает этот одинаковый tie-breaker.
    order_by = (
        f"{sort_map.get(sort, 'cs.pulse_score DESC')}, "
        "COALESCE(ri.published_at, ri.observed_at, ri.snapshot_date) DESC, cs.signal_id DESC"
    )

    select_cols = (
        "cs.signal_id, cs.item_id, cs.subreddit, cs.signal_type, cs.title, "
        "cs.discussion_url, cs.target_url, cs.pulse_score, "
        "cs.subreddit_percentile, cs.comment_velocity, "
        "cs.discussion_depth, cs.cross_subreddit_repetition, cs.novelty, "
        "cs.domain_ids_json, cs.theme_ids_json, cs.pain_points_json, "
        "cs.mainstream_coverage_count, cs.perspective_gap, "
        "COALESCE(json_extract(ri.raw_engagement, '$.score'), 0) as reddit_score, "
        "COALESCE(json_extract(ri.raw_engagement, '$.comments'), 0) as reddit_comments, "
        "COALESCE(json_extract(ri.raw_engagement, '$.upvote_ratio'), 0) as upvote_ratio, "
        "COALESCE(json_extract(ri.metadata, '$.is_self'), 0) as is_self, "
        "COALESCE(json_extract(ri.metadata, '$.link_flair_text'), '') as link_flair_text, "
        "COALESCE(ri.published_at, ri.observed_at, ri.snapshot_date, '') as published_at"
    )

    try:
        rows = conn.execute(
            f"SELECT {select_cols} FROM community_signals cs{join_clause} "
            f"WHERE {where} ORDER BY {order_by} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    except sqlite3.OperationalError:
        return [], 0

    items = []
    for r in rows:
        items.append(
            {
                "signal_id": r["signal_id"],
                "item_id": r["item_id"],
                "subreddit": r["subreddit"],
                "signal_type": r["signal_type"],
                "title": r["title"],
                "published_at": r["published_at"],
                "discussion_url": _safe_url(r["discussion_url"]),
                "target_url": _safe_url(r["target_url"]),
                "pulse_score": r["pulse_score"],
                "subreddit_percentile": r["subreddit_percentile"],
                "comment_velocity": r["comment_velocity"],
                "discussion_depth": r["discussion_depth"],
                "cross_subreddit_repetition": r["cross_subreddit_repetition"],
                "novelty": r["novelty"],
                "domain_ids": json.loads(r["domain_ids_json"] or "[]"),
                "theme_ids": json.loads(r["theme_ids_json"] or "[]"),
                "pain_points": json.loads(r["pain_points_json"] or "[]"),
                "mainstream_coverage_count": r["mainstream_coverage_count"],
                "perspective_gap": r["perspective_gap"],
                "reddit_score": r["reddit_score"],
                "reddit_comments": r["reddit_comments"],
                "upvote_ratio": r["upvote_ratio"],
                "is_self": bool(r["is_self"]),
                "link_flair_text": r["link_flair_text"],
            }
        )
    return items, total


def _latest_signal_release(
    conn: sqlite3.Connection,
    *,
    data_release_id: str | None = None,
    date: str | None = None,
) -> str | None:
    where = ["status = 'finalized'"]
    params: list[Any] = []
    if data_release_id:
        where.append("data_release_id = ?")
        params.append(data_release_id)
    if date:
        where.append("date = ?")
        params.append(date)
    try:
        row = conn.execute(
            "SELECT signal_release_id FROM signal_releases "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC LIMIT 1",
            params,
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row["signal_release_id"]) if row else None


@router.get("/reddit-pulse", response_model=PulseListOut)
def list_reddit_pulse(
    signal_release: str | None = None,
    data_release: str | None = None,
    date: str | None = None,
    signal_type: str | None = None,
    subreddit: str | None = None,
    sort: str = Query(default="pulse", pattern="^(pulse|score|comments|velocity|ratio)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> PulseListOut:
    """List Reddit Pulse signals with optional filters and sort."""
    conn = _require_engine(engine_conn)
    sig_id = signal_release or _latest_signal_release(
        conn,
        data_release_id=data_release,
        date=date,
    )
    if not sig_id:
        return PulseListOut(signal_release_id="", total=0, items=[])
    offset = (page - 1) * page_size
    items, total = _engine_pulse_signals(
        conn,
        sig_id,
        signal_type=signal_type,
        subreddit=subreddit,
        sort=sort,
        limit=page_size,
        offset=offset,
    )
    return PulseListOut(
        signal_release_id=sig_id,
        total=total,
        items=[PulseSignalOut(**i) for i in items],
    )


@router.get("/reddit-pulse/summary", response_model=PulseSummaryOut)
def reddit_pulse_summary(
    signal_release: str | None = None,
    data_release: str | None = None,
    date: str | None = None,
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> PulseSummaryOut:
    """Reddit Pulse summary: top signals by category."""
    conn = _require_engine(engine_conn)
    sig_id = signal_release or _latest_signal_release(
        conn,
        data_release_id=data_release,
        date=date,
    )
    if not sig_id:
        return PulseSummaryOut(
            signal_release_id="",
            total_signals=0,
            by_type={},
            top_pulse=[],
            top_pain=[],
            top_ai=[],
            mainstream_gap=[],
        )
    total = conn.execute(
        "SELECT COUNT(*) FROM community_signals WHERE signal_release_id = ?",
        (sig_id,),
    ).fetchone()[0]
    type_rows = conn.execute(
        "SELECT signal_type, COUNT(*) as cnt "
        "FROM community_signals WHERE signal_release_id = ? "
        "GROUP BY signal_type ORDER BY cnt DESC",
        (sig_id,),
    ).fetchall()
    by_type = {r["signal_type"]: r["cnt"] for r in type_rows}

    metrics_row = conn.execute(
        "SELECT metrics_json FROM signal_releases WHERE signal_release_id = ?",
        (sig_id,),
    ).fetchone()
    metrics = json.loads(metrics_row["metrics_json"] or "{}") if metrics_row else {}
    gap_available = bool(metrics.get("perspective_gap_available", False))

    def _top(signal_type: str | None = None, lim: int = 10) -> list[PulseSignalOut]:
        items, _ = _engine_pulse_signals(
            conn,
            sig_id,
            signal_type=signal_type,
            limit=lim,
            data_release_id=data_release,
        )
        return [PulseSignalOut(**item) for item in items]

    def _top_many(signal_types: list[str], lim: int = 10) -> list[PulseSignalOut]:
        merged: list[PulseSignalOut] = []
        for item_type in signal_types:
            merged.extend(_top(item_type, lim=lim))
        return sorted(merged, key=lambda item: item.pulse_score, reverse=True)[:lim]

    return PulseSummaryOut(
        signal_release_id=sig_id,
        total_signals=total,
        by_type=by_type,
        top_pulse=_top(),
        top_pain=_top_many(["pain_point", "complaint"]),
        top_ai=_top_many(["ai_capability", "ai_risk", "ai_tools"]),
        mainstream_gap=sorted(
            (
                PulseSignalOut(**item)
                for item in _engine_pulse_signals(
                    conn,
                    sig_id,
                    limit=50,
                    data_release_id=data_release,
                )[0]
                if item.get("pulse_score", 0) >= 60 and item.get("mainstream_coverage_count", 0) < 2
            ),
            key=lambda item: item.perspective_gap,
            reverse=True,
        )[:10],
        perspective_gap_available=gap_available,
    )


@router.get("/reddit-pulse/{signal_id}", response_model=PulseSignalOut)
def get_reddit_pulse_signal(
    signal_id: str,
    signal_release: str | None = None,
    data_release: str | None = None,
    date: str | None = None,
    engine_conn: sqlite3.Connection | None = Depends(_get_engine_db),
) -> PulseSignalOut:
    """Get a single Reddit Pulse signal by ID."""
    conn = _require_engine(engine_conn)
    sig_id = signal_release or _latest_signal_release(
        conn,
        data_release_id=data_release,
        date=date,
    )
    if not sig_id:
        raise HTTPException(404, "No signal release found")
    row = conn.execute(
        "SELECT signal_id, item_id, subreddit, signal_type, title, "
        "discussion_url, target_url, pulse_score, "
        "subreddit_percentile, comment_velocity, "
        "discussion_depth, cross_subreddit_repetition, novelty, "
        "domain_ids_json, theme_ids_json, pain_points_json "
        "FROM community_signals "
        "WHERE signal_release_id = ? AND signal_id = ?",
        (sig_id, signal_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Signal {signal_id} not found")
    return PulseSignalOut(
        signal_id=row["signal_id"],
        item_id=row["item_id"],
        subreddit=row["subreddit"],
        signal_type=row["signal_type"],
        title=row["title"],
        discussion_url=_safe_url(row["discussion_url"]),
        target_url=_safe_url(row["target_url"]),
        pulse_score=row["pulse_score"],
        subreddit_percentile=row["subreddit_percentile"],
        comment_velocity=row["comment_velocity"],
        discussion_depth=row["discussion_depth"],
        cross_subreddit_repetition=row["cross_subreddit_repetition"],
        novelty=row["novelty"],
        domain_ids=json.loads(row["domain_ids_json"] or "[]"),
        theme_ids=json.loads(row["theme_ids_json"] or "[]"),
        pain_points=json.loads(row["pain_points_json"] or "[]"),
    )
