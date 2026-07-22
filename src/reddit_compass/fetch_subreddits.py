"""Сбор hot/top постов по сабреддитам через Reddit JSON API."""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import TYPE_CHECKING, Any

from .client import (
    RedditEngine,
    fetch_rss_aiohttp,
    parse_comments_json,
    parse_listing_json,
    rate_limit_pause,
)
from .models import CommentCard, PostCard, iso_utc, utc_from_timestamp

if TYPE_CHECKING:
    from .config import MonitorConfig

logger = logging.getLogger("reddit_compass")


def _json_to_card(
    post: dict[str, Any],
    monitoring_type: str,
    snapshot_date: str,
    keyword: str | None = None,
    top_comments: list[CommentCard] | None = None,
) -> PostCard:
    created = post.get("created_utc")
    created_str = iso_utc(utc_from_timestamp(created)) if created else None

    crosspost_parents = []
    for parent in post.get("crosspost_parent_list", []):
        pid = parent.get("id", "")
        psub = parent.get("subreddit", "")
        if pid:
            crosspost_parents.append(f"{psub}/{pid}")

    return PostCard(
        subreddit=post.get("subreddit", ""),
        post_id=post.get("post_id", ""),
        title=post.get("title", ""),
        author=post.get("author", "[deleted]"),
        created_utc=created_str,
        score=post.get("score", 0),
        upvote_ratio=post.get("upvote_ratio", 0.0),
        num_comments=post.get("num_comments", 0),
        url=post.get("url", ""),
        selftext=post.get("selftext", ""),
        link_flair_text=post.get("link_flair_text"),
        is_self=post.get("is_self", False),
        permalink=post.get("permalink", ""),
        monitoring_type=monitoring_type,
        snapshot_date=snapshot_date,
        keyword=keyword,
        top_comments=top_comments or [],
        crosspost_parents=crosspost_parents,
        is_video=post.get("is_video", False),
        over_18=post.get("over_18", False),
        stickied=post.get("stickied", False),
    )


async def _fetch_comments(
    engine: RedditEngine,
    permalink: str,
    limit: int,
) -> list[CommentCard]:
    """Загружает top-N комментариев к посту."""
    if limit <= 0 or not permalink:
        return []
    url = f"https://www.reddit.com{permalink}.json?limit={limit + 10}&sort=top"
    data = await engine.fetch_json(url)
    if data is None:
        return []
    raw_comments = parse_comments_json(data, limit)
    return [
        CommentCard(
            comment_id=c["comment_id"],
            author=c["author"],
            score=c["score"],
            body=c["body"],
            created_utc=iso_utc(utc_from_timestamp(c["created_utc"]))
            if c.get("created_utc")
            else None,
            is_submitter=c.get("is_submitter", False),
        )
        for c in raw_comments
    ]


async def fetch_subreddit_posts(
    engine: RedditEngine,
    subreddit_name: str,
    config: MonitorConfig,
    snapshot_date: str,
    modes: list[str] | None = None,
) -> list[PostCard]:
    """Собирает посты из одного сабреддита: hot + top.

    Комментарии загружаются только для top-N постов по score (comments_for_top_n),
    что сокращает объём запросов в ~5 раз.
    """
    if modes is None:
        modes = ["hot", "top"]

    settings = config.settings
    limit = settings.posts_per_subreddit
    comment_limit = settings.top_comments_per_post
    comments_top_n = settings.comments_for_top_n
    stealth = settings.stealth
    time_filter = settings.time_filter

    cards: list[PostCard] = []
    seen_ids: set[str] = set()

    for mode in modes:
        if mode == "hot":
            url = f"https://www.reddit.com/r/{subreddit_name}/hot.json?limit={limit}"
        elif mode == "top":
            url = (
                f"https://www.reddit.com/r/{subreddit_name}/top.json?t={time_filter}&limit={limit}"
            )
        elif mode == "rising":
            url = f"https://www.reddit.com/r/{subreddit_name}/rising.json?limit={limit}"
        else:
            continue

        data = await engine.fetch_json(url)
        posts = parse_listing_json(data)

        for post in posts:
            pid = post["post_id"]
            if pid in seen_ids or post.get("stickied"):
                continue
            seen_ids.add(pid)
            card = _json_to_card(post, mode, snapshot_date)
            cards.append(card)

        await rate_limit_pause(stealth)

    # Комментарии — только для top-N по score (экономия запросов)
    if comment_limit > 0 and comments_top_n > 0 and cards:
        ranked = sorted(cards, key=lambda c: c.score, reverse=True)
        for card in ranked[:comments_top_n]:
            card.top_comments = await _fetch_comments(engine, card.permalink, comment_limit)
            await rate_limit_pause(stealth)

    logger.info("r/%s: собрано %d постов (%s)", subreddit_name, len(cards), "+".join(modes))
    return cards


async def fetch_all_subreddits(
    config: MonitorConfig,
    snapshot_date: str,
    modes: list[str] | None = None,
) -> list[PostCard]:
    """Собирает посты из всех сабреддитов (aiohttp → Playwright → RSS)."""
    stealth = config.settings.stealth
    engine = RedditEngine(stealth=stealth)
    await engine.start()
    all_cards: list[PostCard] = []
    try:
        for name in config.all_subreddits:
            cards = await fetch_subreddit_posts(engine, name, config, snapshot_date, modes)
            all_cards.extend(cards)
            await rate_limit_pause(stealth)
    finally:
        await engine.close()

    # Если aiohttp+Playwright не дали данных — RSS fallback
    if not all_cards:
        logger.warning("JSON API недоступен — fallback на RSS")
        return await _fetch_all_subreddits_rss(config, snapshot_date)

    return all_cards


# ── RSS fallback ───────────────────────────────────────────────────────────

HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    text = HTML_TAG_RE.sub(" ", html)
    return unescape(text).strip()[:5000]


async def _fetch_all_subreddits_rss(
    config: MonitorConfig,
    snapshot_date: str,
) -> list[PostCard]:
    """Fallback: RSS hot (без score/комментариев)."""
    all_cards: list[PostCard] = []
    for name in config.all_subreddits:
        limit = config.settings.posts_per_subreddit
        url = f"https://www.reddit.com/r/{name}/hot/.rss?limit={limit}"
        entries = await fetch_rss_aiohttp(url, name)
        for e in entries:
            all_cards.append(
                PostCard(
                    subreddit=e.subreddit,
                    post_id=e.post_id,
                    title=e.title,
                    author=e.author,
                    created_utc=e.created_utc,
                    score=0,
                    upvote_ratio=0.0,
                    num_comments=0,
                    url=e.url,
                    selftext=_strip_html(e.content_html),
                    link_flair_text=None,
                    is_self=bool(e.content_html),
                    permalink=e.permalink,
                    monitoring_type="hot",
                    snapshot_date=snapshot_date,
                )
            )
        await rate_limit_pause()
    return all_cards
