"""Hacker News source: Algolia Search API (бесплатно, без ключей).

API: https://hn.algolia.com/api/v1/search
Query: AI-темы, tags: story, hitsPerPage: 50.
Выход: PostCard (source="hackernews") → hackernews.jsonl.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import PostCard

logger = logging.getLogger("reddit_compass")

ALGOLIA_BASE = "https://hn.algolia.com/api/v1"

# Запросы для мониторинга трендов на HN (AI + общие)
DEFAULT_QUERIES = [
    # AI и технологии
    "AI agents",
    "LLM",
    "AI layoffs",
    "vibe coding",
    "AI startup",
    "GPT",
    "Claude",
    "open source AI",
    # Общие тренды
    "startup funding",
    "privacy surveillance",
    "tech layoffs",
    "remote work",
    "open source",
    "cybersecurity",
]


async def fetch_hn_stories(
    queries: list[str] | None = None,
    hits_per_query: int = 20,
    snapshot_date: str = "",
) -> list[PostCard]:
    """Загружает stories из HN по AI-запросам через Algolia API."""
    import aiohttp

    queries = queries or DEFAULT_QUERIES
    cards: list[PostCard] = []
    seen_ids: set[str] = set()

    headers = {"User-Agent": "reddit-compass/0.2 (trend monitor)"}

    async with aiohttp.ClientSession(headers=headers) as session:
        for query in queries:
            url = (
                f"{ALGOLIA_BASE}/search"
                f"?query={query.replace(' ', '+')}"
                f"&tags=story&hitsPerPage={hits_per_query}"
            )
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.warning("HN Algolia %r: HTTP %d", query, resp.status)
                        continue
                    data: dict[str, Any] = await resp.json()

                    for hit in data.get("hits", []):
                        object_id = hit.get("objectID", "")
                        if object_id in seen_ids:
                            continue
                        seen_ids.add(object_id)

                        title = hit.get("title", "")
                        if not title:
                            continue

                        cards.append(
                            PostCard(
                                subreddit="hackernews",
                                post_id=object_id,
                                title=title,
                                author=hit.get("author", ""),
                                created_utc=hit.get("created_at"),
                                score=hit.get("points", 0),
                                upvote_ratio=0.0,
                                num_comments=hit.get("num_comments", 0),
                                url=hit.get(
                                    "url", f"https://news.ycombinator.com/item?id={object_id}"
                                ),
                                selftext="",
                                link_flair_text=None,
                                is_self=not hit.get("url"),
                                permalink=f"/item?id={object_id}",
                                monitoring_type="search",
                                snapshot_date=snapshot_date,
                                keyword=query,
                            )
                        )

                logger.info("HN %r: %d stories", query, len(data.get("hits", [])))

            except Exception as exc:
                logger.warning("HN fetch error for %r: %s", query, exc)
                continue

    logger.info("HN total: %d unique stories", len(cards))
    return cards
