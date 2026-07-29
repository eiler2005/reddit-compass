"""Trend type classification based on provenance and source composition."""

from __future__ import annotations

from typing import Literal

TrendType = Literal[
    "cross_source_confirmed",
    "reddit_first",
    "mainstream_only",
    "community_only",
    "perspective_gap",
    "project_relevant",
    "unclassified",
]


def classify_trend_type(
    *,
    source_count: int,
    story_count: int,
    providers: set[str],
    has_reddit: bool,
    has_mainstream: bool,
    has_qwen_review: bool,
    has_manual_label: bool,
    project_scores: dict[str, int] | None = None,
    avg_pulse_score: float = 0.0,
    mainstream_coverage: int = 0,
) -> TrendType:
    """Classify a trend into one of the defined types.

    Priority order:
    1. project_relevant — has non-zero project scores
    2. cross_source_confirmed — multiple providers + Qwen/manual confirmed
    3. perspective_gap — high Reddit pulse + low mainstream coverage
    4. reddit_first — Reddit present, no mainstream yet
    5. community_only — Reddit only, high pulse
    6. mainstream_only — no Reddit, mainstream only
    7. unclassified
    """
    if project_scores and any(v > 0 for v in project_scores.values()):
        return "project_relevant"

    is_cross_source = len(providers) > 1
    is_confirmed = has_qwen_review or has_manual_label

    if is_cross_source and is_confirmed:
        return "cross_source_confirmed"

    if avg_pulse_score >= 60 and mainstream_coverage < 2:
        return "perspective_gap"

    if has_reddit and not has_mainstream:
        if avg_pulse_score >= 50:
            return "community_only"
        return "reddit_first"

    if has_mainstream and not has_reddit:
        return "mainstream_only"

    if is_cross_source:
        return "cross_source_confirmed"

    return "unclassified"
