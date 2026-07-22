"""Reddit клиент: Playwright JSON API (основной) + RSS (fallback).

Playwright открывает headless Chromium → fetch() к Reddit JSON API из браузера.
Это даёт полные данные (score, комментарии, top/rising/search) без API credentials.

Если Playwright не установлен — fallback на Atom RSS (только hot, без score).
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("reddit_compass")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

REQUEST_PAUSE = 4.0
MAX_RETRIES = 2
RETRY_PAUSE = 10.0

# ── Playwright engine ──────────────────────────────────────────────────────

_playwright_available: bool | None = None


def _check_playwright() -> bool:
    global _playwright_available
    if _playwright_available is not None:
        return _playwright_available
    try:
        from playwright.async_api import async_playwright  # noqa: F401

        _playwright_available = True
    except ImportError:
        _playwright_available = False
        logger.warning(
            "Playwright не установлен — fallback на RSS (только hot, без score). "
            "Установите: pip install playwright && playwright install chromium"
        )
    return _playwright_available


class RedditBrowser:
    """Headless Chromium для Reddit JSON API."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._page = await self._browser.new_page(user_agent=USER_AGENT)
        # Открываем Reddit для получения cookies
        try:
            await self._page.goto(
                "https://www.reddit.com/", wait_until="domcontentloaded", timeout=30000
            )
            await asyncio.sleep(2)
            logger.info("Reddit browser session открыта")
        except Exception as exc:
            logger.warning("Не удалось открыть reddit.com: %s", exc)

    async def fetch_json(self, url: str) -> Any | None:
        """Загружает JSON через fetch() в контексте браузера."""
        if self._page is None:
            return None
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = await self._page.evaluate(
                    """async (url) => {
                        try {
                            const resp = await fetch(url, {
                                headers: {'Accept': 'application/json'}
                            });
                            if (resp.status === 429) return {__status: 429};
                            if (!resp.ok) return {__status: resp.status};
                            return await resp.json();
                        } catch (e) {
                            return {__error: e.message};
                        }
                    }""",
                    url,
                )
                if result is None:
                    return None
                if isinstance(result, dict):
                    if result.get("__status") == 429:
                        if attempt < MAX_RETRIES:
                            logger.warning(
                                "JSON %s: 429, retry %d/%d", url, attempt + 1, MAX_RETRIES
                            )
                            await asyncio.sleep(RETRY_PAUSE)
                            continue
                        logger.warning("JSON %s: 429, исчерпаны retries", url)
                        return None
                    if "__status" in result:
                        logger.warning("JSON %s: HTTP %s", url, result["__status"])
                        return None
                    if "__error" in result:
                        logger.warning("JSON %s: %s", url, result["__error"])
                        return None
                return result
            except Exception as exc:
                logger.warning("JSON fetch error for %s: %s", url, exc)
                return None
        return None

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._page = None
        self._browser = None
        self._pw = None


# ── JSON parsing ───────────────────────────────────────────────────────────


def parse_listing_json(data: Any) -> list[dict[str, Any]]:
    """Парсит Reddit JSON listing (hot/top/rising/search)."""
    if not data or not isinstance(data, dict):
        return []
    children = data.get("data", {}).get("children", [])
    posts = []
    for child in children:
        if child.get("kind") != "t3":
            continue
        d = child.get("data", {})
        posts.append(
            {
                "post_id": d.get("id", ""),
                "title": d.get("title", ""),
                "author": d.get("author", "[deleted]"),
                "subreddit": d.get("subreddit", ""),
                "score": d.get("score", 0),
                "upvote_ratio": d.get("upvote_ratio", 0.0),
                "num_comments": d.get("num_comments", 0),
                "url": d.get("url", ""),
                "permalink": d.get("permalink", ""),
                "selftext": (d.get("selftext", "") or "")[:5000],
                "link_flair_text": d.get("link_flair_text"),
                "is_self": d.get("is_self", False),
                "created_utc": d.get("created_utc"),
                "is_video": d.get("is_video", False),
                "over_18": d.get("over_18", False),
                "stickied": d.get("stickied", False),
                "crosspost_parent_list": d.get("crosspost_parent_list", []),
            }
        )
    return posts


def parse_comments_json(data: Any, limit: int = 5) -> list[dict[str, Any]]:
    """Парсит Reddit JSON комментарии (top-N по score)."""
    if not data or not isinstance(data, list) or len(data) < 2:
        return []
    comments_listing = data[1]
    children = comments_listing.get("data", {}).get("children", [])
    comments = []
    for child in children:
        if child.get("kind") != "t1":
            continue
        d = child.get("data", {})
        body = d.get("body", "")
        if body in ("[removed]", "[deleted]"):
            continue
        if d.get("stickied"):
            continue
        comments.append(
            {
                "comment_id": d.get("id", ""),
                "author": d.get("author", "[deleted]"),
                "score": d.get("score", 0),
                "body": body[:2000],
                "created_utc": d.get("created_utc"),
                "is_submitter": d.get("is_submitter", False),
            }
        )
    comments.sort(key=lambda c: c["score"], reverse=True)
    return comments[:limit]


# ── RSS fallback ───────────────────────────────────────────────────────────

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class RSSEntry:
    title: str
    author: str
    url: str
    permalink: str
    subreddit: str
    post_id: str
    published: str | None
    updated: str | None
    content_html: str
    category: str | None

    @property
    def created_utc(self) -> str | None:
        return self.published or self.updated


def parse_rss(xml_text: str, default_subreddit: str) -> list[RSSEntry]:
    """Парсит Atom RSS фид Reddit (fallback)."""
    if not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("RSS parse error: %s", exc)
        return []

    entries: list[RSSEntry] = []
    for entry_el in root.findall("atom:entry", ATOM_NS):
        title_el = entry_el.find("atom:title", ATOM_NS)
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if not title:
            continue

        author_el = entry_el.find("atom:author/atom:name", ATOM_NS)
        author = author_el.text.strip() if author_el is not None and author_el.text else "[deleted]"
        if author.startswith("/u/"):
            author = author[3:]

        url = ""
        permalink = ""
        link_el = entry_el.find("atom:link", ATOM_NS)
        if link_el is not None:
            url = link_el.get("href", "")
            if "/comments/" in url:
                permalink = url.split("?", 1)[0]
                if permalink.startswith("https://www.reddit.com"):
                    permalink = permalink[len("https://www.reddit.com") :]

        post_id = ""
        id_el = entry_el.find("atom:id", ATOM_NS)
        if id_el is not None and id_el.text:
            raw_id = id_el.text.strip()
            post_id = raw_id.split("_", 1)[-1] if "_" in raw_id else raw_id

        category_el = entry_el.find("atom:category", ATOM_NS)
        subreddit = (
            category_el.get("term", default_subreddit)
            if category_el is not None
            else default_subreddit
        )

        published_el = entry_el.find("atom:published", ATOM_NS)
        published = (
            published_el.text.strip() if published_el is not None and published_el.text else None
        )

        updated_el = entry_el.find("atom:updated", ATOM_NS)
        updated = updated_el.text.strip() if updated_el is not None and updated_el.text else None

        content_el = entry_el.find("atom:content", ATOM_NS)
        content_html = content_el.text.strip() if content_el is not None and content_el.text else ""

        entries.append(
            RSSEntry(
                title=title,
                author=author,
                url=url,
                permalink=permalink,
                subreddit=subreddit,
                post_id=post_id,
                published=published,
                updated=updated,
                content_html=content_html,
                category=category_el.get("label") if category_el is not None else None,
            )
        )
    return entries


async def fetch_rss_aiohttp(url: str, subreddit: str) -> list[RSSEntry]:
    """Загружает RSS через aiohttp (fallback)."""
    try:
        import aiohttp
    except ImportError:
        logger.warning("aiohttp не установлен — RSS fallback недоступен")
        return []
    try:
        async with (
            aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp,
        ):
            if resp.status != 200:
                logger.warning("RSS %s: HTTP %d", url, resp.status)
                return []
            text = await resp.text()
            return parse_rss(text, subreddit)
    except Exception as exc:
        logger.warning("RSS fetch error for %s: %s", url, exc)
        return []


async def rate_limit_pause() -> None:
    await asyncio.sleep(REQUEST_PAUSE)
