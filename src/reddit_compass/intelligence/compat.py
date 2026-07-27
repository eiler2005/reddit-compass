"""Compatibility adapter: legacy PostCard → ContentItem.

Преобразует данные из legacy JSONL-файлов в source-agnostic ContentItem.
Определяет provider по имени файла, а не по полю subreddit.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from ..models import PostCard
from .models import ContentItem, ContentScope, SourceCluster

logger = logging.getLogger("reddit_compass")

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "yclid",
    "ref",
    "ref_src",
    "ref_url",
}

_LEGACY_FILE_MAP: dict[str, tuple[str, SourceCluster]] = {
    "posts.jsonl": ("reddit", "voices"),
    "keyword-search.jsonl": ("reddit", "voices"),
    "hackernews.jsonl": ("hackernews", "developers"),
    "producthunt.jsonl": ("producthunt", "product_pulse"),
}

_RSS_LADDER_CLUSTER_MAP: dict[str, SourceCluster] = {
    "bbc": "mainstream",
    "guardian": "mainstream",
    "reuters": "business",
    "techcrunch": "tech_culture",
    "theverge": "tech_culture",
    "arstechnica": "tech_culture",
    "nytimes": "mainstream",
    "washingtonpost": "mainstream",
    "time": "mainstream",
    "usatoday": "mainstream",
    "ft": "business",
    "americanbanker": "business",
    "foxbusiness": "business",
    "wired": "tech_culture",
    "newyorker": "tech_culture",
    "vanityfair": "tech_culture",
    "medium": "voices",
    "foxnews": "mainstream",
}


def canonicalize_url(url: str) -> str:
    """Очищает URL от tracking parameters и fragments."""
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    query_params = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {k: v for k, v in query_params.items() if k.lower() not in _TRACKING_PARAMS}
    clean_query = urlencode(filtered, doseq=True) if filtered else ""
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/") if parsed.path != "/" else parsed.path,
            parsed.params,
            clean_query,
            "",
        )
    )


def _external_id_from_url(url: str) -> str:
    """Генерирует стабильный external_id из canonical URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def _reddit_permalink_to_url(permalink: str) -> str:
    """Преобразует Reddit permalink в полный URL."""
    if permalink.startswith("http"):
        return permalink
    return f"https://www.reddit.com{permalink}"


def _cluster_for_rss_ladder(subreddit: str) -> SourceCluster:
    """Определяет cluster для RSS/Ladder по имени источника (поле subreddit)."""
    key = subreddit.lower().replace(" ", "").replace("-", "").replace("_", "")
    return _RSS_LADDER_CLUSTER_MAP.get(key, "mainstream")


def _provider_for_rss_ladder(subreddit: str) -> str:
    """Определяет provider для RSS/Ladder по имени источника."""
    key = subreddit.lower().replace(" ", "").replace("-", "").replace("_", "")
    known = set(_RSS_LADDER_CLUSTER_MAP.keys())
    if key in known:
        return key
    return subreddit.lower()


def postcard_to_content_item(
    card: PostCard,
    legacy_file: str,
    observed_at: str,
) -> ContentItem:
    """Преобразует PostCard в ContentItem.

    Args:
        card: Legacy PostCard из JSONL.
        legacy_file: Имя файла-источника (posts.jsonl, hackernews.jsonl, etc.).
        observed_at: UTC ISO-8601 timestamp наблюдения.
    """
    if legacy_file in _LEGACY_FILE_MAP:
        provider, cluster = _LEGACY_FILE_MAP[legacy_file]
    elif legacy_file in ("rss.jsonl", "ladder.jsonl"):
        provider = _provider_for_rss_ladder(card.subreddit)
        cluster = _cluster_for_rss_ladder(card.subreddit)
    else:
        provider = "unknown"
        cluster = "mainstream"

    if provider == "reddit":
        canonical_url = canonicalize_url(_reddit_permalink_to_url(card.permalink))
        external_id = card.post_id
    else:
        canonical_url = canonicalize_url(card.url)
        external_id = card.post_id if card.post_id else _external_id_from_url(canonical_url)

    item_id = f"{provider}:{external_id}"

    raw_engagement: dict[str, float] = {}
    if provider == "reddit":
        raw_engagement = {
            "score": float(card.score),
            "comments": float(card.num_comments),
            "upvote_ratio": card.upvote_ratio,
        }
    elif provider == "hackernews":
        raw_engagement = {
            "points": float(card.score),
            "comments": float(card.num_comments),
        }
    elif provider == "producthunt":
        raw_engagement = {
            "votes": float(card.score),
            "comments": float(card.num_comments),
        }

    excerpt = ""
    content_scope: ContentScope = "headline"
    if provider == "reddit" and card.selftext:
        excerpt = card.selftext[:5000]
        content_scope = "excerpt"
    elif legacy_file in ("rss.jsonl", "ladder.jsonl"):
        if card.selftext:
            excerpt = card.selftext[:2000]
            content_scope = "abstract"
        else:
            content_scope = "headline"
    elif card.selftext:
        excerpt = card.selftext[:2000]
        content_scope = "abstract"

    return ContentItem(
        item_id=item_id,
        provider=provider,
        source_cluster=cluster,
        external_id=external_id,
        canonical_url=canonical_url,
        title=card.title,
        excerpt=excerpt,
        author=card.author,
        published_at=card.created_utc,
        observed_at=observed_at,
        snapshot_date=card.snapshot_date,
        content_scope=content_scope,
        source_section=card.subreddit,
        raw_engagement=raw_engagement,
        metadata={
            "monitoring_type": card.monitoring_type,
            "keyword": card.keyword,
            "is_self": card.is_self,
            "link_flair_text": card.link_flair_text,
        },
    )


def load_legacy_jsonl(
    path: Path,
    legacy_file: str,
    observed_at: str,
) -> tuple[list[ContentItem], int]:
    """Загружает legacy JSONL, преобразуя в ContentItem.

    Пропускает битые строки с диагностикой.

    Returns:
        Tuple of (items, skipped_count).
    """
    items: list[ContentItem] = []
    skipped = 0

    if not path.exists():
        return items, skipped

    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw: dict[str, Any] = json.loads(line)
                card = PostCard.from_dict(raw)
                item = postcard_to_content_item(card, legacy_file, observed_at)
                items.append(item)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                skipped += 1
                logger.warning("Skipping broken line %d in %s: %s", line_no, path.name, exc)

    return items, skipped
