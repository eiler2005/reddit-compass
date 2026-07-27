"""Ranking: расчёт метрик stories.

Компоненты (все 0..100):
- goal_relevance
- cross_source_coverage
- momentum
- novelty
- evidence_quality

trend_score = 0.30*goal + 0.25*coverage + 0.20*momentum + 0.15*novelty + 0.10*evidence
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .models import (
    ConfidenceLevel,
    ContentItem,
    Story,
    StoryDirection,
    StoryMetric,
)

_COVERAGE_MAP = {1: 25, 2: 55, 3: 75, 4: 90}
_COVERAGE_MAX = 100

_NOVELTY_MAP = [
    (0, 100),  # first_seen today
    (2, 80),  # 1-2 days ago
    (6, 55),  # 3-6 days ago
    (13, 30),  # 7-13 days ago
]
_NOVELTY_OLD = 10
_NOVELTY_RESURFACING = 75

_SCOPE_QUALITY = {
    "headline": 25,
    "abstract": 50,
    "excerpt": 75,
    "full": 100,
}


@dataclass
class StoryItems:
    """Items и их метрики для одной story."""

    story: Story
    items: list[ContentItem]
    providers: set[str]
    clusters: set[str]


def compute_engagement_value(item: ContentItem) -> float:
    """Вычисляет engagement value для percentile."""
    eng = item.raw_engagement
    if item.provider == "reddit":
        return math.log1p(eng.get("score", 0)) + 0.5 * math.log1p(eng.get("comments", 0))
    if item.provider == "hackernews":
        return math.log1p(eng.get("points", 0)) + 0.5 * math.log1p(eng.get("comments", 0))
    if item.provider == "producthunt":
        return math.log1p(eng.get("votes", 0)) + 0.5 * math.log1p(eng.get("comments", 0))
    return 0.0


def compute_percentiles(items: list[ContentItem]) -> dict[str, float]:
    """Вычисляет engagement percentile для каждого item внутри provider."""
    by_provider: dict[str, list[tuple[str, float]]] = {}
    for item in items:
        value = compute_engagement_value(item)
        by_provider.setdefault(item.provider, []).append((item.item_id, value))

    percentiles: dict[str, float] = {}
    for _provider, values in by_provider.items():
        sorted_vals = sorted(values, key=lambda x: x[1])
        n = len(sorted_vals)
        for rank, (item_id, _) in enumerate(sorted_vals):
            percentiles[item_id] = (rank / n * 100) if n > 1 else 50.0

    return percentiles


def goal_relevance_score(
    items: list[ContentItem],
    goal_weights: dict[str, float] | None = None,
    item_signals: dict[str, dict[str, int]] | None = None,
) -> float:
    """Goal relevance: weighted average или keyword fallback (max 60)."""
    if item_signals:
        total = 0.0
        count = 0
        for item in items:
            if item.item_id in item_signals:
                relevance = item_signals[item.item_id]
                if goal_weights:
                    weighted = sum(relevance.get(g, 0) * w for g, w in goal_weights.items()) / sum(
                        goal_weights.values()
                    )
                else:
                    weighted = sum(relevance.values()) / len(relevance) if relevance else 0
                total += weighted
                count += 1
        return total / count if count else 0.0

    return min(60.0, len(items) * 10.0)


def cross_source_coverage_score(clusters: set[Any]) -> float:
    """Cross-source coverage по числу независимых clusters."""
    n = len(clusters)
    if n <= 0:
        return 0.0
    if n >= 5:
        return float(_COVERAGE_MAX)
    return float(_COVERAGE_MAP.get(n, 25))


def momentum_score(
    engagement_percentile: float,
    item_count_delta: float | None,
    source_count_delta: float | None,
) -> float:
    """Momentum: 0.50*percentile + 0.30*item_delta + 0.20*source_delta."""
    item_delta_norm = 50.0 if item_count_delta is None else max(0.0, min(100.0, item_count_delta))
    source_delta_norm = (
        50.0 if source_count_delta is None else max(0.0, min(100.0, source_count_delta))
    )

    return 0.50 * engagement_percentile + 0.30 * item_delta_norm + 0.20 * source_delta_norm


def novelty_score(first_seen: str, current_date: str, is_resurfacing: bool = False) -> float:
    """Novelty по возрасту first_seen."""
    if is_resurfacing:
        return float(_NOVELTY_RESURFACING)

    if not first_seen or not current_date:
        return 50.0

    try:
        from datetime import date

        first = date.fromisoformat(first_seen)
        current = date.fromisoformat(current_date)
        days = (current - first).days
    except ValueError:
        return 50.0

    if days <= 0:
        return 100.0
    for max_days, score in _NOVELTY_MAP:
        if days <= max_days:
            return float(score)
    return float(_NOVELTY_OLD)


def evidence_quality_score(items: list[ContentItem]) -> float:
    """Evidence quality: среднее двух лучших независимых providers."""
    by_provider: dict[str, list[float]] = {}
    for item in items:
        quality = _SCOPE_QUALITY.get(item.content_scope, 25)
        by_provider.setdefault(item.provider, []).append(float(quality))

    if not by_provider:
        return 0.0

    provider_best = [max(vals) for vals in by_provider.values()]
    provider_best.sort(reverse=True)

    if len(provider_best) == 1:
        return min(60.0, provider_best[0])

    top_two = provider_best[:2]
    return sum(top_two) / len(top_two)


def compute_confidence(providers: set[str], evidence_quality: float) -> ConfidenceLevel:
    """Confidence: high/medium/low."""
    n_providers = len(providers)
    if n_providers >= 2 and evidence_quality >= 60:
        return "high"
    if n_providers >= 2 or evidence_quality >= 75:
        return "medium"
    return "low"


def compute_direction(
    first_seen: str,
    current_date: str,
    prev_item_count: int | None,
    prev_source_count: int | None,
    curr_item_count: int,
    curr_source_count: int,
    gap_days: int | None = None,
) -> StoryDirection:
    """Direction: new/growing/stable/fading/resurfacing."""
    if gap_days is not None and gap_days >= 14:
        return "resurfacing"

    if first_seen == current_date:
        return "new"

    if prev_item_count is not None and prev_source_count is not None:
        if prev_item_count > 0:
            item_growth = (curr_item_count - prev_item_count) / prev_item_count
        else:
            item_growth = 1.0 if curr_item_count > 0 else 0.0

        if prev_source_count > 0:
            source_growth = (curr_source_count - prev_source_count) / prev_source_count
        else:
            source_growth = 1.0 if curr_source_count > 0 else 0.0

        if item_growth >= 0.30 or source_growth >= 0.30:
            return "growing"
        if item_growth <= -0.30 and source_growth <= -0.30:
            return "fading"

    return "stable"


def compute_trend_score(
    goal_relevance: float,
    cross_source_coverage: float,
    momentum: float,
    novelty: float,
    evidence_quality: float,
) -> float:
    """Итоговый trend_score."""
    return (
        0.30 * goal_relevance
        + 0.25 * cross_source_coverage
        + 0.20 * momentum
        + 0.15 * novelty
        + 0.10 * evidence_quality
    )


def rank_story(
    story: Story,
    items: list[ContentItem],
    current_date: str,
    percentiles: dict[str, float],
    prev_item_count: int | None = None,
    prev_source_count: int | None = None,
    gap_days: int | None = None,
    goal_weights: dict[str, float] | None = None,
    item_signals: dict[str, dict[str, int]] | None = None,
    run_id: str = "",
) -> StoryMetric:
    """Вычисляет все метрики для story."""
    providers = {item.provider for item in items}
    clusters = {item.source_cluster for item in items}

    item_percentiles = [percentiles.get(item.item_id, 50.0) for item in items]
    median_percentile = (
        sorted(item_percentiles)[len(item_percentiles) // 2] if item_percentiles else 50.0
    )

    item_count_delta = None
    source_count_delta = None
    if prev_item_count is not None:
        item_count_delta = (len(items) - prev_item_count) * 10
    if prev_source_count is not None:
        source_count_delta = (len(providers) - prev_source_count) * 20

    is_resurfacing = gap_days is not None and gap_days >= 14

    goal_rel = goal_relevance_score(items, goal_weights, item_signals)
    coverage = cross_source_coverage_score(clusters)
    momentum = momentum_score(median_percentile, item_count_delta, source_count_delta)
    novelty = novelty_score(story.first_seen, current_date, is_resurfacing)
    evidence = evidence_quality_score(items)

    trend = compute_trend_score(goal_rel, coverage, momentum, novelty, evidence)
    confidence = compute_confidence(providers, evidence)
    direction = compute_direction(
        story.first_seen,
        current_date,
        prev_item_count,
        prev_source_count,
        len(items),
        len(providers),
        gap_days,
    )

    return StoryMetric(
        run_id=run_id,
        story_id=story.story_id,
        goal_relevance=goal_rel,
        cross_source_coverage=coverage,
        momentum=momentum,
        novelty=novelty,
        evidence_quality=evidence,
        trend_score=trend,
        confidence=confidence,
        direction=direction,
        item_count=len(items),
        source_count=len(providers),
    )
