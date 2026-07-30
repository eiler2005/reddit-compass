"""Source-agnostic domain models для intelligence layer.

ContentItem — нормализованная единица контента из любого источника.
Observation — метрика наблюдения в конкретном run.
Story — кластер связанных материалов.
Briefing — итоговый редакционный синтез.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SourceCluster = Literal[
    "voices",
    "developers",
    "mainstream",
    "business",
    "tech_culture",
    "product_pulse",
    "search_interest",
]

ContentScope = Literal["headline", "abstract", "excerpt", "full"]

StoryDirection = Literal["new", "growing", "stable", "fading", "resurfacing"]

ConfidenceLevel = Literal["low", "medium", "high"]

ResearchStatus = Literal["unread", "read", "in_progress", "archived", "dismissed"]
SourceStatus = Literal["ok", "empty", "degraded", "partial", "error", "not_configured", "skipped"]


@dataclass(frozen=True)
class ContentItem:
    """Нормализованная единица контента из любого источника."""

    item_id: str
    provider: str
    source_cluster: SourceCluster
    external_id: str
    canonical_url: str
    title: str
    summary_ru: str = ""
    excerpt: str = ""
    author: str = ""
    published_at: str | None = None
    observed_at: str = ""
    snapshot_date: str = ""
    language: str = "en"
    content_scope: ContentScope = "headline"
    source_section: str = ""
    domain_ids: list[str] = field(default_factory=lambda: ["other"])
    discussion_url: str = ""
    target_url: str = ""
    dedupe_group_id: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    raw_engagement: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """Метрика наблюдения item в конкретном run."""

    run_id: str
    item_id: str
    observed_at: str
    source_rank: int | None = None
    engagement_percentile: float = 0.0
    score_delta: float | None = None
    comments_delta: float | None = None


@dataclass(frozen=True)
class ItemSignal:
    """LLM-анализ отдельного item."""

    item_id: str
    domain_ids: list[str] = field(default_factory=list)
    theme_ids: list[str] = field(default_factory=list)
    candidate_themes: list[str] = field(default_factory=list)
    pain_points: list[str] = field(default_factory=list)
    buying_intent: bool = False
    goal_relevance: dict[str, int] = field(default_factory=dict)
    summary_ru: str = ""
    evidence_scope: ContentScope = "headline"
    model: str = ""
    analyzed_at: str = ""


@dataclass(frozen=True)
class EvidenceRef:
    """Ссылка на конкретный item как evidence."""

    item_id: str
    provider: str
    source_cluster: SourceCluster
    url: str
    title: str
    excerpt: str = ""
    content_scope: ContentScope = "headline"


@dataclass(frozen=True)
class Story:
    """Кластер связанных материалов (один сюжет)."""

    story_id: str
    canonical_key: str
    title: str
    summary_ru: str = ""
    domain_ids: list[str] = field(default_factory=lambda: ["other"])
    theme_ids: list[str] = field(default_factory=list)
    trend_id: str = ""
    lifecycle: StoryDirection = "new"
    project_scores: dict[str, int] = field(default_factory=dict)
    first_seen: str = ""
    last_seen: str = ""
    item_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StoryMetric:
    """Расчётные метрики story в конкретном run."""

    run_id: str
    story_id: str
    goal_relevance: float = 0.0
    cross_source_coverage: float = 0.0
    momentum: float = 0.0
    novelty: float = 0.0
    evidence_quality: float = 0.0
    trend_score: float = 0.0
    confidence: ConfidenceLevel = "low"
    direction: StoryDirection = "new"
    trend_id: str = ""
    lifecycle: StoryDirection = "new"
    project_scores: dict[str, int] = field(default_factory=dict)
    item_count: int = 0
    source_count: int = 0


@dataclass(frozen=True)
class BriefingStory:
    """Story с метриками и evidence для briefing."""

    story: Story
    metric: StoryMetric
    why_it_matters: str = ""
    evidence: list[EvidenceRef] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class GroundedText:
    """Текст с привязкой к evidence."""

    text: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SourceHealth:
    """Статус источника в run."""

    source_id: str
    provider: str
    cluster: SourceCluster
    status: SourceStatus
    count: int = 0
    duration_sec: float = 0.0
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True)
class Briefing:
    """Итоговый редакционный синтез run."""

    schema_version: int
    run_id: str
    date: str
    profile: str
    status: Literal["complete", "partial"]
    generated_at: str
    source_health: list[SourceHealth] = field(default_factory=list)
    top_changes: list[BriefingStory] = field(default_factory=list)
    mega_stories: list[BriefingStory] = field(default_factory=list)
    watchlist: list[BriefingStory] = field(default_factory=list)
    pain_points: list[GroundedText] = field(default_factory=list)
    column_ideas: list[GroundedText] = field(default_factory=list)
    narrative_shifts: list[GroundedText] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchState:
    """Пользовательское состояние исследования story."""

    story_id: str
    saved: bool = False
    status: ResearchStatus = "unread"
    note: str = ""
    updated_at: str = ""
