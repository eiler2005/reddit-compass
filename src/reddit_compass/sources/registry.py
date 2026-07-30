"""Source registry: определение всех поддерживаемых источников.

SourceDefinition содержит метаданные источника: provider, cluster,
access method, required env vars, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..intelligence.models import ContentScope, SourceCluster


@dataclass(frozen=True)
class SourceDefinition:
    """Определение источника."""

    source_id: str
    provider: str
    label: str
    cluster: SourceCluster
    access: str  # "reddit", "api", "rss", "ladder", "manual"
    country: str = "US"
    language: str = "en"
    default_scope: ContentScope = "headline"
    expected_freshness_hours: int = 24
    expected_min_items: int = 0
    requires_env: tuple[str, ...] = field(default_factory=tuple)
    enabled_by_default: bool = True


# Registry всех поддерживаемых источников
SOURCES: dict[str, SourceDefinition] = {
    # Reddit
    "reddit": SourceDefinition(
        source_id="reddit",
        provider="reddit",
        label="Reddit",
        cluster="voices",
        access="reddit",
        default_scope="excerpt",
        expected_min_items=1,
    ),
    # Hacker News
    "hackernews": SourceDefinition(
        source_id="hackernews",
        provider="hackernews",
        label="Hacker News",
        cluster="developers",
        access="api",
        default_scope="abstract",
        expected_min_items=1,
    ),
    # RSS feeds
    "bbc": SourceDefinition(
        source_id="bbc",
        provider="bbc",
        label="BBC News",
        cluster="mainstream",
        access="rss",
        country="UK",
    ),
    "guardian": SourceDefinition(
        source_id="guardian",
        provider="guardian",
        label="The Guardian",
        cluster="mainstream",
        access="rss",
        country="UK",
    ),
    "reuters": SourceDefinition(
        source_id="reuters",
        provider="reuters",
        label="Reuters",
        cluster="business",
        access="rss",
    ),
    "techcrunch": SourceDefinition(
        source_id="techcrunch",
        provider="techcrunch",
        label="TechCrunch",
        cluster="tech_culture",
        access="rss",
    ),
    "theverge": SourceDefinition(
        source_id="theverge",
        provider="theverge",
        label="The Verge",
        cluster="tech_culture",
        access="rss",
    ),
    "arstechnica": SourceDefinition(
        source_id="arstechnica",
        provider="arstechnica",
        label="Ars Technica",
        cluster="tech_culture",
        access="rss",
    ),
    # Ladder sources
    "nytimes": SourceDefinition(
        source_id="nytimes",
        provider="nytimes",
        label="The New York Times",
        cluster="mainstream",
        access="ladder",
        default_scope="abstract",
    ),
    "washingtonpost": SourceDefinition(
        source_id="washingtonpost",
        provider="washingtonpost",
        label="The Washington Post",
        cluster="mainstream",
        access="ladder",
    ),
    "time": SourceDefinition(
        source_id="time",
        provider="time",
        label="TIME",
        cluster="mainstream",
        access="ladder",
    ),
    "usatoday": SourceDefinition(
        source_id="usatoday",
        provider="usatoday",
        label="USA Today",
        cluster="mainstream",
        access="ladder",
    ),
    "ft": SourceDefinition(
        source_id="ft",
        provider="ft",
        label="Financial Times",
        cluster="business",
        access="ladder",
        country="UK",
    ),
    "americanbanker": SourceDefinition(
        source_id="americanbanker",
        provider="americanbanker",
        label="American Banker",
        cluster="business",
        access="ladder",
    ),
    "foxbusiness": SourceDefinition(
        source_id="foxbusiness",
        provider="foxbusiness",
        label="Fox Business",
        cluster="business",
        access="ladder",
    ),
    "wired": SourceDefinition(
        source_id="wired",
        provider="wired",
        label="Wired",
        cluster="tech_culture",
        access="ladder",
        default_scope="abstract",
    ),
    "newyorker": SourceDefinition(
        source_id="newyorker",
        provider="newyorker",
        label="The New Yorker",
        cluster="tech_culture",
        access="ladder",
    ),
    "vanityfair": SourceDefinition(
        source_id="vanityfair",
        provider="vanityfair",
        label="Vanity Fair",
        cluster="tech_culture",
        access="ladder",
    ),
    "medium": SourceDefinition(
        source_id="medium",
        provider="medium",
        label="Medium",
        cluster="voices",
        access="ladder",
    ),
    "foxnews": SourceDefinition(
        source_id="foxnews",
        provider="foxnews",
        label="Fox News",
        cluster="mainstream",
        access="ladder",
    ),
    # ProductHunt
    "producthunt": SourceDefinition(
        source_id="producthunt",
        provider="producthunt",
        label="Product Hunt",
        cluster="product_pulse",
        access="api",
        default_scope="abstract",
    ),
    # NYT API (official)
    "nytimes_api": SourceDefinition(
        source_id="nytimes_api",
        provider="nytimes",
        label="NYT API",
        cluster="mainstream",
        access="api",
        default_scope="abstract",
        requires_env=("NYT_API_KEY",),
        enabled_by_default=False,
    ),
    # WSJ (not configured)
    "wsj": SourceDefinition(
        source_id="wsj",
        provider="wsj",
        label="The Wall Street Journal",
        cluster="business",
        access="api",
        requires_env=("WSJ_API_KEY",),
        enabled_by_default=False,
    ),
}


def get_source(source_id: str) -> SourceDefinition | None:
    """Возвращает SourceDefinition по ID."""
    return SOURCES.get(source_id)


def get_sources_by_cluster(cluster: SourceCluster) -> list[SourceDefinition]:
    """Возвращает все источники кластера."""
    return [s for s in SOURCES.values() if s.cluster == cluster]


def get_enabled_sources() -> list[SourceDefinition]:
    """Возвращает источники, включённые по умолчанию."""
    return [s for s in SOURCES.values() if s.enabled_by_default]


def get_provider_label(provider: str) -> str:
    """Возвращает label для provider."""
    for source in SOURCES.values():
        if source.provider == provider:
            return source.label
    return provider.title()
