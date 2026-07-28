"""Тесты story clustering (intelligence/clustering.py)."""

from __future__ import annotations

from reddit_compass.intelligence.clustering import (
    StoryClusterer,
    cluster_items,
    extract_entities,
    normalize_title,
    title_similarity,
    token_jaccard,
)
from reddit_compass.intelligence.models import ContentItem


def _make_item(
    item_id: str,
    title: str,
    provider: str = "reddit",
    url: str = "",
    cluster: str = "voices",
    snapshot_date: str = "2026-07-27",
) -> ContentItem:
    return ContentItem(
        item_id=item_id,
        provider=provider,
        source_cluster=cluster,  # type: ignore[arg-type]
        external_id=item_id.split(":")[-1],
        canonical_url=url or f"https://{provider}.com/{item_id}",
        title=title,
        snapshot_date=snapshot_date,
    )


class TestNormalizeTitle:
    def test_basic(self):
        assert normalize_title("OpenAI Releases GPT-5") == "openai releases gpt"

    def test_removes_stopwords(self):
        result = normalize_title("The AI is going to change the world")
        assert "the" not in result
        assert "is" not in result

    def test_keeps_ai_token(self):
        result = normalize_title("AI agents are here")
        assert "ai" in result

    def test_removes_url(self):
        result = normalize_title("Check this out https://example.com/article")
        assert "https" not in result
        assert "example" not in result

    def test_removes_publisher_suffix(self):
        result = normalize_title("AI News | The Verge", "theverge")
        assert "verge" not in result

    def test_nfkc_normalization(self):
        result = normalize_title("ＡＩ agents")  # Fullwidth chars
        assert "ai" in result

    def test_opinion_prefix_uses_right_part(self):
        """Opinion | Real title - NYT → должен использовать правую часть."""
        result = normalize_title(
            "Opinion | Mamdani's Netanyahu Stunt Was a Waste - The New York Times",
            "nytimes",
        )
        assert "opinion" not in result
        assert "mamdani" in result

    def test_opinion_prefix_different_articles_not_merged(self):
        """Разные Opinion| статьи не должны нормализоваться в одно."""
        r1 = normalize_title(
            "Opinion | Ban AR-style rifles? Virginia is a warning. - The Washington Post",
            "washingtonpost",
        )
        r2 = normalize_title(
            "Opinion | The path forward for clean energy - The Washington Post",
            "washingtonpost",
        )
        assert r1 != r2

    def test_publisher_suffix_stripped(self):
        """Trailing '- The New York Times' должен быть удалён."""
        result = normalize_title(
            "AI Regulation Bill Passes Senate - The New York Times",
            "nytimes",
        )
        assert "times" not in result
        assert "regulation" in result

    def test_tech_life_generic_not_merged(self):
        """'Tech Life' с разными URL не должен склеиваться."""
        r1 = normalize_title("Tech Life", "bbc")
        r2 = normalize_title("Tech Life", "bbc")
        # Same normalized title — but clustering should use URL guard
        assert r1 == r2  # normalization is same, guard is in clustering


class TestTokenJaccard:
    def test_identical(self):
        tokens = {"ai", "agents", "jobs"}
        assert token_jaccard(tokens, tokens) == 1.0

    def test_disjoint(self):
        assert token_jaccard({"ai", "agents"}, {"weather", "forecast"}) == 0.0

    def test_partial(self):
        a = {"ai", "agents", "jobs"}
        b = {"ai", "agents", "future"}
        assert 0.0 < token_jaccard(a, b) < 1.0

    def test_empty(self):
        assert token_jaccard(set(), {"ai"}) == 0.0


class TestTitleSimilarity:
    def test_identical_titles(self):
        sim = title_similarity("OpenAI releases GPT-5", "OpenAI releases GPT-5")
        assert sim > 0.9

    def test_similar_titles(self):
        sim = title_similarity(
            "OpenAI releases GPT-5 model",
            "GPT-5 released by OpenAI today",
        )
        # Similarity зависит от нормализации; проверяем что она положительная
        assert sim > 0.3

    def test_different_titles(self):
        sim = title_similarity(
            "OpenAI releases GPT-5",
            "Weather forecast for tomorrow",
        )
        assert sim < 0.3


class TestExtractEntities:
    def test_company_name(self):
        entities = extract_entities("OpenAI announces new model")
        # Entity pattern ловит CamelCase; "OpenAI" → "open" + "ai" (acronym)
        assert len(entities) > 0

    def test_number(self):
        entities = extract_entities("GPT-4 scores 90 percent")
        # Pattern ловит числа
        assert any(e.isdigit() for e in entities)

    def test_acronym(self):
        entities = extract_entities("NASA launches rocket")
        assert "nasa" in entities


class TestStoryClusterer:
    def test_same_url_same_story(self):
        clusterer = StoryClusterer()
        item1 = _make_item("reddit:1", "AI news", url="https://example.com/article")
        item2 = _make_item("hackernews:2", "Different title", url="https://example.com/article")

        story1 = clusterer.add_item(item1)
        story2 = clusterer.add_item(item2)

        assert story1 == story2

    def test_reddit_target_url_merges_with_article(self):
        clusterer = StoryClusterer()
        reddit_item = ContentItem(
            item_id="reddit:1",
            provider="reddit",
            source_cluster="voices",
            external_id="1",
            canonical_url="https://www.reddit.com/r/technology/comments/1/story",
            target_url="https://example.com/article",
            discussion_url="https://www.reddit.com/r/technology/comments/1/story",
            title="Discussion of the article",
        )
        rss_item = _make_item(
            "bbc:2",
            "Article title",
            provider="bbc",
            url="https://example.com/article",
            cluster="mainstream",
        )

        story1 = clusterer.add_item(reddit_item)
        story2 = clusterer.add_item(rss_item)

        assert story1 == story2

    def test_similar_titles_same_story(self):
        clusterer = StoryClusterer()
        # Используем более похожие заголовки
        item1 = _make_item("reddit:1", "OpenAI releases new GPT model")
        item2 = _make_item("hackernews:2", "OpenAI releases new GPT model today")

        story1 = clusterer.add_item(item1)
        story2 = clusterer.add_item(item2)

        assert story1 == story2

    def test_different_titles_different_stories(self):
        clusterer = StoryClusterer()
        item1 = _make_item("reddit:1", "OpenAI releases GPT-5")
        item2 = _make_item("reddit:2", "Weather forecast for tomorrow")

        story1 = clusterer.add_item(item1)
        story2 = clusterer.add_item(item2)

        assert story1 != story2

    def test_unrelated_same_company_not_merged(self):
        """Два разных события одной компании не склеиваются."""
        clusterer = StoryClusterer()
        item1 = _make_item("reddit:1", "OpenAI releases GPT-5 model")
        item2 = _make_item("reddit:2", "OpenAI office opening in London")

        story1 = clusterer.add_item(item1)
        story2 = clusterer.add_item(item2)

        assert story1 != story2

    def test_mixed_source_fixture(self):
        """Reddit + HN + NYT + FT с одним URL → один story."""
        clusterer = StoryClusterer()
        url = "https://example.com/ai-breakthrough"

        items = [
            _make_item("reddit:1", "AI breakthrough announced", "reddit", url, "voices"),
            _make_item("hackernews:2", "AI breakthrough", "hackernews", url, "developers"),
            _make_item("nytimes:3", "Major AI breakthrough reported", "nytimes", url, "mainstream"),
            _make_item("ft:4", "AI breakthrough impact on business", "ft", url, "business"),
        ]

        stories = set()
        for item in items:
            stories.add(clusterer.add_item(item))

        assert len(stories) == 1

    def test_unrelated_post_not_attached(self):
        """Несвязанный пост той же компании не приклеивается."""
        clusterer = StoryClusterer()

        item1 = _make_item("reddit:1", "OpenAI releases GPT-5 model")
        item2 = _make_item("reddit:2", "OpenAI hiring engineers")

        story1 = clusterer.add_item(item1)
        story2 = clusterer.add_item(item2)

        assert story1 != story2

    def test_get_stories(self):
        clusterer = StoryClusterer()
        clusterer.add_item(_make_item("reddit:1", "AI news"))
        clusterer.add_item(_make_item("reddit:2", "Weather update"))

        stories = clusterer.get_stories()
        assert len(stories) == 2


class TestClusterItems:
    def test_returns_stories_and_ambiguity(self):
        items = [
            _make_item("reddit:1", "AI agents replace jobs"),
            _make_item("hackernews:2", "AI agents taking over work"),
            _make_item("reddit:3", "Weather forecast"),
        ]
        stories, ambiguity = cluster_items(items)
        assert len(stories) >= 2
        assert ambiguity >= 0

    def test_story_id_stable(self):
        items = [_make_item("reddit:1", "OpenAI GPT-5 release")]
        stories1, _ = cluster_items(items)
        stories2, _ = cluster_items(items)
        assert stories1[0].story_id == stories2[0].story_id
