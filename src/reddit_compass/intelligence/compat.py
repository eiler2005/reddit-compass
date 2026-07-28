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
from .taxonomy import classify_domains, compute_project_scores, stable_hash_id

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
    "oc",
    "cmpid",
    "smid",
    "partner",
    "campaign_id",
    "source",
    "output",
}

# Publisher host normalization: mobile/AMP/alt → canonical
_HOST_ALIASES: dict[str, str] = {
    "m.nytimes.com": "www.nytimes.com",
    "mobile.nytimes.com": "www.nytimes.com",
    "nytimes.com": "www.nytimes.com",
    "amp.theguardian.com": "www.theguardian.com",
    "theguardian.com": "www.theguardian.com",
    "m.theguardian.com": "www.theguardian.com",
    "reuters.com": "www.reuters.com",
    "m.reuters.com": "www.reuters.com",
    "washingtonpost.com": "www.washingtonpost.com",
    "m.washingtonpost.com": "www.washingtonpost.com",
    "wapo.st": "www.washingtonpost.com",
    "bbc.com": "www.bbc.com",
    "m.bbc.com": "www.bbc.com",
    "bbc.co.uk": "www.bbc.co.uk",
    "m.bbc.co.uk": "www.bbc.co.uk",
    "ft.com": "www.ft.com",
    "m.ft.com": "www.ft.com",
    "techcrunch.com": "techcrunch.com",
    "m.techcrunch.com": "techcrunch.com",
    "theverge.com": "www.theverge.com",
    "m.theverge.com": "www.theverge.com",
    "arstechnica.com": "arstechnica.com",
    "m.arstechnica.com": "arstechnica.com",
    "wired.com": "www.wired.com",
    "m.wired.com": "www.wired.com",
    "www.wired.com": "www.wired.com",
    "usatoday.com": "www.usatoday.com",
    "m.usatoday.com": "www.usatoday.com",
    "foxbusiness.com": "www.foxbusiness.com",
    "m.foxbusiness.com": "www.foxbusiness.com",
    "americanbanker.com": "www.americanbanker.com",
    "medium.com": "medium.com",
    "m.medium.com": "medium.com",
    "time.com": "time.com",
    "m.time.com": "time.com",
    "vanityfair.com": "www.vanityfair.com",
    "m.vanityfair.com": "www.vanityfair.com",
    "newyorker.com": "www.newyorker.com",
    "m.newyorker.com": "www.newyorker.com",
    "foxnews.com": "www.foxnews.com",
    "m.foxnews.com": "www.foxnews.com",
}


def _normalize_host(host: str) -> str:
    """Нормализует host: lowercase + mobile/AMP → canonical."""
    host = host.lower().strip()
    return _HOST_ALIASES.get(host, host)


def _unwrap_google_news_url(url: str) -> str:
    """Пытается раскрыть Google News RSS URL в publisher URL.

    Google News URLs вида:
    https://news.google.com/rss/articles/CBMi...?oc=5

    Без сети нельзя надёжно раскрыть base64-encoded publisher URL.
    Возвращаем пустую строку если не можем раскрыть — caller использует title matching.
    """
    parsed = urlparse(url)
    if "news.google.com" not in parsed.netloc:
        return ""
    # Google News URLs не раскрываются без сети — возвращаем пустую строку
    # Clustering будет использовать title/entity matching вместо URL
    return ""


def canonicalize_url(url: str) -> str:
    """Очищает URL: tracking params, fragments, host normalization, scheme."""
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""

    # Normalize scheme to https
    scheme = "https"

    # Normalize host
    host = _normalize_host(parsed.netloc)

    # Try to unwrap Google News URLs
    if "news.google.com" in host:
        unwrapped = _unwrap_google_news_url(url)
        if unwrapped:
            return canonicalize_url(unwrapped)  # recursive normalize
        # Can't unwrap — return cleaned Google URL as-is
        return urlunparse((scheme, host, parsed.path, "", "", ""))

    # Filter tracking params
    query_params = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {k: v for k, v in query_params.items() if k.lower() not in _TRACKING_PARAMS}
    clean_query = urlencode(filtered, doseq=True) if filtered else ""

    # Normalize path: strip trailing slash (except root)
    path = parsed.path.rstrip("/") if parsed.path != "/" else parsed.path

    return urlunparse((scheme, host, path, "", clean_query, ""))


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
    "verge": "tech_culture",
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


def _external_id_from_url(url: str) -> str:
    """Генерирует стабильный external_id из canonical URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def _reddit_permalink_to_url(permalink: str) -> str:
    """Преобразует Reddit permalink в полный URL."""
    if permalink.startswith("http"):
        return permalink
    return f"https://www.reddit.com{permalink}"


def _is_reddit_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return host.endswith("reddit.com") or host.endswith("redd.it")


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

    discussion_url = ""
    target_url = ""
    if provider == "reddit":
        discussion_url = canonicalize_url(_reddit_permalink_to_url(card.permalink))
        target_url = canonicalize_url(card.url)
        if target_url and not _is_reddit_url(target_url) and not card.is_self:
            canonical_url = target_url
        else:
            canonical_url = discussion_url
        external_id = card.post_id
    else:
        canonical_url = canonicalize_url(card.url)
        target_url = canonical_url
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

    source_section = card.subreddit
    if legacy_file in ("rss.jsonl", "ladder.jsonl") and card.keyword:
        source_section = card.keyword

    domain_ids = classify_domains(
        title=card.title,
        excerpt=excerpt,
        provider=provider,
        source_section=source_section,
        keyword=card.keyword or "",
        link_flair=card.link_flair_text or "",
    )
    project_scores = compute_project_scores(domain_ids, card.title, excerpt)

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
        source_section=source_section,
        domain_ids=domain_ids,
        discussion_url=discussion_url,
        target_url=target_url,
        dedupe_group_id=stable_hash_id("dedupe", canonical_url or item_id),
        evidence_refs=[],
        raw_engagement=raw_engagement,
        metadata={
            "monitoring_type": card.monitoring_type,
            "keyword": card.keyword,
            "is_self": card.is_self,
            "link_flair_text": card.link_flair_text,
            "discussion_url": discussion_url,
            "target_url": target_url,
            "project_scores": project_scores,
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
