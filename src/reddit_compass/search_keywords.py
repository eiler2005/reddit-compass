"""Поиск по ключевым словам через Playwright JSON API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .client import (
    RedditBrowser,
    _check_playwright,
    parse_listing_json,
    rate_limit_pause,
)
from .fetch_subreddits import _fetch_comments, _json_to_card
from .models import PostCard

if TYPE_CHECKING:
    from .config import MonitorConfig

logger = logging.getLogger("reddit_compass")


async def search_keyword(
    browser: RedditBrowser,
    keyword: str,
    config: MonitorConfig,
    snapshot_date: str,
) -> list[PostCard]:
    """Ищет посты по ключевому слову через search JSON API."""
    settings = config.settings
    limit = settings.posts_per_subreddit
    sort = settings.search_sort
    time_filter = settings.search_time_filter
    comment_limit = settings.top_comments_per_post

    encoded = keyword.replace(" ", "+")
    url = (
        f"https://www.reddit.com/search.json?q={encoded}&sort={sort}&t={time_filter}&limit={limit}"
    )

    data = await browser.fetch_json(url)
    posts = parse_listing_json(data)

    cards: list[PostCard] = []
    for post in posts:
        top_comments = await _fetch_comments(browser, post["permalink"], comment_limit)
        card = _json_to_card(
            post, "search", snapshot_date, keyword=keyword, top_comments=top_comments
        )
        cards.append(card)

    logger.info("Keyword %r: найдено %d постов", keyword, len(cards))
    return cards


async def search_all_keywords(
    config: MonitorConfig,
    snapshot_date: str,
) -> list[PostCard]:
    """Ищет посты по всем ключевым словам."""
    if not _check_playwright():
        logger.info("Playwright недоступен — search пропущен")
        return []

    browser = RedditBrowser()
    await browser.start()
    all_cards: list[PostCard] = []
    try:
        for keyword in config.keywords:
            cards = await search_keyword(browser, keyword, config, snapshot_date)
            all_cards.extend(cards)
            await rate_limit_pause()
    finally:
        await browser.close()
    return all_cards
