"""Unified run: оркестрация сбора из всех источников.

Команда `reddit-compass run` запускает только запрошенные adapters,
обновляет manifest, items, observations, stories, metrics.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..config import MonitorConfig
from ..intelligence.briefing import build_deterministic_briefing
from ..intelligence.clustering import cluster_items
from ..intelligence.compat import load_legacy_jsonl
from ..intelligence.migrations import migrate
from ..intelligence.models import ContentItem, SourceHealth
from ..intelligence.ranking import compute_percentiles, rank_story
from ..intelligence.repository import (
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
    profile: str = "ai-native",
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

    if sources is None:
        sources = ["reddit", "hackernews", "rss", "ladder", "producthunt"]

    for source_id in sources:
        result = await _run_single_source(source_id, config, snap_dir, snapshot_date)
        source_results.append(result)

        if result.status == "ok":
            items, _ = load_legacy_jsonl(
                snap_dir / f"{source_id}.jsonl",
                f"{source_id}.jsonl",
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

        stories, _ = cluster_items(all_items)
        percentiles = compute_percentiles(all_items)

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
            )
            metrics.append(metric)

        replace_run_stories(conn, run_id, stories, metrics)

        source_health = [
            SourceHealth(
                source_id=r.source_id,
                provider=r.source_id,
                cluster="voices",
                status=r.status,  # type: ignore[arg-type]
                count=r.count,
                duration_sec=r.duration_sec,
                error_code=r.error_code,
                message=r.message,
            )
            for r in source_results
        ]
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


async def _run_single_source(
    source_id: str,
    config: MonitorConfig,
    snap_dir: Path,
    snapshot_date: str,
) -> SourceResult:
    """Запускает один источник."""
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
