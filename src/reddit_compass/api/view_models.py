"""View models для Jinja2 UI.

Преобразует domain models в данные для шаблонов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..intelligence.models import ResearchState
from ..intelligence.taxonomy import DOMAIN_LABELS_RU


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
    # Derived honesty metrics
    candidate_story_count: int = 0
    single_item_story_count: int = 0
    multi_item_story_count: int = 0
    cross_source_story_count: int = 0
    radar_ready_story_count: int = 0
    analyzed_coverage_ratio: float = 0.0
    compression_ratio: float = 0.0


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
    status: Literal["ok", "empty", "partial", "error", "stale", "skipped", "not_configured"] = (
        "skipped"
    )
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
class DomainSummaryView:
    """Broad Radar domain summary."""

    domain_id: str
    label_ru: str
    item_count: int = 0
    story_count: int = 0
    source_count: int = 0
    top_score: float = 0.0
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
    domain_ids: list[str] = field(default_factory=list)
    domain_labels: list[str] = field(default_factory=list)
    clusters: list[str] = field(default_factory=list)
    clusters_display: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    research_state: ResearchState | None = None
    primary_evidence_url: str = ""
    primary_evidence_provider: str = ""
    primary_evidence_provider_label: str = ""


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


_DIRECTION_LABELS = {
    "new": "🆕 Новый",
    "growing": "📈 Растёт",
    "stable": "➡️ Стабильный",
    "fading": "📉 Затухает",
    "resurfacing": "🔄 Возвращается",
}

_CLUSTER_LABELS = {
    "voices": "🗣 Голоса",
    "developers": "💻 Разработчики",
    "mainstream": "📰 Мейнстрим",
    "business": "💰 Бизнес",
    "tech_culture": "🔬 Tech/Культура",
    "product_pulse": "🚀 Продукты",
    "search_interest": "📊 Поиск",
}

_PROVIDER_LABELS = {
    "reddit": "Reddit",
    "hackernews": "HN",
    "producthunt": "PH",
    "nytimes": "NYT",
    "washingtonpost": "WaPo",
    "wired": "Wired",
    "time": "Time",
    "vanityfair": "Vanity Fair",
    "newyorker": "New Yorker",
    "americanbanker": "Am. Banker",
    "foxnews": "Fox News",
    "ft": "FT",
    "bbc": "BBC",
    "guardian": "Guardian",
    "reuters": "Reuters",
    "techcrunch": "TechCrunch",
    "theverge": "Verge",
    "arstechnica": "Ars",
    "usatoday": "USA Today",
    "foxbusiness": "Fox Biz",
    "medium": "Medium",
}


def cluster_label(cluster: str) -> str:
    return _CLUSTER_LABELS.get(cluster, cluster)


def provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, provider)


def domain_label(domain_id: str) -> str:
    return DOMAIN_LABELS_RU.get(domain_id, domain_id)


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


@dataclass
class TrendStrengthView:
    """View model для строки силы трендов."""

    story_id: str
    title: str
    trend_score: float
    novelty: float
    coverage: float
    direction: str
    direction_label: str
    provider_count: int
    item_count: int


@dataclass
class RawItemView:
    """View model для raw item (популярное в каналах)."""

    item_id: str
    title: str
    provider: str
    source_cluster: str
    url: str
    score: int = 0
    comments: int = 0
