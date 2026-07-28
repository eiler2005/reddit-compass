"""Hacker News source: Algolia Search API (бесплатно, без ключей).

API: https://hn.algolia.com/api/v1/search
Query: AI-темы, tags: story, hitsPerPage: 50.
Выход: PostCard (source="hackernews") → hackernews.jsonl.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

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
    days_back: int = 7,
) -> list[PostCard]:
    """Загружает stories из HN по запросам через Algolia API (последние N дней)."""
    import time as _time

    import aiohttp

    queries = queries or DEFAULT_QUERIES
    cards: list[PostCard] = []
    seen_ids: set[str] = set()

    # Фильтр: только последние N дней
    since_ts = int(_time.time()) - (days_back * 86400)

    headers = {"User-Agent": "reddit-compass/0.2 (trend monitor)"}

    async with aiohttp.ClientSession(headers=headers) as session:
        front_page_specs = [
            (
                "front_page",
                _algolia_url(
                    "search",
                    {"tags": "front_page", "hitsPerPage": hits_per_query},
                ),
            ),
            (
                "new",
                _algolia_url(
                    "search_by_date",
                    {
                        "tags": "story",
                        "hitsPerPage": hits_per_query,
                        "numericFilters": f"created_at_i>{since_ts}",
                    },
                ),
            ),
            (
                "weekly_top",
                _algolia_url(
                    "search",
                    {
                        "tags": "story",
                        "hitsPerPage": hits_per_query,
                        "numericFilters": f"created_at_i>{since_ts},points>50",
                    },
                ),
            ),
        ]
        for label, url in front_page_specs:
            await _fetch_algolia_url(session, url, label, snapshot_date, seen_ids, cards)

        for query in queries:
            url = _algolia_url(
                "search",
                {
                    "query": query,
                    "tags": "story",
                    "hitsPerPage": hits_per_query,
                    "numericFilters": f"created_at_i>{since_ts}",
                },
            )
            await _fetch_algolia_url(session, url, query, snapshot_date, seen_ids, cards)

    logger.info("HN total: %d unique stories", len(cards))
    return cards


async def _fetch_algolia_url(
    session: Any,
    url: str,
    query_label: str,
    snapshot_date: str,
    seen_ids: set[str],
    cards: list[PostCard],
) -> None:
    import aiohttp

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning("HN Algolia %r: HTTP %d", query_label, resp.status)
                return
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
                        url=hit.get("url", f"https://news.ycombinator.com/item?id={object_id}"),
                        selftext="",
                        link_flair_text=None,
                        is_self=not hit.get("url"),
                        permalink=f"/item?id={object_id}",
                        monitoring_type=(
                            "front_page"
                            if query_label in {"front_page", "new", "weekly_top"}
                            else "search"
                        ),
                        snapshot_date=snapshot_date,
                        keyword=query_label,
                    )
                )

        logger.info("HN %r: %d stories", query_label, len(data.get("hits", [])))

    except Exception as exc:
        logger.warning("HN fetch error for %r: %s", query_label, exc)
        return


def _algolia_url(endpoint: str, params: dict[str, object]) -> str:
    return f"{ALGOLIA_BASE}/{endpoint}?{urlencode(params)}"
