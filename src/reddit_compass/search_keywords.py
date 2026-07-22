"""Поиск по ключевым словам через Reddit JSON API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .client import (
    RedditEngine,
    parse_listing_json,
    rate_limit_pause,
)
from .fetch_subreddits import _fetch_comments, _json_to_card
from .models import PostCard

if TYPE_CHECKING:
    from .config import MonitorConfig

logger = logging.getLogger("reddit_compass")


async def search_keyword(
    engine: RedditEngine,
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
    comments_top_n = settings.comments_for_top_n

    encoded = keyword.replace(" ", "+")
    url = (
        f"https://www.reddit.com/search.json?q={encoded}&sort={sort}&t={time_filter}&limit={limit}"
    )

    data = await engine.fetch_json(url)
    posts = parse_listing_json(data)

    cards: list[PostCard] = []
    for post in posts:
        card = _json_to_card(post, "search", snapshot_date, keyword=keyword)
        cards.append(card)

    # Комментарии — только для top-N по score
    if comment_limit > 0 and comments_top_n > 0 and cards:
        ranked = sorted(cards, key=lambda c: c.score, reverse=True)
        for card in ranked[:comments_top_n]:
            card.top_comments = await _fetch_comments(engine, card.permalink, comment_limit)
            await rate_limit_pause(config.settings.stealth)

    logger.info("Keyword %r: найдено %d постов", keyword, len(cards))
    return cards


async def search_all_keywords(
    config: MonitorConfig,
    snapshot_date: str,
) -> list[PostCard]:
    """Ищет посты по всем ключевым словам."""
    stealth = config.settings.stealth
    engine = RedditEngine(stealth=stealth)
    await engine.start()
    all_cards: list[PostCard] = []
    try:
        for keyword in config.keywords:
            cards = await search_keyword(engine, keyword, config, snapshot_date)
            all_cards.extend(cards)
            await rate_limit_pause(stealth)
    finally:
        await engine.close()

    if not all_cards:
        logger.info("JSON API недоступен для search — результаты пустые")

    return all_cards
