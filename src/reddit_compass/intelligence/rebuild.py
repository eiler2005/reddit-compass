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
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from ..config import DEFAULT_PROFILE
from .compat import load_legacy_jsonl
from .migrations import migrate
from .models import ContentItem, Observation, SourceHealth
from .repository import save_source_health, upsert_items, upsert_observations, upsert_run

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
    profile: str = DEFAULT_PROFILE,
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

        # Очищаем старые observations для этого run (idempotent rebuild)
        conn.execute("DELETE FROM observations WHERE run_id = ?", (run_id,))
        observations = _compute_observations(run_id, items, observed_at)
        upsert_observations(conn, observations)

        # Clustering + ranking + briefing
        from .briefing import build_deterministic_briefing
        from .clustering import cluster_items_with_history
        from .ranking import compute_percentiles, rank_story
        from .repository import replace_run_stories, save_briefing

        # Загружаем stories ТОЛЬКО из предыдущей даты (не все!)
        # Это даёт cross-date continuity без O(n*m) взрыва
        existing_stories = []
        prev_date_row = conn.execute(
            "SELECT snapshot_date FROM runs "
            "WHERE snapshot_date < ? AND profile = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (snapshot_date, profile),
        ).fetchone()

        if prev_date_row:
            prev_run_id = f"{prev_date_row[0]}:{profile}"
            prev_rows = conn.execute(
                "SELECT s.story_id, s.canonical_key, s.title, s.summary_ru, "
                "s.domain_ids, s.theme_ids, s.trend_id, s.lifecycle, s.project_scores, "
                "s.first_seen, s.last_seen, s.item_ids "
                "FROM stories s "
                "JOIN story_metrics sm ON s.story_id = sm.story_id "
                "WHERE sm.run_id = ? "
                "ORDER BY sm.trend_score DESC LIMIT 200",
                (prev_run_id,),
            ).fetchall()
            import json as _json

            for row in prev_rows:
                from .models import Story

                existing_stories.append(
                    Story(
                        story_id=row["story_id"],
                        canonical_key=row["canonical_key"],
                        title=row["title"],
                        summary_ru=row["summary_ru"],
                        domain_ids=_json.loads(row["domain_ids"] or '["other"]'),
                        theme_ids=_json.loads(row["theme_ids"] or "[]"),
                        trend_id=row["trend_id"],
                        lifecycle=row["lifecycle"],
                        project_scores=_json.loads(row["project_scores"] or "{}"),
                        first_seen=row["first_seen"],
                        last_seen=row["last_seen"],
                        item_ids=_json.loads(row["item_ids"]),
                    )
                )

        stories, _ = cluster_items_with_history(items, existing_stories)

        # Фильтруем: оставляем только stories с items из текущего run
        current_item_ids = {item.item_id for item in items}
        stories = [s for s in stories if any(iid in current_item_ids for iid in s.item_ids)]
        stories = [
            replace(
                story,
                item_ids=[item_id for item_id in story.item_ids if item_id in current_item_ids],
            )
            for story in stories
        ]

        # Cross-source second pass: merge stories from different providers
        from .clustering import merge_cross_source_candidates

        items_by_id = {item.item_id: item for item in items}
        items_by_story: dict[str, list[ContentItem]] = {}
        for story in stories:
            items_by_story[story.story_id] = [
                items_by_id[iid] for iid in story.item_ids if iid in items_by_id
            ]
        stories = merge_cross_source_candidates(stories, items_by_story)

        percentiles = compute_percentiles(items)

        # Deterministic item signals (100% coverage, no network)
        from .llm_pipeline import build_deterministic_item_signals
        from .repository import replace_run_signals

        signals = build_deterministic_item_signals(items, analyzed_at=observed_at)
        replace_run_signals(conn, run_id, signals)
        item_signal_scores = {sig.item_id: sig.goal_relevance for sig in signals}

        # Быстрый lookup: item_id → ContentItem (перестраиваем после merge)
        items_by_id = {item.item_id: item for item in items}
        items_by_story = {
            story.story_id: [items_by_id[iid] for iid in story.item_ids if iid in items_by_id]
            for story in stories
        }

        metrics = []
        for story in stories:
            story_items = items_by_story.get(story.story_id, [])

            # Ищем предыдущие метрики для этого story
            prev_row = conn.execute(
                "SELECT item_count, source_count FROM story_metrics "
                "WHERE story_id = ? AND run_id != ? "
                "ORDER BY run_id DESC LIMIT 1",
                (story.story_id, run_id),
            ).fetchone()

            prev_item_count = prev_row["item_count"] if prev_row else None
            prev_source_count = prev_row["source_count"] if prev_row else None

            # Вычисляем gap_days для resurfacing detection
            gap_days = None
            if story.last_seen and story.last_seen != snapshot_date:
                from datetime import date as _date

                try:
                    last = _date.fromisoformat(story.last_seen)
                    curr = _date.fromisoformat(snapshot_date)
                    gap_days = (curr - last).days
                except ValueError:
                    pass

            metric = rank_story(
                story=story,
                items=story_items,
                current_date=snapshot_date,
                percentiles=percentiles,
                run_id=run_id,
                prev_item_count=prev_item_count,
                prev_source_count=prev_source_count,
                gap_days=gap_days,
                item_signals=item_signal_scores,
            )
            metrics.append(metric)

        replace_run_stories(conn, run_id, stories, metrics)
        source_health = _build_rebuild_source_health(items)
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

        conn.commit()
        logger.info(
            "  %s: %d items, %d stories, %d skipped",
            snapshot_date,
            len(items),
            len(stories),
            skipped,
        )

    return {"dates": len(dates), "items": total_items, "skipped": total_skipped}


def _build_rebuild_source_health(items: list[ContentItem]) -> list[SourceHealth]:
    by_provider_section: dict[tuple[str, str], list[ContentItem]] = {}
    for item in items:
        key = (item.provider, item.source_section or item.provider)
        by_provider_section.setdefault(key, []).append(item)
    return [
        SourceHealth(
            source_id=f"{provider}:{section}",
            provider=provider,
            cluster=section_items[0].source_cluster,
            status="ok",
            count=len(section_items),
            message=section,
        )
        for (provider, section), section_items in sorted(by_provider_section.items())
    ]
