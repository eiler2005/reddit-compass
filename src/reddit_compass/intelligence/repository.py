"""Repository layer для intelligence SQLite projection.

Отдельный от legacy db.py: не раздувает существующий модуль.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from typing import Any

from .models import (
    Briefing,
    BriefingStory,
    ContentItem,
    EvidenceRef,
    GroundedText,
    ItemSignal,
    Observation,
    ResearchState,
    SourceHealth,
    Story,
    StoryMetric,
)

logger = logging.getLogger("reddit_compass")


def upsert_run(
    conn: sqlite3.Connection,
    run_id: str,
    snapshot_date: str,
    profile: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO runs (run_id, snapshot_date, profile, status, started_at, finished_at,
                             schema_version)
           VALUES (?, ?, ?, ?, ?, ?, 2)
           ON CONFLICT(run_id) DO UPDATE SET
               status = excluded.status,
               finished_at = excluded.finished_at""",
        (run_id, snapshot_date, profile, status, started_at, finished_at),
    )


def upsert_items(conn: sqlite3.Connection, items: list[ContentItem]) -> None:
    for item in items:
        conn.execute(
            """INSERT INTO items (item_id, provider, source_cluster, external_id, canonical_url,
                                  title, summary_ru, excerpt, author, published_at, observed_at,
                                  snapshot_date, language, content_scope, source_section,
                                  domain_ids, discussion_url, target_url, dedupe_group_id,
                                  evidence_refs, raw_engagement, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(item_id) DO UPDATE SET
                   title = excluded.title,
                   summary_ru = excluded.summary_ru,
                   excerpt = excluded.excerpt,
                   source_section = excluded.source_section,
                   domain_ids = excluded.domain_ids,
                   discussion_url = excluded.discussion_url,
                   target_url = excluded.target_url,
                   dedupe_group_id = excluded.dedupe_group_id,
                   evidence_refs = excluded.evidence_refs,
                   raw_engagement = excluded.raw_engagement,
                   metadata = excluded.metadata""",
            (
                item.item_id,
                item.provider,
                item.source_cluster,
                item.external_id,
                item.canonical_url,
                item.title,
                item.summary_ru,
                item.excerpt,
                item.author,
                item.published_at,
                item.observed_at,
                item.snapshot_date,
                item.language,
                item.content_scope,
                item.source_section,
                json.dumps(item.domain_ids, ensure_ascii=False),
                item.discussion_url,
                item.target_url,
                item.dedupe_group_id,
                json.dumps(item.evidence_refs, ensure_ascii=False),
                json.dumps(item.raw_engagement, ensure_ascii=False),
                json.dumps(item.metadata, ensure_ascii=False),
            ),
        )


def upsert_observations(conn: sqlite3.Connection, observations: list[Observation]) -> None:
    for obs in observations:
        conn.execute(
            """INSERT INTO observations (run_id, item_id, observed_at, source_rank,
                                         engagement_percentile, score_delta, comments_delta)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, item_id) DO UPDATE SET
                   observed_at = excluded.observed_at,
                   source_rank = excluded.source_rank,
                   engagement_percentile = excluded.engagement_percentile,
                   score_delta = excluded.score_delta,
                   comments_delta = excluded.comments_delta""",
            (
                obs.run_id,
                obs.item_id,
                obs.observed_at,
                obs.source_rank,
                obs.engagement_percentile,
                obs.score_delta,
                obs.comments_delta,
            ),
        )


def upsert_story(conn: sqlite3.Connection, story: Story) -> None:
    conn.execute(
        """INSERT INTO stories (
                                story_id, canonical_key, title, summary_ru, domain_ids, theme_ids,
                                trend_id, lifecycle, project_scores,
                                first_seen, last_seen, item_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(story_id) DO UPDATE SET
               title = excluded.title,
               summary_ru = excluded.summary_ru,
               domain_ids = excluded.domain_ids,
               theme_ids = excluded.theme_ids,
               trend_id = excluded.trend_id,
               lifecycle = excluded.lifecycle,
               project_scores = excluded.project_scores,
               last_seen = excluded.last_seen,
               item_ids = excluded.item_ids""",
        (
            story.story_id,
            story.canonical_key,
            story.title,
            story.summary_ru,
            json.dumps(story.domain_ids, ensure_ascii=False),
            json.dumps(story.theme_ids, ensure_ascii=False),
            story.trend_id,
            story.lifecycle,
            json.dumps(story.project_scores, ensure_ascii=False),
            story.first_seen,
            story.last_seen,
            json.dumps(story.item_ids, ensure_ascii=False),
        ),
    )


def replace_run_stories(
    conn: sqlite3.Connection,
    run_id: str,
    stories: list[Story],
    metrics: list[StoryMetric],
) -> None:
    """Заменяет stories и metrics для run (идемпотентно)."""
    conn.execute("DELETE FROM story_items WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM story_metrics WHERE run_id = ?", (run_id,))

    for story in stories:
        upsert_story(conn, story)
        for item_id in story.item_ids:
            conn.execute(
                "INSERT OR IGNORE INTO story_items (run_id, story_id, item_id) VALUES (?, ?, ?)",
                (run_id, story.story_id, item_id),
            )

    for metric in metrics:
        conn.execute(
            """INSERT INTO story_metrics (run_id, story_id, goal_relevance, cross_source_coverage,
                                          momentum, novelty, evidence_quality, trend_score,
                                          confidence, direction, trend_id, lifecycle,
                                          project_scores, item_count, source_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, story_id) DO UPDATE SET
                   goal_relevance = excluded.goal_relevance,
                   cross_source_coverage = excluded.cross_source_coverage,
                   momentum = excluded.momentum,
                   novelty = excluded.novelty,
                   evidence_quality = excluded.evidence_quality,
                   trend_score = excluded.trend_score,
                   confidence = excluded.confidence,
                   direction = excluded.direction,
                   trend_id = excluded.trend_id,
                   lifecycle = excluded.lifecycle,
                   project_scores = excluded.project_scores,
                   item_count = excluded.item_count,
                   source_count = excluded.source_count""",
            (
                metric.run_id,
                metric.story_id,
                metric.goal_relevance,
                metric.cross_source_coverage,
                metric.momentum,
                metric.novelty,
                metric.evidence_quality,
                metric.trend_score,
                metric.confidence,
                metric.direction,
                metric.trend_id,
                metric.lifecycle,
                json.dumps(metric.project_scores, ensure_ascii=False),
                metric.item_count,
                metric.source_count,
            ),
        )


def replace_run_signals(conn: sqlite3.Connection, run_id: str, signals: list[ItemSignal]) -> None:
    conn.execute("DELETE FROM item_signals WHERE run_id = ?", (run_id,))
    seen: set[str] = set()
    for sig in signals:
        if sig.item_id in seen:
            continue
        seen.add(sig.item_id)
        conn.execute(
            """INSERT OR REPLACE INTO item_signals
               (run_id, item_id, domain_ids, theme_ids, candidate_themes,
                pain_points, buying_intent, goal_relevance,
                summary_ru, evidence_scope, model, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                sig.item_id,
                json.dumps(sig.domain_ids, ensure_ascii=False),
                json.dumps(sig.theme_ids, ensure_ascii=False),
                json.dumps(sig.candidate_themes, ensure_ascii=False),
                json.dumps(sig.pain_points, ensure_ascii=False),
                int(sig.buying_intent),
                json.dumps(sig.goal_relevance, ensure_ascii=False),
                sig.summary_ru,
                sig.evidence_scope,
                sig.model,
                sig.analyzed_at,
            ),
        )


def save_briefing(conn: sqlite3.Connection, briefing: Briefing) -> None:
    conn.execute(
        """INSERT INTO briefings (run_id, schema_version, briefing_json)
           VALUES (?, ?, ?)
           ON CONFLICT(run_id, schema_version) DO UPDATE SET
               briefing_json = excluded.briefing_json,
               created_at = datetime('now')""",
        (
            briefing.run_id,
            briefing.schema_version,
            json.dumps(_briefing_to_dict(briefing), ensure_ascii=False),
        ),
    )


def get_briefing(conn: sqlite3.Connection, date: str, profile: str) -> Briefing | None:
    row = conn.execute(
        """SELECT b.briefing_json FROM briefings b
           JOIN runs r ON b.run_id = r.run_id
           WHERE r.snapshot_date = ? AND r.profile = ?
           ORDER BY b.schema_version DESC LIMIT 1""",
        (date, profile),
    ).fetchone()
    if not row:
        return None
    return _dict_to_briefing(json.loads(row[0]))


def query_stories(
    conn: sqlite3.Connection,
    run_id: str | None = None,
    date: str | None = None,
    profile: str | None = None,
    theme: str | None = None,
    candidate_theme: str | None = None,
    domain: str | None = None,
    provider: str | None = None,
    source_cluster: str | None = None,
    direction: str | None = None,
    confidence: str | None = None,
    pain: str | None = None,
    q: str | None = None,
    sort: str = "trend_score",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Запрос stories с фильтрами. Возвращает (items, total)."""
    where: list[str] = []
    params: list[Any] = []

    if run_id:
        where.append("sm.run_id = ?")
        params.append(run_id)
    elif date and profile:
        where.append(
            "sm.run_id IN (SELECT run_id FROM runs WHERE snapshot_date = ? AND profile = ?)"
        )
        params.extend([date, profile])
    elif date:
        where.append("sm.run_id IN (SELECT run_id FROM runs WHERE snapshot_date = ?)")
        params.append(date)

    def item_signal_exists(column: str) -> str:
        return (
            "EXISTS (SELECT 1 FROM story_items si "
            "JOIN item_signals isig ON isig.run_id = si.run_id AND isig.item_id = si.item_id "
            f"WHERE si.run_id = sm.run_id AND si.story_id = sm.story_id AND isig.{column} LIKE ?)"
        )

    if theme:
        where.append(f"(s.theme_ids LIKE ? OR {item_signal_exists('theme_ids')})")
        params.extend([f'%"{theme}"%', f'%"{theme}"%'])
    if candidate_theme:
        where.append(item_signal_exists("candidate_themes"))
        params.append(f'%"{candidate_theme}"%')
    if domain:
        where.append("s.domain_ids LIKE ?")
        params.append(f'%"{domain}"%')
    if provider:
        where.append(
            "EXISTS (SELECT 1 FROM story_items si JOIN items i ON i.item_id = si.item_id "
            "WHERE si.run_id = sm.run_id AND si.story_id = sm.story_id AND i.provider = ?)"
        )
        params.append(provider)
    if source_cluster:
        where.append(
            "EXISTS (SELECT 1 FROM story_items si JOIN items i ON i.item_id = si.item_id "
            "WHERE si.run_id = sm.run_id AND si.story_id = sm.story_id AND i.source_cluster = ?)"
        )
        params.append(source_cluster)
    if direction:
        where.append("sm.direction = ?")
        params.append(direction)
    if confidence:
        where.append("sm.confidence = ?")
        params.append(confidence)
    if pain:
        where.append(item_signal_exists("pain_points"))
        params.append(f'%"{pain}"%')
    if q:
        where.append("(s.title LIKE ? OR s.summary_ru LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    sort_col = {
        "trend_score": "sm.trend_score DESC",
        "first_seen": "s.first_seen DESC",
        "last_seen": "s.last_seen DESC",
        "item_count": "sm.item_count DESC",
    }.get(sort, "sm.trend_score DESC")

    count_sql = f"""SELECT COUNT(*) FROM story_metrics sm
                    JOIN stories s ON sm.story_id = s.story_id {where_clause}"""
    total: int = conn.execute(count_sql, params).fetchone()[0]

    offset = (page - 1) * page_size
    data_sql = f"""SELECT s.*, sm.run_id, sm.goal_relevance, sm.cross_source_coverage,
                          sm.momentum, sm.novelty, sm.evidence_quality, sm.trend_score,
                          sm.confidence, sm.direction, sm.trend_id AS metric_trend_id,
                          sm.lifecycle AS metric_lifecycle,
                          sm.project_scores AS metric_project_scores,
                          sm.item_count, sm.source_count
                   FROM story_metrics sm
                   JOIN stories s ON sm.story_id = s.story_id
                   {where_clause}
                   ORDER BY {sort_col}
                   LIMIT ? OFFSET ?"""
    rows = conn.execute(data_sql, [*params, page_size, offset]).fetchall()
    return [_decode_story_row(dict(r)) for r in rows], total


def _decode_story_row(row: dict[str, Any]) -> dict[str, Any]:
    for field in ("theme_ids", "domain_ids"):
        raw = row.get(field)
        if isinstance(raw, str):
            row[field] = json.loads(raw) if raw else []
    for field in ("project_scores", "metric_project_scores"):
        raw = row.get(field)
        if isinstance(raw, str):
            row[field] = json.loads(raw) if raw else {}
    return row


def get_story(conn: sqlite3.Connection, story_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM stories WHERE story_id = ?", (story_id,)).fetchone()
    if not row:
        return None
    story = dict(row)
    story["theme_ids"] = json.loads(story["theme_ids"])
    story["domain_ids"] = json.loads(story.get("domain_ids") or '["other"]')
    story["item_ids"] = json.loads(story["item_ids"])
    story["project_scores"] = json.loads(story.get("project_scores") or "{}")

    metrics = conn.execute(
        "SELECT * FROM story_metrics WHERE story_id = ? ORDER BY run_id", (story_id,)
    ).fetchall()
    story["metrics"] = [dict(m) for m in metrics]

    return story


def update_research_state(
    conn: sqlite3.Connection,
    story_id: str,
    saved: bool | None = None,
    status: str | None = None,
    note: str | None = None,
    updated_at: str = "",
) -> ResearchState:
    existing = conn.execute(
        "SELECT * FROM research_state WHERE story_id = ?", (story_id,)
    ).fetchone()

    if existing:
        new_saved = int(saved) if saved is not None else existing["saved"]
        new_status = status if status is not None else existing["status"]
        new_note = note if note is not None else existing["note"]
        conn.execute(
            """UPDATE research_state SET saved = ?, status = ?, note = ?, updated_at = ?
               WHERE story_id = ?""",
            (new_saved, new_status, new_note, updated_at, story_id),
        )
    else:
        new_saved = int(saved or False)
        new_status = status or "unread"
        new_note = note or ""
        conn.execute(
            """INSERT INTO research_state (story_id, saved, status, note, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (story_id, new_saved, new_status, new_note, updated_at),
        )

    return ResearchState(
        story_id=story_id,
        saved=bool(new_saved),
        status=new_status,  # type: ignore[arg-type]
        note=new_note,
        updated_at=updated_at,
    )


def get_research_state(conn: sqlite3.Connection, story_id: str) -> ResearchState | None:
    row = conn.execute("SELECT * FROM research_state WHERE story_id = ?", (story_id,)).fetchone()
    if not row:
        return None
    return ResearchState(
        story_id=row["story_id"],
        saved=bool(row["saved"]),
        status=row["status"],
        note=row["note"],
        updated_at=row["updated_at"],
    )


def save_source_health(conn: sqlite3.Connection, run_id: str, health: list[SourceHealth]) -> None:
    conn.execute("DELETE FROM source_health WHERE run_id = ?", (run_id,))
    for sh in health:
        conn.execute(
            """INSERT INTO source_health (run_id, source_id, provider, cluster, status,
                                          count, duration_sec, error_code, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                sh.source_id,
                sh.provider,
                sh.cluster,
                sh.status,
                sh.count,
                sh.duration_sec,
                sh.error_code,
                sh.message,
            ),
        )


def _briefing_to_dict(b: Briefing) -> dict[str, Any]:
    """Сериализует Briefing в dict для JSON."""

    def _story_dict(bs: BriefingStory) -> dict[str, Any]:
        return {
            "story": asdict(bs.story),
            "metric": asdict(bs.metric),
            "why_it_matters": bs.why_it_matters,
            "evidence": [asdict(e) for e in bs.evidence],
            "score_breakdown": bs.score_breakdown,
        }

    return {
        "schema_version": b.schema_version,
        "run_id": b.run_id,
        "date": b.date,
        "profile": b.profile,
        "status": b.status,
        "generated_at": b.generated_at,
        "source_health": [asdict(sh) for sh in b.source_health],
        "top_changes": [_story_dict(bs) for bs in b.top_changes],
        "mega_stories": [_story_dict(bs) for bs in b.mega_stories],
        "watchlist": [_story_dict(bs) for bs in b.watchlist],
        "pain_points": [asdict(gp) for gp in b.pain_points],
        "column_ideas": [asdict(gp) for gp in b.column_ideas],
        "narrative_shifts": [asdict(gp) for gp in b.narrative_shifts],
    }


def _dict_to_briefing(d: dict[str, Any]) -> Briefing:
    """Десериализует Briefing из dict."""

    def _story_from_dict(sd: dict[str, Any]) -> BriefingStory:
        story_data = sd["story"]
        metric_data = sd["metric"]
        return BriefingStory(
            story=Story(
                story_id=story_data["story_id"],
                canonical_key=story_data["canonical_key"],
                title=story_data["title"],
                summary_ru=story_data.get("summary_ru", ""),
                domain_ids=story_data.get("domain_ids", ["other"]),
                theme_ids=story_data.get("theme_ids", []),
                trend_id=story_data.get("trend_id", ""),
                lifecycle=story_data.get("lifecycle", "new"),
                project_scores=story_data.get("project_scores", {}),
                first_seen=story_data.get("first_seen", ""),
                last_seen=story_data.get("last_seen", ""),
                item_ids=story_data.get("item_ids", []),
            ),
            metric=StoryMetric(
                run_id=metric_data["run_id"],
                story_id=metric_data["story_id"],
                goal_relevance=metric_data.get("goal_relevance", 0.0),
                cross_source_coverage=metric_data.get("cross_source_coverage", 0.0),
                momentum=metric_data.get("momentum", 0.0),
                novelty=metric_data.get("novelty", 0.0),
                evidence_quality=metric_data.get("evidence_quality", 0.0),
                trend_score=metric_data.get("trend_score", 0.0),
                confidence=metric_data.get("confidence", "low"),
                direction=metric_data.get("direction", "new"),
                trend_id=metric_data.get("trend_id", ""),
                lifecycle=metric_data.get("lifecycle", metric_data.get("direction", "new")),
                project_scores=metric_data.get("project_scores", {}),
                item_count=metric_data.get("item_count", 0),
                source_count=metric_data.get("source_count", 0),
            ),
            why_it_matters=sd.get("why_it_matters", ""),
            evidence=[
                EvidenceRef(
                    item_id=e["item_id"],
                    provider=e["provider"],
                    source_cluster=e["source_cluster"],
                    url=e["url"],
                    title=e["title"],
                    excerpt=e.get("excerpt", ""),
                    content_scope=e.get("content_scope", "headline"),
                )
                for e in sd.get("evidence", [])
            ],
            score_breakdown=sd.get("score_breakdown", {}),
        )

    return Briefing(
        schema_version=d["schema_version"],
        run_id=d["run_id"],
        date=d["date"],
        profile=d["profile"],
        status=d["status"],
        generated_at=d["generated_at"],
        source_health=[
            SourceHealth(
                source_id=sh["source_id"],
                provider=sh["provider"],
                cluster=sh["cluster"],
                status=sh["status"],
                count=sh.get("count", 0),
                duration_sec=sh.get("duration_sec", 0.0),
                error_code=sh.get("error_code"),
                message=sh.get("message", ""),
            )
            for sh in d.get("source_health", [])
        ],
        top_changes=[_story_from_dict(sd) for sd in d.get("top_changes", [])],
        mega_stories=[_story_from_dict(sd) for sd in d.get("mega_stories", [])],
        watchlist=[_story_from_dict(sd) for sd in d.get("watchlist", [])],
        pain_points=[
            GroundedText(text=gp["text"], evidence_ids=gp.get("evidence_ids", []))
            for gp in d.get("pain_points", [])
        ],
        column_ideas=[
            GroundedText(text=gp["text"], evidence_ids=gp.get("evidence_ids", []))
            for gp in d.get("column_ideas", [])
        ],
        narrative_shifts=[
            GroundedText(text=gp["text"], evidence_ids=gp.get("evidence_ids", []))
            for gp in d.get("narrative_shifts", [])
        ],
    )
