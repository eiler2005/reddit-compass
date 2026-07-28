"""Deterministic briefing: создаёт briefing.json без LLM.

Даже без Qwen система создаёт корректный briefing:
- top_changes: первые 5 stories по trend score (new/growing/resurfacing)
- watchlist: следующие 10 (stable/growing)
- why_it_matters: безопасная шаблонная строка
- pain_points, column_ideas, narrative_shifts: пустые без LLM
- status=partial если отсутствует expected source
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from .models import (
    Briefing,
    BriefingStory,
    ContentItem,
    EvidenceRef,
    SourceHealth,
    Story,
    StoryMetric,
)

_TOP_CHANGES_LIMIT = 5
_MEGA_STORIES_LIMIT = 10
_WATCHLIST_LIMIT = 10

_WHY_IT_MATTERS_TEMPLATE = (
    "Сюжет из {source_count} источник(ов), {item_count} материал(ов). Направление: {direction}."
)


def build_evidence_refs(items: list[ContentItem], limit: int = 3) -> list[EvidenceRef]:
    """Строит evidence refs из items (топ по content_scope)."""
    scope_order = {"full": 0, "excerpt": 1, "abstract": 2, "headline": 3}
    sorted_items = sorted(items, key=lambda i: scope_order.get(i.content_scope, 4))

    refs = []
    seen_providers: set[str] = set()
    for item in sorted_items:
        if item.provider in seen_providers:
            continue
        refs.append(
            EvidenceRef(
                item_id=item.item_id,
                provider=item.provider,
                source_cluster=item.source_cluster,
                url=item.canonical_url,
                title=item.title,
                excerpt=item.excerpt[:200] if item.excerpt else "",
                content_scope=item.content_scope,
            )
        )
        seen_providers.add(item.provider)
        if len(refs) >= limit:
            break

    return refs


def build_briefing_story(
    story: Story,
    metric: StoryMetric,
    items: list[ContentItem],
) -> BriefingStory:
    """Строит BriefingStory из story, metric и items."""
    why = _WHY_IT_MATTERS_TEMPLATE.format(
        source_count=metric.source_count,
        item_count=metric.item_count,
        direction=metric.direction,
    )

    evidence = build_evidence_refs(items)

    breakdown = {
        "goal_relevance": metric.goal_relevance,
        "cross_source_coverage": metric.cross_source_coverage,
        "momentum": metric.momentum,
        "novelty": metric.novelty,
        "evidence_quality": metric.evidence_quality,
    }

    return BriefingStory(
        story=story,
        metric=metric,
        why_it_matters=why,
        evidence=evidence,
        score_breakdown=breakdown,
    )


def build_deterministic_briefing(
    run_id: str,
    date: str,
    profile: str,
    stories: list[Story],
    metrics: list[StoryMetric],
    items_by_story: dict[str, list[ContentItem]],
    source_health: list[SourceHealth],
    expected_sources: set[str] | None = None,
) -> Briefing:
    """Создаёт deterministic briefing без LLM.

    Args:
        run_id: ID run.
        date: Дата snapshot.
        profile: Имя профиля.
        stories: Все stories.
        metrics: Метрики для каждой story.
        items_by_story: Items по story_id.
        source_health: Статусы источников.
        expected_sources: Ожидаемые источники для complete run.
    """
    stories_by_id = {s.story_id: s for s in stories}

    sorted_metrics = sorted(metrics, key=lambda m: m.trend_score, reverse=True)

    top_changes: list[BriefingStory] = []
    mega_stories: list[BriefingStory] = []
    watchlist: list[BriefingStory] = []

    # Top changes: new/growing/resurfacing
    # Watchlist: остальные
    used_story_ids: set[str] = set()
    for metric in sorted_metrics:
        story = stories_by_id.get(metric.story_id)
        if not story:
            continue

        items = items_by_story.get(metric.story_id, [])
        briefing_story = build_briefing_story(story, metric, items)

        if metric.direction in ("new", "growing", "resurfacing"):
            if len(top_changes) < _TOP_CHANGES_LIMIT:
                top_changes.append(briefing_story)
                used_story_ids.add(metric.story_id)
            elif len(watchlist) < _WATCHLIST_LIMIT:
                watchlist.append(briefing_story)
        elif metric.direction in ("stable", "fading") and len(watchlist) < _WATCHLIST_LIMIT:
            watchlist.append(briefing_story)

    # Mega stories exclude already highlighted top changes to reduce Radar repeats.
    for metric in sorted_metrics:
        if metric.story_id in used_story_ids:
            continue
        story = stories_by_id.get(metric.story_id)
        if not story:
            continue
        items = items_by_story.get(metric.story_id, [])
        mega_stories.append(build_briefing_story(story, metric, items))
        if len(mega_stories) >= _MEGA_STORIES_LIMIT:
            break

    status: Literal["complete", "partial"] = "complete"
    if expected_sources:
        actual_sources = {sh.source_id for sh in source_health if sh.status == "ok"}
        if not expected_sources.issubset(actual_sources):
            status = "partial"

    if any(sh.status in ("error", "not_configured") for sh in source_health):
        status = "partial"

    return Briefing(
        schema_version=1,
        run_id=run_id,
        date=date,
        profile=profile,
        status=status,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        source_health=source_health,
        top_changes=top_changes,
        mega_stories=mega_stories,
        watchlist=watchlist,
        pain_points=[],
        column_ideas=[],
        narrative_shifts=[],
    )
