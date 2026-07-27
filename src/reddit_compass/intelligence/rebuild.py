"""Rebuild SQLite v2 projection из snapshots.

Алгоритм:
1. Найти директории snapshots/YYYY-MM-DD.
2. Сортировать по дате.
3. Для каждой даты: загрузить legacy + v2 artifacts, нормализовать items,
   пересчитать observations/stories/metrics.
4. Commit отдельно для каждой даты.
5. Повторный rebuild не создаёт дублей и не стирает research_state.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .compat import load_legacy_jsonl
from .migrations import migrate
from .models import ContentItem, Observation
from .repository import upsert_items, upsert_observations, upsert_run

logger = logging.getLogger("reddit_compass")

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_LEGACY_FILES = [
    "posts.jsonl",
    "keyword-search.jsonl",
    "hackernews.jsonl",
    "rss.jsonl",
    "ladder.jsonl",
    "producthunt.jsonl",
]


def _find_snapshot_dates(snapshots_dir: Path) -> list[str]:
    """Находит все даты snapshots (YYYY-MM-DD), сортирует по возрастанию."""
    if not snapshots_dir.exists():
        return []
    dates = [d.name for d in snapshots_dir.iterdir() if d.is_dir() and _DATE_DIR_RE.match(d.name)]
    return sorted(dates)


def _load_items_for_date(snap_dir: Path, snapshot_date: str) -> tuple[list[ContentItem], int]:
    """Загружает все legacy JSONL для даты, возвращает (items, skipped)."""
    observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    all_items: list[ContentItem] = []
    total_skipped = 0

    for filename in _LEGACY_FILES:
        path = snap_dir / filename
        if not path.exists():
            continue
        items, skipped = load_legacy_jsonl(path, filename, observed_at)
        all_items.extend(items)
        total_skipped += skipped
        if items:
            logger.info("  %s: %d items", filename, len(items))

    return all_items, total_skipped


def _compute_observations(
    run_id: str, items: list[ContentItem], observed_at: str
) -> list[Observation]:
    """Создаёт observations для items (без percentile — нужен ranking)."""
    observations = []
    for item in items:
        observations.append(
            Observation(
                run_id=run_id,
                item_id=item.item_id,
                observed_at=observed_at,
            )
        )
    return observations


def rebuild_from_snapshots(
    conn: sqlite3.Connection,
    snapshots_dir: Path,
    profile: str = "ai-native",
    target_date: str | None = None,
) -> dict[str, int]:
    """Перестраивает SQLite v2 из snapshots.

    Args:
        conn: SQLite connection (уже мигрирована).
        snapshots_dir: Путь к data/snapshots/.
        profile: Имя профиля.
        target_date: Если указана, перестроить только эту дату.

    Returns:
        Статистика: {"dates": N, "items": N, "skipped": N}.
    """
    migrate(conn)

    dates = _find_snapshot_dates(snapshots_dir)
    if target_date:
        dates = [d for d in dates if d == target_date]
        if not dates:
            logger.warning("Date %s not found in snapshots", target_date)
            return {"dates": 0, "items": 0, "skipped": 0}

    total_items = 0
    total_skipped = 0

    for snapshot_date in dates:
        snap_dir = snapshots_dir / snapshot_date
        run_id = f"{snapshot_date}:{profile}"
        observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        logger.info("Rebuilding %s...", snapshot_date)

        items, skipped = _load_items_for_date(snap_dir, snapshot_date)
        total_skipped += skipped

        if not items:
            logger.warning("  No items found for %s", snapshot_date)
            continue

        upsert_run(
            conn,
            run_id=run_id,
            snapshot_date=snapshot_date,
            profile=profile,
            status="complete",
            started_at=observed_at,
            finished_at=observed_at,
        )

        upsert_items(conn, items)
        total_items += len(items)

        observations = _compute_observations(run_id, items, observed_at)
        upsert_observations(conn, observations)

        # Clustering + ranking + briefing
        from .briefing import build_deterministic_briefing
        from .clustering import cluster_items
        from .ranking import compute_percentiles, rank_story
        from .repository import replace_run_stories, save_briefing

        stories, _ = cluster_items(items)
        percentiles = compute_percentiles(items)

        items_by_story: dict[str, list] = {}
        for story in stories:
            items_by_story[story.story_id] = [
                item for item in items if item.item_id in story.item_ids
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

        briefing = build_deterministic_briefing(
            run_id=run_id,
            date=snapshot_date,
            profile=profile,
            stories=stories,
            metrics=metrics,
            items_by_story=items_by_story,
            source_health=[],
        )
        save_briefing(conn, briefing)

        conn.commit()
        logger.info(
            "  %s: %d items, %d stories, %d skipped",
            snapshot_date,
            len(items),
            len(stories),
            skipped,
        )

    return {"dates": len(dates), "items": total_items, "skipped": total_skipped}
