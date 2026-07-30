"""Тесты source registry (sources/registry.py)."""

from __future__ import annotations

import pytest

from reddit_compass.sources.registry import (
    SOURCES,
    get_enabled_sources,
    get_provider_label,
    get_source,
    get_sources_by_cluster,
)


class TestSourceDefinition:
    def test_frozen(self):
        source = get_source("reddit")
        assert source is not None
        with pytest.raises(AttributeError):
            source.label = "new"  # type: ignore[misc]


class TestGetSource:
    def test_known_source(self):
        source = get_source("reddit")
        assert source is not None
        assert source.provider == "reddit"
        assert source.cluster == "voices"

    def test_unknown_source(self):
        assert get_source("unknown") is None

    def test_all_sources_have_required_fields(self):
        for source_id, source in SOURCES.items():
            assert source.source_id == source_id
            assert source.provider
            assert source.label
            assert source.cluster
            assert source.access


class TestGetSourcesByCluster:
    def test_voices_cluster(self):
        sources = get_sources_by_cluster("voices")
        assert len(sources) >= 1
        assert all(s.cluster == "voices" for s in sources)

    def test_mainstream_cluster(self):
        sources = get_sources_by_cluster("mainstream")
        assert len(sources) >= 3
        providers = {s.provider for s in sources}
        assert "bbc" in providers
        assert "guardian" in providers


class TestGetEnabledSources:
    def test_returns_enabled(self):
        enabled = get_enabled_sources()
        assert len(enabled) > 0
        assert all(s.enabled_by_default for s in enabled)

    def test_nyt_api_not_enabled_by_default(self):
        enabled_ids = {s.source_id for s in get_enabled_sources()}
        assert "nytimes_api" not in enabled_ids
        assert "wsj" not in enabled_ids


class TestGetProviderLabel:
    def test_known_provider(self):
        assert get_provider_label("reddit") == "Reddit"
        assert get_provider_label("bbc") == "BBC News"

    def test_unknown_provider(self):
        assert get_provider_label("unknown") == "Unknown"


class TestRegistryCompleteness:
    def test_reddit_sources(self):
        assert "reddit" in SOURCES
        assert SOURCES["reddit"].expected_min_items >= 1

    def test_hackernews(self):
        assert "hackernews" in SOURCES
        assert SOURCES["hackernews"].expected_min_items >= 1

    def test_rss_sources(self):
        rss_sources = ["bbc", "guardian", "reuters", "techcrunch", "theverge", "arstechnica"]
        for source_id in rss_sources:
            assert source_id in SOURCES, f"Missing RSS source: {source_id}"
            assert SOURCES[source_id].access == "rss"

    def test_ladder_sources(self):
        ladder_sources = [
            "nytimes",
            "washingtonpost",
            "time",
            "usatoday",
            "ft",
            "americanbanker",
            "foxbusiness",
            "wired",
            "newyorker",
            "vanityfair",
            "medium",
            "foxnews",
        ]
        for source_id in ladder_sources:
            assert source_id in SOURCES, f"Missing Ladder source: {source_id}"
            assert SOURCES[source_id].access == "ladder"

    def test_producthunt(self):
        assert "producthunt" in SOURCES
        assert SOURCES["producthunt"].access == "api"

    def test_nyt_api_requires_key(self):
        source = SOURCES["nytimes_api"]
        assert "NYT_API_KEY" in source.requires_env
        assert not source.enabled_by_default

    def test_wsj_not_configured(self):
        source = SOURCES["wsj"]
        assert not source.enabled_by_default
        assert len(source.requires_env) > 0
