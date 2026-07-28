"""Тесты compatibility adapter (intelligence/compat.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reddit_compass.intelligence.compat import (
    canonicalize_url,
    load_legacy_jsonl,
    postcard_to_content_item,
)
from reddit_compass.models import PostCard

OBSERVED = "2026-07-27T12:00:00Z"


def _make_card(**over: Any) -> PostCard:
    base: dict[str, Any] = {
        "subreddit": "artificial",
        "post_id": "p1",
        "title": "Test post",
        "author": "tester",
        "created_utc": "2026-07-27T10:00:00Z",
        "score": 100,
        "upvote_ratio": 0.95,
        "num_comments": 42,
        "url": "https://example.com/article",
        "selftext": "",
        "link_flair_text": None,
        "is_self": False,
        "permalink": "/r/artificial/comments/p1/test/",
        "monitoring_type": "hot",
        "snapshot_date": "2026-07-27",
    }
    base.update(over)
    return PostCard(**base)


class TestCanonicalizeUrl:
    def test_strips_utm_params(self):
        url = "https://example.com/article?utm_source=twitter&utm_medium=social&id=123"
        assert canonicalize_url(url) == "https://example.com/article?id=123"

    def test_strips_fbclid_gclid(self):
        url = "https://example.com/page?fbclid=abc&gclid=def&real=1"
        assert canonicalize_url(url) == "https://example.com/page?real=1"

    def test_strips_fragment(self):
        url = "https://example.com/article#section-2"
        assert canonicalize_url(url) == "https://example.com/article"

    def test_strips_trailing_slash(self):
        url = "https://example.com/path/"
        assert canonicalize_url(url) == "https://example.com/path"

    def test_preserves_root_slash(self):
        url = "https://example.com/"
        assert canonicalize_url(url) == "https://example.com/"

    def test_empty_url(self):
        assert canonicalize_url("") == ""

    def test_non_http_scheme(self):
        assert canonicalize_url("ftp://example.com/file") == ""
        assert canonicalize_url("javascript:alert(1)") == ""

    def test_same_url_different_tracking(self):
        url1 = "https://example.com/a?utm_source=reddit&id=1"
        url2 = "https://example.com/a?utm_source=twitter&id=1"
        assert canonicalize_url(url1) == canonicalize_url(url2)

    def test_all_tracking_removed(self):
        url = "https://example.com/a?utm_source=x&utm_medium=y&utm_campaign=z"
        assert canonicalize_url(url) == "https://example.com/a"


class TestPostcardToContentItem:
    def test_reddit_posts_jsonl(self):
        card = _make_card(subreddit="artificial", post_id="abc123")
        item = postcard_to_content_item(card, "posts.jsonl", OBSERVED)
        assert item.provider == "reddit"
        assert item.source_cluster == "voices"
        assert item.item_id == "reddit:abc123"
        assert item.canonical_url == "https://example.com/article"
        assert item.target_url == "https://example.com/article"
        assert "reddit.com" in item.discussion_url
        assert item.raw_engagement["score"] == 100.0
        assert item.raw_engagement["comments"] == 42.0

    def test_reddit_keyword_search(self):
        card = _make_card(monitoring_type="search", keyword="AI agents")
        item = postcard_to_content_item(card, "keyword-search.jsonl", OBSERVED)
        assert item.provider == "reddit"
        assert item.source_cluster == "voices"
        assert item.metadata["keyword"] == "AI agents"

    def test_hackernews(self):
        card = _make_card(
            subreddit="hackernews",
            post_id="12345",
            url="https://news.ycombinator.com/item?id=12345",
            score=200,
            num_comments=80,
        )
        item = postcard_to_content_item(card, "hackernews.jsonl", OBSERVED)
        assert item.provider == "hackernews"
        assert item.source_cluster == "developers"
        assert item.item_id == "hackernews:12345"
        assert item.raw_engagement["points"] == 200.0
        assert item.raw_engagement["comments"] == 80.0

    def test_producthunt(self):
        card = _make_card(
            subreddit="producthunt",
            post_id="ph-001",
            url="https://producthunt.com/posts/test",
            score=50,
        )
        item = postcard_to_content_item(card, "producthunt.jsonl", OBSERVED)
        assert item.provider == "producthunt"
        assert item.source_cluster == "product_pulse"
        assert item.raw_engagement["votes"] == 50.0

    def test_rss_bbc(self):
        card = _make_card(
            subreddit="bbc",
            post_id="",
            url="https://bbc.com/news/article-123",
            score=0,
            num_comments=0,
        )
        item = postcard_to_content_item(card, "rss.jsonl", OBSERVED)
        assert item.provider == "bbc"
        assert item.source_cluster == "mainstream"
        assert item.item_id != ""
        assert item.raw_engagement == {}

    def test_rss_ladder_never_reddit(self):
        """RSS/Ladder никогда не маркируются как Reddit."""
        for source in ("bbc", "guardian", "reuters", "nytimes", "ft", "wired"):
            card = _make_card(subreddit=source, url=f"https://{source}.com/article")
            item = postcard_to_content_item(card, "rss.jsonl", OBSERVED)
            assert item.provider != "reddit", f"{source} should not be reddit"
            assert item.source_cluster != "voices" or source == "medium"

    def test_ladder_nytimes(self):
        card = _make_card(
            subreddit="nytimes",
            post_id="",
            url="https://nytimes.com/2026/07/27/tech/ai.html",
            score=0,
        )
        item = postcard_to_content_item(card, "ladder.jsonl", OBSERVED)
        assert item.provider == "nytimes"
        assert item.source_cluster == "mainstream"
        assert item.content_scope == "headline"

    def test_ladder_with_excerpt(self):
        card = _make_card(
            subreddit="wired",
            url="https://wired.com/story/ai",
            selftext="This is an article excerpt about AI.",
        )
        item = postcard_to_content_item(card, "ladder.jsonl", OBSERVED)
        assert item.content_scope == "abstract"
        assert item.excerpt == "This is an article excerpt about AI."

    def test_reddit_selftext_as_excerpt(self):
        card = _make_card(selftext="Long post text " * 100, is_self=True)
        item = postcard_to_content_item(card, "posts.jsonl", OBSERVED)
        assert item.content_scope == "excerpt"
        assert len(item.excerpt) <= 5000

    def test_reddit_no_comments_in_excerpt(self):
        """Selftext не включает comments."""
        card = _make_card(selftext="Post body only")
        item = postcard_to_content_item(card, "posts.jsonl", OBSERVED)
        assert "comment" not in item.excerpt.lower() or "comment" in "Post body only".lower()

    def test_external_id_from_url_when_missing(self):
        card = _make_card(post_id="", url="https://example.com/unique-article")
        item = postcard_to_content_item(card, "rss.jsonl", OBSERVED)
        assert item.external_id != ""
        assert len(item.external_id) == 24

    def test_canonical_url_strips_tracking(self):
        card = _make_card(url="https://example.com/a?utm_source=reddit&id=1")
        item = postcard_to_content_item(card, "rss.jsonl", OBSERVED)
        assert "utm_source" not in item.canonical_url
        assert "id=1" in item.canonical_url

    def test_reddit_permalink_to_full_url(self):
        card = _make_card(permalink="/r/test/comments/abc/post/")
        item = postcard_to_content_item(card, "posts.jsonl", OBSERVED)
        assert item.discussion_url.startswith("https://www.reddit.com/")

    def test_reddit_self_post_uses_discussion_as_canonical_url(self):
        card = _make_card(is_self=True, url="", permalink="/r/test/comments/abc/post/")
        item = postcard_to_content_item(card, "posts.jsonl", OBSERVED)
        assert item.canonical_url.startswith("https://www.reddit.com/")


class TestLoadLegacyJsonl:
    def test_loads_valid_file(self, tmp_path: Path):
        f = tmp_path / "posts.jsonl"
        cards = [_make_card(post_id=f"p{i}") for i in range(3)]
        f.write_text("\n".join(c.to_json() for c in cards), encoding="utf-8")

        items, skipped = load_legacy_jsonl(f, "posts.jsonl", OBSERVED)
        assert len(items) == 3
        assert skipped == 0

    def test_skips_broken_lines(self, tmp_path: Path):
        f = tmp_path / "posts.jsonl"
        valid = _make_card(post_id="good").to_json()
        f.write_text(f"{valid}\n{{broken json\n\n{valid}\n", encoding="utf-8")

        items, skipped = load_legacy_jsonl(f, "posts.jsonl", OBSERVED)
        assert len(items) == 2
        assert skipped == 1

    def test_missing_file(self, tmp_path: Path):
        items, skipped = load_legacy_jsonl(tmp_path / "nope.jsonl", "posts.jsonl", OBSERVED)
        assert items == []
        assert skipped == 0

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "posts.jsonl"
        f.write_text("", encoding="utf-8")
        items, skipped = load_legacy_jsonl(f, "posts.jsonl", OBSERVED)
        assert items == []
        assert skipped == 0

    def test_all_five_families(self, tmp_path: Path):
        """Все пять legacy source families преобразуются."""
        families = {
            "posts.jsonl": ("reddit", "voices"),
            "keyword-search.jsonl": ("reddit", "voices"),
            "hackernews.jsonl": ("hackernews", "developers"),
            "rss.jsonl": ("bbc", "mainstream"),
            "ladder.jsonl": ("nytimes", "mainstream"),
            "producthunt.jsonl": ("producthunt", "product_pulse"),
        }
        for filename, (expected_provider, expected_cluster) in families.items():
            sub = expected_provider if filename in ("rss.jsonl", "ladder.jsonl") else "artificial"
            url = (
                f"https://{expected_provider}.com/article"
                if filename != "posts.jsonl"
                else "https://reddit.com/x"
            )
            card = _make_card(subreddit=sub, url=url)
            f = tmp_path / filename
            f.write_text(card.to_json(), encoding="utf-8")
            items, _ = load_legacy_jsonl(f, filename, OBSERVED)
            assert len(items) == 1, f"{filename}: expected 1 item"
            assert items[0].provider == expected_provider, f"{filename}: provider"
            assert items[0].source_cluster == expected_cluster, f"{filename}: cluster"
