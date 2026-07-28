"""Unified run: оркестрация сбора из всех источников.

Команда `reddit-compass run` запускает только запрошенные adapters,
обновляет manifest, items, observations, stories, metrics.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from ..config import DEFAULT_PROFILE, MonitorConfig
from ..intelligence.briefing import build_deterministic_briefing
from ..intelligence.clustering import cluster_items_with_history
from ..intelligence.compat import load_legacy_jsonl
from ..intelligence.migrations import migrate
from ..intelligence.models import ContentItem, SourceHealth, Story
from ..intelligence.ranking import compute_percentiles, rank_story
from ..intelligence.repository import (
    replace_run_signals,
    replace_run_stories,
    save_briefing,
    save_source_health,
    upsert_items,
    upsert_observations,
    upsert_run,
)

logger = logging.getLogger("reddit_compass")


@dataclass
class SourceResult:
    """Результат сбора из источника."""

    source_id: str
    status: str  # "ok", "error", "not_configured", "skipped"
    count: int = 0
    duration_sec: float = 0.0
    error_code: str | None = None
    message: str = ""


@dataclass
class RunResult:
    """Результат всего run."""

    run_id: str
    date: str
    profile: str
    status: str  # "complete", "partial"
    source_results: list[SourceResult] = field(default_factory=list)
    items: list[ContentItem] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def run_sources(
    config: MonitorConfig,
    snapshots_dir: Path,
    db_path: Path,
    sources: list[str] | None = None,
    profile: str = DEFAULT_PROFILE,
    analyze: bool = False,
    allow_partial: bool = False,
) -> RunResult:
    """Запускает сбор из указанных источников.

    Args:
        config: MonitorConfig.
        snapshots_dir: Путь к data/snapshots/.
        db_path: Путь к SQLite БД.
        sources: Список source_id для запуска. Если None — все enabled.
        profile: Имя профиля.
        analyze: Запустить LLM-анализ.
        allow_partial: Разрешить partial run.

    Returns:
        RunResult с итогами.
    """
    from ..db import get_db

    snapshot_date = datetime.now(UTC).strftime("%Y-%m-%d")
    run_id = f"{snapshot_date}:{profile}"
    started_at = _now_iso()

    snap_dir = snapshots_dir / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)

    conn = get_db(db_path)
    migrate(conn)

    upsert_run(
        conn,
        run_id=run_id,
        snapshot_date=snapshot_date,
        profile=profile,
        status="running",
        started_at=started_at,
    )
    conn.commit()

    source_results: list[SourceResult] = []
    all_items: list[ContentItem] = []
    analyzed_count = 0

    if sources is None:
        sources = ["reddit", "hackernews", "rss", "ladder", "producthunt"]

    # Маппинг source_id → имя файла в snapshot
    _FILE_MAP = {
        "reddit": "posts.jsonl",
        "hackernews": "hackernews.jsonl",
        "hn": "hackernews.jsonl",
        "rss": "rss.jsonl",
        "ladder": "ladder.jsonl",
        "producthunt": "producthunt.jsonl",
        "ph": "producthunt.jsonl",
    }

    for source_id in sources:
        result = await _run_single_source(source_id, config, snap_dir, snapshot_date)
        source_results.append(result)

        if result.status == "ok":
            filename = _FILE_MAP.get(source_id, f"{source_id}.jsonl")
            items, _ = load_legacy_jsonl(
                snap_dir / filename,
                filename,
                _now_iso(),
            )
            all_items.extend(items)

    if all_items:
        upsert_items(conn, all_items)

        observed_at = _now_iso()
        from ..intelligence.models import Observation

        observations = [
            Observation(run_id=run_id, item_id=item.item_id, observed_at=observed_at)
            for item in all_items
        ]
        upsert_observations(conn, observations)

        existing_stories = _load_recent_stories(conn, snapshot_date, profile)
        stories, _ = cluster_items_with_history(all_items, existing_stories)
        current_item_ids = {item.item_id for item in all_items}
        stories = [s for s in stories if any(item_id in current_item_ids for item_id in s.item_ids)]
        stories = [
            replace(
                story,
                item_ids=[item_id for item_id in story.item_ids if item_id in current_item_ids],
            )
            for story in stories
        ]
        percentiles = compute_percentiles(all_items)

        item_signal_scores: dict[str, dict[str, int]] = {}
        if analyze:
            from ..intelligence.llm_pipeline import build_deterministic_item_signals

            theme_catalog = {theme.id: theme.keywords for theme in config.themes}
            signals = build_deterministic_item_signals(
                all_items,
                theme_catalog=theme_catalog,
                analyzed_at=_now_iso(),
            )
            replace_run_signals(conn, run_id, signals)
            item_signal_scores = {sig.item_id: sig.goal_relevance for sig in signals}
            analyzed_count = len(signals)

        items_by_story: dict[str, list[ContentItem]] = {}
        for story in stories:
            items_by_story[story.story_id] = [
                item for item in all_items if item.item_id in story.item_ids
            ]

        metrics = []
        for story in stories:
            story_items = items_by_story.get(story.story_id, [])
            metric = rank_story(
                story=story,
                items=story_items,
                current_date=snapshot_date,
                percentiles=percentiles,
                run_id=run_id,
                item_signals=item_signal_scores or None,
            )
            metrics.append(metric)

        replace_run_stories(conn, run_id, stories, metrics)

        source_health = _build_source_health(source_results, all_items)
        save_source_health(conn, run_id, source_health)

        briefing = build_deterministic_briefing(
            run_id=run_id,
            date=snapshot_date,
            profile=profile,
            stories=stories,
            metrics=metrics,
            items_by_story=items_by_story,
            source_health=source_health,
        )
        save_briefing(conn, briefing)

    finished_at = _now_iso()
    status = "complete" if all(r.status == "ok" for r in source_results) else "partial"
    if analyze and all_items and analyzed_count != len(all_items):
        status = "partial"

    upsert_run(
        conn,
        run_id=run_id,
        snapshot_date=snapshot_date,
        profile=profile,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
    )
    conn.commit()
    conn.close()

    return RunResult(
        run_id=run_id,
        date=snapshot_date,
        profile=profile,
        status=status,
        source_results=source_results,
        items=all_items,
        started_at=started_at,
        finished_at=finished_at,
    )


def _load_recent_stories(
    conn: sqlite3.Connection,
    snapshot_date: str,
    profile: str,
) -> list[Story]:
    """Load recent stories for cross-date continuity."""
    rows = conn.execute(
        """SELECT s.story_id, s.canonical_key, s.title, s.summary_ru, s.domain_ids,
                  s.theme_ids, s.trend_id, s.lifecycle, s.project_scores,
                  s.first_seen, s.last_seen, s.item_ids
           FROM stories s
           JOIN story_metrics sm ON s.story_id = sm.story_id
           JOIN runs r ON r.run_id = sm.run_id
           WHERE r.snapshot_date < ? AND r.profile = ?
           ORDER BY r.snapshot_date DESC, sm.trend_score DESC
           LIMIT 500""",
        (snapshot_date, profile),
    ).fetchall()
    stories = []
    seen: set[str] = set()
    for row in rows:
        if row["story_id"] in seen:
            continue
        seen.add(row["story_id"])
        stories.append(
            Story(
                story_id=row["story_id"],
                canonical_key=row["canonical_key"],
                title=row["title"],
                summary_ru=row["summary_ru"],
                domain_ids=json.loads(row["domain_ids"] or '["other"]'),
                theme_ids=json.loads(row["theme_ids"] or "[]"),
                trend_id=row["trend_id"],
                lifecycle=row["lifecycle"],
                project_scores=json.loads(row["project_scores"] or "{}"),
                first_seen=row["first_seen"],
                last_seen=row["last_seen"],
                item_ids=json.loads(row["item_ids"] or "[]"),
            )
        )
    return stories


def _build_source_health(
    source_results: list[SourceResult],
    items: list[ContentItem],
) -> list[SourceHealth]:
    """Build provider-section source health rows for Radar coverage."""
    from ..sources.registry import SOURCES

    health: list[SourceHealth] = []
    by_provider_section: dict[tuple[str, str], list[ContentItem]] = {}
    for item in items:
        section = item.source_section or item.provider
        by_provider_section.setdefault((item.provider, section), []).append(item)

    for (provider, section), section_items in sorted(by_provider_section.items()):
        cluster = section_items[0].source_cluster
        source_id = f"{provider}:{section}"
        health.append(
            SourceHealth(
                source_id=source_id,
                provider=provider,
                cluster=cluster,
                status="ok",
                count=len(section_items),
                message=section,
            )
        )

    for result in source_results:
        source_def = SOURCES.get(result.source_id)
        cluster = source_def.cluster if source_def else "voices"
        ok_statuses = {"ok", "empty", "error", "not_configured", "skipped"}
        status = result.status if result.status in ok_statuses else "partial"
        health.append(
            SourceHealth(
                source_id=result.source_id,
                provider=source_def.provider if source_def else result.source_id,
                cluster=cluster,
                status=status,  # type: ignore[arg-type]
                count=result.count,
                duration_sec=result.duration_sec,
                error_code=result.error_code,
                message=result.message,
            )
        )

    return health


async def _run_single_source(
    source_id: str,
    config: MonitorConfig,
    snap_dir: Path,
    snapshot_date: str,
) -> SourceResult:
    """Запускает один источник."""
    # Нормализация aliases
    _ALIASES = {"hn": "hackernews", "ph": "producthunt"}
    source_id = _ALIASES.get(source_id, source_id)

    t0 = time.time()

    try:
        if source_id == "reddit":
            from ..export import write_posts_jsonl
            from ..fetch_subreddits import fetch_all_subreddits

            cards = await fetch_all_subreddits(config, snapshot_date)
            write_posts_jsonl(cards, snap_dir / "posts.jsonl")
            return SourceResult(
                source_id="reddit",
                status="ok" if cards else "empty",
                count=len(cards),
                duration_sec=round(time.time() - t0, 1),
            )

        if source_id == "hackernews":
            from ..export import write_posts_jsonl
            from ..sources.hackernews import fetch_hn_stories

            cards = await fetch_hn_stories(snapshot_date=snapshot_date)
            write_posts_jsonl(cards, snap_dir / "hackernews.jsonl")
            return SourceResult(
                source_id="hackernews",
                status="ok" if cards else "empty",
                count=len(cards),
                duration_sec=round(time.time() - t0, 1),
            )

        if source_id == "rss":
            from ..export import write_posts_jsonl
            from ..sources.rss import fetch_all_rss

            cards = await fetch_all_rss(snapshot_date=snapshot_date)
            write_posts_jsonl(cards, snap_dir / "rss.jsonl")
            return SourceResult(
                source_id="rss",
                status="ok" if cards else "empty",
                count=len(cards),
                duration_sec=round(time.time() - t0, 1),
            )

        if source_id == "ladder":
            from ..export import write_posts_jsonl
            from ..sources.ladder import fetch_all_ladder

            cards = await fetch_all_ladder(snapshot_date=snapshot_date)
            write_posts_jsonl(cards, snap_dir / "ladder.jsonl")
            return SourceResult(
                source_id="ladder",
                status="ok" if cards else "empty",
                count=len(cards),
                duration_sec=round(time.time() - t0, 1),
            )

        if source_id == "producthunt":
            from ..export import write_posts_jsonl
            from ..sources.producthunt import fetch_producthunt

            cards = await fetch_producthunt(snapshot_date=snapshot_date)
            write_posts_jsonl(cards, snap_dir / "producthunt.jsonl")
            return SourceResult(
                source_id="producthunt",
                status="ok" if cards else "empty",
                count=len(cards),
                duration_sec=round(time.time() - t0, 1),
            )

        return SourceResult(
            source_id=source_id,
            status="skipped",
            message=f"Unknown source: {source_id}",
        )

    except Exception as exc:
        logger.exception("Source %s failed", source_id)
        return SourceResult(
            source_id=source_id,
            status="error",
            duration_sec=round(time.time() - t0, 1),
            error_code=type(exc).__name__,
            message=str(exc)[:200],
        )
