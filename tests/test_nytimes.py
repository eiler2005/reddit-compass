"""Тесты NYT adapter (sources/nytimes.py)."""

from __future__ import annotations

from unittest.mock import patch

from reddit_compass.sources.nytimes import (
    _article_to_card,
    _doc_to_card,
    _is_configured,
)


class TestIsConfigured:
    def test_not_configured(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _is_configured() is False

    def test_configured(self):
        with patch.dict("os.environ", {"NYT_API_KEY": "test-key"}):  # pragma: allowlist secret
            assert _is_configured() is True


class TestArticleToCard:
    def test_valid_article(self):
        article = {
            "url": "https://nytimes.com/2026/07/27/tech/ai.html",
            "title": "AI Breakthrough Announced",
            "abstract": "Scientists announce major AI breakthrough.",
            "byline": "By John Doe",
            "published_date": "2026-07-27T10:00:00Z",
        }
        card = _article_to_card(article, "technology", "2026-07-27")

        assert card is not None
        assert card.subreddit == "nytimes"
        assert card.title == "AI Breakthrough Announced"
        assert card.url == "https://nytimes.com/2026/07/27/tech/ai.html"
        assert card.selftext == "Scientists announce major AI breakthrough."
        assert card.author == "By John Doe"
        assert card.score == 0
        assert card.monitoring_type == "api"

    def test_missing_url(self):
        article = {
            "title": "Test",
            "abstract": "Test abstract",
        }
        card = _article_to_card(article, "technology", "2026-07-27")
        assert card is None

    def test_missing_title(self):
        article = {
            "url": "https://nytimes.com/article",
            "abstract": "Test abstract",
        }
        card = _article_to_card(article, "technology", "2026-07-27")
        assert card is None


class TestDocToCard:
    def test_valid_doc(self):
        doc = {
            "web_url": "https://nytimes.com/2026/07/27/science/research.html",
            "headline": {
                "main": "New Research Findings",
                "kicker": "Science",
            },
            "abstract": "Researchers discover new phenomenon.",
            "byline": {"original": "By Jane Smith"},
            "pub_date": "2026-07-27T08:00:00Z",
        }
        card = _doc_to_card(doc, "2026-07-27")

        assert card is not None
        assert card.subreddit == "nytimes"
        assert card.title == "New Research Findings"
        assert card.url == "https://nytimes.com/2026/07/27/science/research.html"
        assert card.author == "By Jane Smith"

    def test_missing_web_url(self):
        doc = {
            "headline": {"main": "Test"},
        }
        card = _doc_to_card(doc, "2026-07-27")
        assert card is None

    def test_missing_headline_main(self):
        doc = {
            "web_url": "https://nytimes.com/article",
            "headline": {},
        }
        card = _doc_to_card(doc, "2026-07-27")
        assert card is None


class TestContentScope:
    def test_nyt_content_scope_abstract(self):
        """NYT articles имеют content_scope=abstract."""
        article = {
            "url": "https://nytimes.com/article",
            "title": "Test Article",
            "abstract": "This is an abstract.",
        }
        card = _article_to_card(article, "technology", "2026-07-27")

        assert card is not None
        # selftext содержит abstract
        assert card.selftext == "This is an abstract."
