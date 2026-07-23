"""Ladder-адаптер: paywall-источники через self-hosted Ladder proxy.

Источники: NYT, WaPo, FT, Wired, Medium, Time, USA Today, Fox, New Yorker, VF, AmBanker.
Ladder: http://localhost:8080 (на HostKey). Правила: ladder-rules (per-domain).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from ..models import PostCard

logger = logging.getLogger("reddit_compass")

HTML_TAG_RE = re.compile(r"<[^>]+>")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_DESC_RE = re.compile(r'<meta\s+name="description"\s+content="([^"]*)"', re.IGNORECASE)
ARTICLE_RE = re.compile(r"<article[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class LadderSource:
    """Описание paywall-источника через Ladder."""

    name: str
    cluster: str
    base_url: str
    search_paths: list[str]
    country: str = ""


# Конфигурация Ladder-источников (12 доменов из ruleset)
LADDER_SOURCES: list[LadderSource] = [
    # Кластер 1: Мейнстрим
    LadderSource(
        name="nytimes",
        cluster="mainstream",
        country="US",
        base_url="https://www.nytimes.com",
        search_paths=["/section/technology", "/section/business"],
    ),
    LadderSource(
        name="washingtonpost",
        cluster="mainstream",
        country="US",
        base_url="https://www.washingtonpost.com",
        search_paths=["/technology/", "/business/"],
    ),
    LadderSource(
        name="time",
        cluster="mainstream",
        country="US",
        base_url="https://time.com",
        search_paths=["/section/tech/", "/section/business/"],
    ),
    # Кластер 2: Бизнес/финансы
    LadderSource(
        name="ft",
        cluster="business",
        country="UK",
        base_url="https://www.ft.com",
        search_paths=["/technology", "/companies"],
    ),
    LadderSource(
        name="americanbanker",
        cluster="business",
        country="US",
        base_url="https://www.americanbanker.com",
        search_paths=["/news"],
    ),
    # Кластер 3: Tech/культура
    LadderSource(
        name="wired",
        cluster="tech",
        country="US",
        base_url="https://www.wired.com",
        search_paths=["/tag/artificial-intelligence/", "/category/business/"],
    ),
    LadderSource(
        name="newyorker",
        cluster="tech",
        country="US",
        base_url="https://www.newyorker.com",
        search_paths=["/tech", "/business"],
    ),
    LadderSource(
        name="vanityfair",
        cluster="tech",
        country="US",
        base_url="https://www.vanityfair.com",
        search_paths=["/tech", "/business"],
    ),
    # Кластер 5: Массовый пульс
    LadderSource(
        name="foxnews",
        cluster="pulse",
        country="US",
        base_url="https://www.foxnews.com",
        search_paths=["/tech", "/media"],
    ),
]


def _get_ladder_url() -> str:
    """URL Ladder proxy (по умолчанию localhost:8080 на VPS)."""
    return os.environ.get("LADDER_URL", "http://localhost:8080")


def _extract_title(html: str) -> str:
    m = TITLE_RE.search(html)
    return m.group(1).strip() if m else ""


def _extract_description(html: str) -> str:
    m = META_DESC_RE.search(html)
    return m.group(1).strip()[:1000] if m else ""


# Паттерн для извлечения ссылок на статьи из listing-страниц
LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
# URL-паттерны статей (даты, /article/, /story/, /news/, /2026/, /2025/)
ARTICLE_URL_RE = re.compile(
    r"(/\d{4}/\d{2}/|/article/|/story/|/news/|/p/|/post/|/blog/)", re.IGNORECASE
)


def _extract_articles_from_listing(html: str, base_url: str) -> list[dict[str, str]]:
    """Извлекает статьи (title + url) из HTML listing-страницы."""
    articles: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for match in LINK_RE.finditer(html):
        href = match.group(1).strip()
        inner_html = match.group(2)
        # Очищаем title от HTML-тегов
        title = HTML_TAG_RE.sub("", inner_html).strip()

        # Фильтры: только статьи с нормальным заголовком
        if not title or len(title) < 15:
            continue
        if href.startswith(("#", "javascript:", "mailto:")):
            continue

        # Полный URL (Ladder может возвращать /https://... — чистим)
        if href.startswith("/https://") or href.startswith("/http://"):
            full_url = href[1:]  # убираем ведущий /
        elif href.startswith("/"):
            full_url = f"{base_url}{href}"
        elif href.startswith("http"):
            full_url = href
        else:
            continue

        # Только article-like URL
        if not ARTICLE_URL_RE.search(full_url):
            continue

        # Дедупликация
        clean_url = full_url.split("?")[0].split("#")[0]
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        articles.append({"title": title[:200], "url": clean_url})

    return articles


async def fetch_ladder_page(url: str) -> str | None:
    """Загружает страницу через Ladder proxy."""
    import aiohttp

    ladder_url = _get_ladder_url()
    proxy_url = f"{ladder_url}/{url}"

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(proxy_url, timeout=aiohttp.ClientTimeout(total=30)) as resp,
        ):
            if resp.status != 200:
                logger.warning("Ladder %s: HTTP %d", url, resp.status)
                return None
            return await resp.text()
    except Exception as exc:
        logger.warning("Ladder fetch error (%s): %s", url, exc)
        return None


async def fetch_ladder_source(
    source: LadderSource,
    snapshot_date: str,
    max_pages: int = 2,
) -> list[PostCard]:
    """Загружает статьи источника через Ladder (парсинг listing-страниц)."""
    cards: list[PostCard] = []
    seen_urls: set[str] = set()

    for path in source.search_paths[:max_pages]:
        url = f"{source.base_url}{path}"
        html = await fetch_ladder_page(url)
        if not html:
            continue

        # Извлекаем статьи из listing-страницы
        articles = _extract_articles_from_listing(html, source.base_url)

        for article in articles[:20]:  # макс 20 статей на секцию
            article_url = article["url"]
            if article_url in seen_urls:
                continue
            seen_urls.add(article_url)

            # ID из URL
            post_id = article_url.rstrip("/").split("/")[-1][:50]

            cards.append(
                PostCard(
                    subreddit=source.name,
                    post_id=post_id,
                    title=article["title"],
                    author=source.name,
                    created_utc=None,
                    score=0,
                    upvote_ratio=0.0,
                    num_comments=0,
                    url=article_url,
                    selftext="",
                    link_flair_text=source.cluster,
                    is_self=False,
                    permalink=article_url,
                    monitoring_type="ladder",
                    snapshot_date=snapshot_date,
                    keyword=source.cluster,
                )
            )

    logger.info("Ladder %s: %d статей", source.name, len(cards))
    return cards


async def fetch_all_ladder(
    snapshot_date: str,
    sources: list[LadderSource] | None = None,
    max_pages: int = 2,
) -> list[PostCard]:
    """Загружает все Ladder-источники."""
    sources = sources or LADDER_SOURCES
    all_cards: list[PostCard] = []
    for source in sources:
        cards = await fetch_ladder_source(source, snapshot_date, max_pages)
        all_cards.extend(cards)
    logger.info("Ladder total: %d статей из %d источников", len(all_cards), len(sources))
    return all_cards
