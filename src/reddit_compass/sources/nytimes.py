"""NYT adapter: Top Stories API + Article Search API.

Приоритет:
1. NYT Top Stories API для текущих разделов.
2. NYT Article Search API для профильных keywords.

Config: NYT_API_KEY
Если ключ отсутствует: adapter status not_configured.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import aiohttp

from ..models import PostCard

logger = logging.getLogger("reddit_compass")

NYT_API_KEY_ENV = "NYT_API_KEY"  # pragma: allowlist secret
NYT_TOP_STORIES_URL = "https://api.nytimes.com/svc/topstories/v2/{section}.json"
NYT_ARTICLE_SEARCH_URL = "https://api.nytimes.com/svc/search/v2/articlesearch.json"

DEFAULT_SECTIONS = ["technology", "business", "science"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_configured() -> bool:
    return bool(os.environ.get(NYT_API_KEY_ENV))


async def fetch_nyt_top_stories(
    sections: list[str] | None = None,
    snapshot_date: str | None = None,
) -> list[PostCard]:
    """Загружает топ-статьи из NYT Top Stories API.

    Args:
        sections: Список разделов. По умолчанию: technology, business, science.
        snapshot_date: Дата snapshot.

    Returns:
        Список PostCard.
    """
    if not _is_configured():
        logger.warning("NYT_API_KEY not configured, skipping NYT fetch")
        return []

    api_key = os.environ.get(NYT_API_KEY_ENV, "")
    sections = sections or DEFAULT_SECTIONS
    snapshot_date = snapshot_date or datetime.now(UTC).strftime("%Y-%m-%d")

    cards: list[PostCard] = []

    async with aiohttp.ClientSession() as session:
        for section in sections:
            url = NYT_TOP_STORIES_URL.format(section=section)
            params = {"api-key": api_key}

            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with session.get(url, params=params, timeout=timeout) as resp:
                    if resp.status != 200:
                        logger.warning("NYT API error for %s: %d", section, resp.status)
                        continue

                    data: dict[str, Any] = await resp.json()
                    results = data.get("results", [])

                    for article in results:
                        card = _article_to_card(article, section, snapshot_date)
                        if card:
                            cards.append(card)

            except Exception:
                logger.exception("NYT fetch failed for section %s", section)
                continue

    logger.info("NYT: fetched %d articles from %d sections", len(cards), len(sections))
    return cards


async def fetch_nyt_article_search(
    query: str,
    snapshot_date: str | None = None,
) -> list[PostCard]:
    """Загружает статьи из NYT Article Search API.

    Args:
        query: Поисковый запрос.
        snapshot_date: Дата snapshot.

    Returns:
        Список PostCard.
    """
    if not _is_configured():
        logger.warning("NYT_API_KEY not configured, skipping NYT search")
        return []

    api_key = os.environ.get(NYT_API_KEY_ENV, "")
    snapshot_date = snapshot_date or datetime.now(UTC).strftime("%Y-%m-%d")

    cards: list[PostCard] = []

    async with aiohttp.ClientSession() as session:
        params: dict[str, str] = {
            "q": query,
            "api-key": api_key,
            "page": "0",
        }

        try:
            async with session.get(
                NYT_ARTICLE_SEARCH_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    logger.warning("NYT Article Search error: %d", resp.status)
                    return []

                data: dict[str, Any] = await resp.json()
                docs = data.get("response", {}).get("docs", [])

                for doc in docs:
                    card = _doc_to_card(doc, snapshot_date)
                    if card:
                        cards.append(card)

        except Exception:
            logger.exception("NYT Article Search failed for query: %s", query)

    return cards


def _article_to_card(
    article: dict[str, Any],
    section: str,
    snapshot_date: str,
) -> PostCard | None:
    """Преобразует NYT Top Stories article в PostCard."""
    url = article.get("url", "")
    title = article.get("title", "")

    if not url or not title:
        return None

    abstract = article.get("abstract", "")
    byline = article.get("byline", "")
    published = article.get("published_date", "")

    return PostCard(
        subreddit="nytimes",
        post_id=f"nyt_{hash(url) % 10**8}",
        title=title,
        author=byline,
        created_utc=published,
        score=0,
        upvote_ratio=0.0,
        num_comments=0,
        url=url,
        selftext=abstract,
        link_flair_text=section,
        is_self=False,
        permalink=url,
        monitoring_type="api",
        snapshot_date=snapshot_date,
        keyword=None,
    )


def _doc_to_card(
    doc: dict[str, Any],
    snapshot_date: str,
) -> PostCard | None:
    """Преобразует NYT Article Search doc в PostCard."""
    web_url = doc.get("web_url", "")
    headline = doc.get("headline", {})
    title = headline.get("main", "")

    if not web_url or not title:
        return None

    abstract = doc.get("abstract", "") or headline.get("kicker", "")
    byline = doc.get("byline", {}).get("original", "")
    pub_date = doc.get("pub_date", "")

    return PostCard(
        subreddit="nytimes",
        post_id=f"nyt_{hash(web_url) % 10**8}",
        title=title,
        author=byline,
        created_utc=pub_date,
        score=0,
        upvote_ratio=0.0,
        num_comments=0,
        url=web_url,
        selftext=abstract,
        link_flair_text="search",
        is_self=False,
        permalink=web_url,
        monitoring_type="api",
        snapshot_date=snapshot_date,
        keyword=None,
    )


async def fetch_all_nyt(
    snapshot_date: str | None = None,
    keywords: list[str] | None = None,
) -> list[PostCard]:
    """Загружает все NYT статьи: top stories + article search.

    Args:
        snapshot_date: Дата snapshot.
        keywords: Keywords для article search.

    Returns:
        Список PostCard.
    """
    if not _is_configured():
        return []

    cards = await fetch_nyt_top_stories(snapshot_date=snapshot_date)

    if keywords:
        for keyword in keywords[:3]:  # Ограничиваем до 3 запросов
            search_cards = await fetch_nyt_article_search(keyword, snapshot_date)
            cards.extend(search_cards)

    return cards
