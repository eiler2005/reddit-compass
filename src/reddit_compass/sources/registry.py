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
    # `verge`, а не `theverge`: RSS-адаптер пишет в items именно это имя, и реестр
    # обязан совпадать с фактическим провайдером. Пока здесь стояло `theverge`,
    # `get_source("verge")` возвращал None, а `get_provider_label("verge")` молча падал
    # в `.title()` и давал «Verge» вместо «The Verge». Ошибка тихая: реестр выглядел
    # полным, но реальное издание в нём отсутствовало.
    "verge": SourceDefinition(
        source_id="verge",
        provider="verge",
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


# Профили, у которых полнота корпуса — часть контракта публикации. Для них релиз
# обязан покрывать все кластеры, а не только тот, что успел собраться.
PRODUCTION_PROFILES = frozenset({"broad", "ai-native"})


def expected_clusters_for_profile(profile: str) -> frozenset[SourceCluster]:
    """Кластеры, обязанные дать хотя бы один item в релизе прод-профиля.

    Обязательность не изобретается здесь заново, а читается из реестра: кластер
    обязателен, если в нём есть включённый источник с ``expected_min_items > 0``.
    Сегодня это ``voices`` (reddit) и ``developers`` (hackernews) — то есть ровно те
    источники, для которых владелец уже объявил минимальный ожидаемый объём.
    Ужесточение — правка одного поля в реестре, а не этой функции.

    Требовать *все* кластеры нельзя: тогда упавший на одну ночь ProductHunt блокировал
    бы публикацию целиком, и гейт начали бы обходить.

    Зачем вообще: 2026-07-31 и 2026-08-01 дали три прод-run'а со статусом ``complete``,
    в которых выжил только ``reddit`` (1 provider против 11 у здорового прогона).
    Пропавший источник не оставляет health-строки, поэтому проверка «нет ли плохих
    строк» его не видит. Одно-провайдерный корпус делает ``stories_cross_source_per_1k``
    структурно нулевым — метрика считает истории с ``source_count > 1``, которых там
    быть не может, — и релиз падал на полах качества, на два слоя ниже причины.
    """
    if profile not in PRODUCTION_PROFILES:
        return frozenset()
    return frozenset(
        source.cluster
        for source in SOURCES.values()
        if source.enabled_by_default and source.expected_min_items > 0
    )
