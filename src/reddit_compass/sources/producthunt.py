"""ProductHunt-адаптер: GraphQL API (бесплатно, OAuth token).

API: https://api.producthunt.com/v2/api/graphql
Token: PRODUCTHUNT_API_KEY (Developer Token из настроек аккаунта).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..models import PostCard

logger = logging.getLogger("reddit_compass")

PH_API_URL = "https://api.producthunt.com/v2/api/graphql"

PH_QUERY = """
query {{
  posts(order: {order}, first: {limit}) {{
    edges {{
      node {{
        id
        name
        tagline
        url
        website
        votesCount
        commentsCount
        createdAt
        topics(edges: {{first: 3}}) {{
          edges {{
            node {{
              name
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


def _get_api_key() -> str:
    return os.environ.get("PRODUCTHUNT_API_KEY", "")


async def fetch_producthunt(
    snapshot_date: str,
    limit: int = 20,
    order: str = "RANKING",
) -> list[PostCard]:
    """Загружает топ продуктов из ProductHunt."""
    import aiohttp

    api_key = _get_api_key()
    if not api_key:
        logger.info("PRODUCTHUNT_API_KEY не установлен — ProductHunt пропущен")
        return []

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    query = PH_QUERY.format(order=order, limit=limit)

    cards: list[PostCard] = []
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                PH_API_URL,
                json={"query": query},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp,
        ):
            if resp.status != 200:
                logger.warning("ProductHunt: HTTP %d", resp.status)
                return []
            data: dict[str, Any] = await resp.json()

            edges = data.get("data", {}).get("posts", {}).get("edges", [])
            for edge in edges:
                node = edge.get("node", {})
                topics = [
                    t["node"]["name"]
                    for t in node.get("topics", {}).get("edges", [])
                    if t.get("node", {}).get("name")
                ]

                cards.append(
                    PostCard(
                        subreddit="producthunt",
                        post_id=str(node.get("id", "")),
                        title=node.get("name", ""),
                        author="producthunt",
                        created_utc=node.get("createdAt"),
                        score=node.get("votesCount", 0),
                        upvote_ratio=0.0,
                        num_comments=node.get("commentsCount", 0),
                        url=node.get("website") or node.get("url", ""),
                        selftext=node.get("tagline", ""),
                        link_flair_text=", ".join(topics) if topics else None,
                        is_self=True,
                        permalink=node.get("url", ""),
                        monitoring_type="api",
                        snapshot_date=snapshot_date,
                        keyword="producthunt",
                    )
                )

    except Exception as exc:
        logger.warning("ProductHunt fetch error: %s", exc)
        return []

    logger.info("ProductHunt: %d продуктов", len(cards))
    return cards
