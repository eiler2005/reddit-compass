"""Hacker News source: Algolia Search API (бесплатно, без ключей).

API: https://hn.algolia.com/api/v1/search
Query: AI-темы, tags: story, hitsPerPage: 50.
Выход: PostCard (source="hackernews") → hackernews.jsonl.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from ..models import PostCard
from .errors import RequestTally

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
    historical_date: str | None = None,
) -> list[PostCard]:
    """Load HN stories from a recent or one exact historical UTC interval."""
    import time as _time

    import aiohttp

    queries = queries or DEFAULT_QUERIES
    cards: list[PostCard] = []
    seen_ids: set[str] = set()
    tally = RequestTally("hackernews")

    if historical_date:
        start = datetime.strptime(historical_date, "%Y-%m-%d").replace(tzinfo=UTC)
        end = start + timedelta(days=1)
        time_filter = f"created_at_i>={int(start.timestamp())},created_at_i<{int(end.timestamp())}"
    else:
        since_ts = int(_time.time()) - (days_back * 86400)
        time_filter = f"created_at_i>{since_ts}"

    headers = {"User-Agent": "reddit-compass/0.2 (trend monitor)"}

    async with aiohttp.ClientSession(headers=headers) as session:
        front_page_specs = (
            [
                (
                    "historical_date",
                    _algolia_url(
                        "search_by_date",
                        {
                            "tags": "story",
                            "hitsPerPage": hits_per_query,
                            "numericFilters": time_filter,
                        },
                    ),
                ),
                (
                    "historical_top",
                    _algolia_url(
                        "search",
                        {
                            "tags": "story",
                            "hitsPerPage": hits_per_query,
                            "numericFilters": f"{time_filter},points>50",
                        },
                    ),
                ),
            ]
            if historical_date
            else [
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
                            "numericFilters": time_filter,
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
                            "numericFilters": f"{time_filter},points>50",
                        },
                    ),
                ),
            ]
        )
        for label, url in front_page_specs:
            await _fetch_algolia_url(session, url, label, snapshot_date, seen_ids, cards, tally)

        for query in queries:
            url = _algolia_url(
                "search",
                {
                    "query": query,
                    "tags": "story",
                    "hitsPerPage": hits_per_query,
                    "numericFilters": time_filter,
                },
            )
            await _fetch_algolia_url(session, url, query, snapshot_date, seen_ids, cards, tally)

    logger.info("HN total: %d unique stories", len(cards))
    # Пустой список после того, как отвалились все запросы, — не пустой день.
    tally.raise_if_total_failure()
    return cards


async def _fetch_algolia_url(
    session: Any,
    url: str,
    query_label: str,
    snapshot_date: str,
    seen_ids: set[str],
    cards: list[PostCard],
    tally: RequestTally | None = None,
) -> None:
    import aiohttp

    if tally is not None:
        tally.attempt()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning("HN Algolia %r: HTTP %d", query_label, resp.status)
                if tally is not None:
                    tally.failed(f"{query_label}: HTTP {resp.status}")
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
        if tally is not None:
            tally.failed(f"{query_label}: {type(exc).__name__}")
        return


def _algolia_url(endpoint: str, params: dict[str, object]) -> str:
    return f"{ALGOLIA_BASE}/{endpoint}?{urlencode(params)}"
