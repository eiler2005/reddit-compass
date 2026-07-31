"""Collection-only runtime.

This module owns network adapters and raw corpus persistence. It intentionally
does not import clustering, ranking, briefing, item-signal or LLM modules.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .config import DEFAULT_PROFILE, MonitorConfig
from .intelligence.compat import load_legacy_jsonl
from .intelligence.migrations import migrate
from .intelligence.models import (
    ContentItem,
    Observation,
    SourceHealth,
    SourceStatus,
)
from .intelligence.repository import upsert_items, upsert_observations, upsert_run
from .models import PostCard

logger = logging.getLogger("reddit_compass")

DEFAULT_SOURCES = ["reddit", "hackernews", "rss", "ladder", "producthunt"]
_ALIASES = {"hn": "hackernews", "ph": "producthunt"}
_FILE_MAP = {
    "reddit": "posts.jsonl",
    "hackernews": "hackernews.jsonl",
    "rss": "rss.jsonl",
    "ladder": "ladder.jsonl",
    "producthunt": "producthunt.jsonl",
}


@dataclass(frozen=True)
class SourceResult:
    source_id: str
    status: str
    count: int = 0
    duration_sec: float = 0.0
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True)
class CollectionResult:
    run_id: str
    date: str
    profile: str
    status: str
    source_results: list[SourceResult] = field(default_factory=list)
    items: list[ContentItem] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def collect_sources(
    config: MonitorConfig,
    snapshots_dir: Path,
    db_path: Path,
    sources: list[str] | None = None,
    profile: str = DEFAULT_PROFILE,
) -> CollectionResult:
    """Collect and persist raw corpus facts without derived intelligence."""
    from .db import get_db

    snapshot_date = datetime.now(UTC).strftime("%Y-%m-%d")
    run_id = f"{snapshot_date}:{profile}"
    started_at = now_iso()
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
    for requested_id in sources or DEFAULT_SOURCES:
        source_id = _ALIASES.get(requested_id, requested_id)
        result = await run_source_adapter(source_id, config, snap_dir, snapshot_date)
        source_results.append(result)
        if result.status not in {"ok", "empty"}:
            continue
        filename = _FILE_MAP.get(source_id, f"{source_id}.jsonl")
        items, _ = load_legacy_jsonl(
            snap_dir / filename,
            filename,
            now_iso(),
        )
        all_items.extend(items)

    finished_at = now_iso()
    status = (
        "complete"
        if source_results and all(result.status in {"ok", "empty"} for result in source_results)
        else "partial"
    )
    persist_collection(
        conn,
        run_id=run_id,
        snapshot_date=snapshot_date,
        profile=profile,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        items=all_items,
        source_results=source_results,
    )
    conn.close()
    return CollectionResult(
        run_id=run_id,
        date=snapshot_date,
        profile=profile,
        status=status,
        source_results=source_results,
        items=all_items,
        started_at=started_at,
        finished_at=finished_at,
    )


def finalize_snapshot_collection(
    config: MonitorConfig,
    snapshots_dir: Path,
    db_path: Path,
    *,
    sources: list[str] | None = None,
    profile: str = DEFAULT_PROFILE,
    snapshot_date: str | None = None,
) -> CollectionResult:
    """Persist an already collected snapshot as one truthful collection run.

    Source adapters are intentionally *not* invoked here.  This is the hand-off
    used when Reddit has been fetched from the Mac and the remaining adapters
    have written their JSONL artifacts on the VPS.  A missing artifact is an
    explicit error and therefore makes the resulting run ``partial``; stale or
    absent input can never silently turn into a completed collection.
    """

    del config  # Kept in the public signature alongside ``collect_sources``.
    date = snapshot_date or datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("snapshot_date must use YYYY-MM-DD") from exc
    run_id = f"{date}:{profile}"
    started_at = now_iso()
    snap_dir = snapshots_dir / date
    selected = [
        _ALIASES.get(source.strip(), source.strip()) for source in (sources or DEFAULT_SOURCES)
    ]

    source_results: list[SourceResult] = []
    all_items: list[ContentItem] = []
    for source_id in selected:
        filename = _FILE_MAP.get(source_id, f"{source_id}.jsonl")
        path = snap_dir / filename
        if not path.exists():
            source_results.append(
                SourceResult(
                    source_id=source_id,
                    status="error",
                    error_code="snapshot_missing",
                    message=f"Missing snapshot artifact: {filename}",
                )
            )
            continue
        try:
            items, _ = load_legacy_jsonl(path, filename, started_at)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            source_results.append(
                SourceResult(
                    source_id=source_id,
                    status="error",
                    error_code="snapshot_invalid",
                    message=f"Cannot read {filename}: {exc.__class__.__name__}",
                )
            )
            continue
        all_items.extend(items)
        source_results.append(
            SourceResult(
                source_id=source_id,
                status="ok" if items else "empty",
                count=len(items),
                message=f"Finalized existing snapshot artifact: {filename}",
            )
        )

    finished_at = now_iso()
    status = (
        "complete"
        if source_results and all(result.status in {"ok", "empty"} for result in source_results)
        else "partial"
    )
    from .db import get_db

    conn = get_db(db_path)
    try:
        migrate(conn)
        persist_collection(
            conn,
            run_id=run_id,
            snapshot_date=date,
            profile=profile,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            items=all_items,
            source_results=source_results,
        )
    finally:
        conn.close()
    return CollectionResult(
        run_id=run_id,
        date=date,
        profile=profile,
        status=status,
        source_results=source_results,
        items=all_items,
        started_at=started_at,
        finished_at=finished_at,
    )


def persist_collection(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    snapshot_date: str,
    profile: str,
    status: str,
    started_at: str,
    finished_at: str,
    items: list[ContentItem],
    source_results: list[SourceResult],
) -> None:
    """Persist only runs/items/observations/source health."""
    if items:
        upsert_items(conn, items)
        upsert_observations(
            conn,
            [
                Observation(
                    run_id=run_id,
                    item_id=item.item_id,
                    observed_at=finished_at,
                )
                for item in items
            ],
        )
    _upsert_source_health(conn, run_id, _build_source_health(source_results, items))
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


def _upsert_source_health(
    conn: sqlite3.Connection,
    run_id: str,
    health_rows: list[SourceHealth],
) -> None:
    for health in health_rows:
        conn.execute(
            """INSERT INTO source_health
               (run_id, source_id, provider, cluster, status, count,
                duration_sec, error_code, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, source_id) DO UPDATE SET
                 provider = excluded.provider,
                 cluster = excluded.cluster,
                 status = excluded.status,
                 count = excluded.count,
                 duration_sec = excluded.duration_sec,
                 error_code = excluded.error_code,
                 message = excluded.message""",
            (
                run_id,
                health.source_id,
                health.provider,
                health.cluster,
                health.status,
                health.count,
                health.duration_sec,
                health.error_code,
                health.message,
            ),
        )


def _build_source_health(
    source_results: list[SourceResult],
    items: list[ContentItem],
) -> list[SourceHealth]:
    from .sources.registry import SOURCES

    health: list[SourceHealth] = []
    by_provider_section: dict[tuple[str, str], list[ContentItem]] = {}
    for item in items:
        section = item.source_section or item.provider
        by_provider_section.setdefault((item.provider, section), []).append(item)
    for (provider, section), section_items in sorted(by_provider_section.items()):
        health.append(
            SourceHealth(
                source_id=f"{provider}:{section}",
                provider=provider,
                cluster=section_items[0].source_cluster,
                status="ok",
                count=len(section_items),
                message=section,
            )
        )
    allowed_statuses = {"ok", "empty", "error", "not_configured", "skipped"}
    for result in source_results:
        source_definition = SOURCES.get(result.source_id)
        status = cast(
            SourceStatus,
            result.status if result.status in allowed_statuses else "partial",
        )
        health.append(
            SourceHealth(
                source_id=result.source_id,
                provider=(
                    source_definition.provider
                    if source_definition is not None
                    else result.source_id
                ),
                cluster=(source_definition.cluster if source_definition is not None else "voices"),
                status=status,
                count=result.count,
                duration_sec=result.duration_sec,
                error_code=result.error_code,
                message=result.message,
            )
        )
    return health


async def run_source_adapter(
    source_id: str,
    config: MonitorConfig,
    snap_dir: Path,
    snapshot_date: str,
) -> SourceResult:
    """Run one read-only source adapter."""
    source_id = _ALIASES.get(source_id, source_id)
    started = time.monotonic()
    try:
        cards = await _fetch_source_cards(source_id, config, snapshot_date)
        if cards is None:
            return SourceResult(
                source_id=source_id,
                status="skipped",
                message=f"Unknown source: {source_id}",
            )
        from .export import write_posts_jsonl

        write_posts_jsonl(cards, snap_dir / _FILE_MAP[source_id])
        return SourceResult(
            source_id=source_id,
            status="ok" if cards else "empty",
            count=len(cards),
            duration_sec=round(time.monotonic() - started, 1),
        )
    except Exception as exc:
        logger.exception("Source %s failed", source_id)
        return SourceResult(
            source_id=source_id,
            status="error",
            duration_sec=round(time.monotonic() - started, 1),
            error_code=type(exc).__name__,
            message=str(exc)[:200],
        )


async def _fetch_source_cards(
    source_id: str,
    config: MonitorConfig,
    snapshot_date: str,
) -> list[PostCard] | None:
    if source_id == "reddit":
        from .fetch_subreddits import fetch_all_subreddits

        return list(await fetch_all_subreddits(config, snapshot_date))
    if source_id == "hackernews":
        from .sources.hackernews import fetch_hn_stories

        return list(await fetch_hn_stories(snapshot_date=snapshot_date))
    if source_id == "rss":
        from .sources.rss import fetch_all_rss

        return list(await fetch_all_rss(snapshot_date=snapshot_date))
    if source_id == "ladder":
        from .sources.ladder import fetch_all_ladder

        return list(await fetch_all_ladder(snapshot_date=snapshot_date))
    if source_id == "producthunt":
        from .sources.producthunt import fetch_producthunt

        return list(await fetch_producthunt(snapshot_date=snapshot_date))
    return None
