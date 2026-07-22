"""ProductHunt-адаптер: RSS-фид (бесплатно, без ключей).

Feed: https://www.producthunt.com/feed
Fallback: GraphQL API (если PRODUCTHUNT_API_KEY установлен и работает).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from ..models import PostCard

logger = logging.getLogger("reddit_compass")

PH_FEED_URL = "https://www.producthunt.com/feed"
HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    text = HTML_TAG_RE.sub(" ", html)
    return text.strip()[:2000]


async def fetch_producthunt(
    snapshot_date: str,
    limit: int = 30,
) -> list[PostCard]:
    """Загружает топ продуктов из ProductHunt RSS-фида."""
    import aiohttp

    cards: list[PostCard] = []
    headers = {"User-Agent": "reddit-compass/0.2 (trend monitor)"}

    try:
        async with (
            aiohttp.ClientSession(headers=headers) as session,
            session.get(PH_FEED_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp,
        ):
            if resp.status != 200:
                logger.warning("ProductHunt RSS: HTTP %d", resp.status)
                return []
            text = await resp.text()

        root = ET.fromstring(text)

        # Atom: <feed><entry> (namespace http://www.w3.org/2005/Atom)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)

        for entry in entries[:limit]:
            title_el = entry.find("atom:title", ns)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            if not title:
                continue

            link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""

            content_el = entry.find("atom:content", ns)
            desc = _strip_html(content_el.text or "") if content_el is not None else ""

            pub_el = entry.find("atom:published", ns)
            pub_date = pub_el.text.strip() if pub_el is not None and pub_el.text else None

            # Извлекаем ID из Atom id (tag:www.producthunt.com,2005:Post/1202630)
            id_el = entry.find("atom:id", ns)
            post_id = ""
            if id_el is not None and id_el.text:
                post_id = id_el.text.split("/")[-1]
            if not post_id:
                post_id = link.split("/")[-1] if link else title[:20]

            cards.append(
                PostCard(
                    subreddit="producthunt",
                    post_id=post_id,
                    title=title,
                    author="producthunt",
                    created_utc=pub_date,
                    score=0,
                    upvote_ratio=0.0,
                    num_comments=0,
                    url=link,
                    selftext=desc,
                    link_flair_text="producthunt",
                    is_self=True,
                    permalink=link,
                    monitoring_type="rss",
                    snapshot_date=snapshot_date,
                    keyword="producthunt",
                )
            )

    except ET.ParseError as exc:
        logger.warning("ProductHunt RSS parse error: %s", exc)
        return []
    except Exception as exc:
        logger.warning("ProductHunt fetch error: %s", exc)
        return []

    logger.info("ProductHunt: %d продуктов", len(cards))
    return cards
