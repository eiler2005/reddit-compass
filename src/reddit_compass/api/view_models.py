"""View models для Jinja2 UI.

Преобразует domain models в данные для шаблонов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..intelligence.models import Briefing, BriefingStory, ResearchState, Story


@dataclass
class RunSummary:
    """Единый источник правды о run."""

    run_id: str
    date: str
    profile: str
    status: Literal["complete", "partial", "running", "failed"]
    started_at: str | None = None
    finished_at: str | None = None
    last_success_at: str | None = None
    unique_item_count: int = 0
    analyzed_item_count: int = 0
    story_count: int = 0
    expected_provider_count: int = 0
    successful_provider_count: int = 0
    fresh_provider_count: int = 0
    adapter_family_count: int = 0


@dataclass
class SourceCoverageRow:
    """Строка покрытия источника."""

    source_id: str
    label: str
    adapter: str
    source_cluster: str
    configured: bool = True
    expected: bool = True
    attempted: bool = False
    status: Literal["ok", "empty", "error", "stale", "skipped", "not_configured"] = "skipped"
    item_count: int = 0
    content_scope: Literal["headline", "abstract", "excerpt", "full"] = "headline"
    last_success_at: str | None = None
    freshness_hours: float | None = None
    duration_sec: float | None = None
    message: str = ""


@dataclass
class CloudNode:
    """Узел облака тем."""

    node_id: str
    label_ru: str
    label_original: str | None = None
    item_count: int = 0
    story_count: int = 0
    provider_count: int = 0
    source_cluster_count: int = 0
    direction: str = "stable"
    delta_1d: int | None = None
    trend_score: float = 0.0
    url: str = ""


@dataclass
class StoryCardView:
    """View model для story card."""

    story_id: str
    title: str
    summary_ru: str
    direction: str
    direction_label: str
    trend_score: float
    confidence: str
    why_it_matters: str
    source_count: int
    item_count: int
    clusters: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    research_state: ResearchState | None = None


@dataclass
class BriefingView:
    """View model для briefing страницы."""

    date: str
    profile: str
    status: str
    status_label: str
    generated_at: str
    top_changes: list[StoryCardView] = field(default_factory=list)
    mega_stories: list[StoryCardView] = field(default_factory=list)
    watchlist: list[StoryCardView] = field(default_factory=list)
    stable_themes: list[CloudNode] = field(default_factory=list)
    emerging_candidates: list[CloudNode] = field(default_factory=list)
    pain_point_cloud: list[CloudNode] = field(default_factory=list)
    pain_points: list[dict[str, Any]] = field(default_factory=list)
    column_ideas: list[dict[str, Any]] = field(default_factory=list)
    narrative_shifts: list[dict[str, Any]] = field(default_factory=list)
    source_health: list[dict[str, Any]] = field(default_factory=list)
    prev_date: str | None = None
    next_date: str | None = None


@dataclass
class StoryDetailView:
    """View model для story detail страницы."""

    story_id: str
    title: str
    summary_ru: str
    theme_labels: list[str]
    why_it_matters: str
    timeline: list[dict[str, Any]] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    evidence_by_cluster: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    research_state: ResearchState | None = None


@dataclass
class ExploreView:
    """View model для explore страницы."""

    stories: list[StoryCardView] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
    total_pages: int = 0
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunView:
    """View model для run."""

    run_id: str
    date: str
    profile: str
    status: str
    status_label: str
    started_at: str
    finished_at: str | None
    item_count: int = 0
    story_count: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)


_DIRECTION_LABELS = {
    "new": "🆕 Новый",
    "growing": "📈 Растёт",
    "stable": "➡️ Стабильный",
    "fading": "📉 Затухает",
    "resurfacing": "🔄 Возвращается",
}

_STATUS_LABELS = {
    "complete": "✅ Полный",
    "partial": "⚠️ Частичный",
    "running": "🔄 Выполняется",
    "error": "❌ Ошибка",
}


def direction_label(direction: str) -> str:
    return _DIRECTION_LABELS.get(direction, direction)


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


def briefing_to_view(briefing: Briefing) -> BriefingView:
    """Преобразует Briefing в BriefingView."""

    def _story_to_card(bs: BriefingStory) -> StoryCardView:
        clusters: list[str] = list({e.source_cluster for e in bs.evidence})
        return StoryCardView(
            story_id=bs.story.story_id,
            title=bs.story.title,
            summary_ru=bs.story.summary_ru,
            direction=bs.metric.direction,
            direction_label=direction_label(bs.metric.direction),
            trend_score=bs.metric.trend_score,
            confidence=bs.metric.confidence,
            why_it_matters=bs.why_it_matters,
            source_count=bs.metric.source_count,
            item_count=bs.metric.item_count,
            clusters=clusters,
            evidence=[
                {
                    "item_id": e.item_id,
                    "provider": e.provider,
                    "source_cluster": e.source_cluster,
                    "url": e.url,
                    "title": e.title,
                    "excerpt": e.excerpt,
                    "content_scope": e.content_scope,
                }
                for e in bs.evidence
            ],
            score_breakdown=bs.score_breakdown,
        )

    return BriefingView(
        date=briefing.date,
        profile=briefing.profile,
        status=briefing.status,
        status_label=status_label(briefing.status),
        generated_at=briefing.generated_at,
        top_changes=[_story_to_card(bs) for bs in briefing.top_changes],
        mega_stories=[_story_to_card(bs) for bs in briefing.mega_stories],
        watchlist=[_story_to_card(bs) for bs in briefing.watchlist],
        pain_points=[
            {"text": gp.text, "evidence_ids": gp.evidence_ids} for gp in briefing.pain_points
        ],
        column_ideas=[
            {"text": gp.text, "evidence_ids": gp.evidence_ids} for gp in briefing.column_ideas
        ],
        narrative_shifts=[
            {"text": gp.text, "evidence_ids": gp.evidence_ids} for gp in briefing.narrative_shifts
        ],
        source_health=[
            {
                "source_id": sh.source_id,
                "provider": sh.provider,
                "status": sh.status,
                "count": sh.count,
                "message": sh.message,
            }
            for sh in briefing.source_health
        ],
    )


def story_to_detail_view(
    story: Story,
    metrics: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    research_state: ResearchState | None = None,
) -> StoryDetailView:
    """Преобразует Story в StoryDetailView."""
    evidence_by_cluster: dict[str, list[dict[str, Any]]] = {}
    for e in evidence:
        cluster = e.get("source_cluster", "other")
        evidence_by_cluster.setdefault(cluster, []).append(e)

    latest_metric = metrics[-1] if metrics else {}

    return StoryDetailView(
        story_id=story.story_id,
        title=story.title,
        summary_ru=story.summary_ru,
        theme_labels=story.theme_ids,
        why_it_matters=latest_metric.get("why_it_matters", ""),
        timeline=metrics,
        score_breakdown={
            "goal_relevance": latest_metric.get("goal_relevance", 0),
            "cross_source_coverage": latest_metric.get("cross_source_coverage", 0),
            "momentum": latest_metric.get("momentum", 0),
            "novelty": latest_metric.get("novelty", 0),
            "evidence_quality": latest_metric.get("evidence_quality", 0),
        },
        evidence_by_cluster=evidence_by_cluster,
        research_state=research_state,
    )
