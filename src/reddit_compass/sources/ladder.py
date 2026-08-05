"""Ladder-адаптер: paywall-источники через self-hosted Ladder proxy.

Источники: NYT, WaPo, FT, Wired, Medium, Time, USA Today, Fox, New Yorker, VF, AmBanker.
Ladder: http://localhost:8080 (на HostKey). Правила: ladder-rules (per-domain).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit

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


def historical_google_news_url(source: LadderSource, historical_date: str) -> str:
    """Return a Google News RSS query bounded to one UTC day for one domain."""
    host = urlsplit(source.base_url).netloc.removeprefix("www.")
    end = (datetime.strptime(historical_date, "%Y-%m-%d") + timedelta(days=1)).date()
    params = {
        "q": f"site:{host} after:{historical_date} before:{end.isoformat()}",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    return urlunsplit(("https", "news.google.com", "/rss/search", urlencode(params), ""))


def historical_google_news_cards(
    source: LadderSource,
    xml_text: str,
    *,
    snapshot_date: str,
    historical_date: str,
) -> list[PostCard]:
    """Turn one date-bounded Google News feed into explicit historical Ladder facts."""
    # Google News exposes the publication time in RSS.  Keep the local date filter as
    # a second guard: query syntax is provider-controlled and must not be trusted alone.
    from .rss import _published_on_date, parse_feed

    cards: list[PostCard] = []
    seen_urls: set[str] = set()
    for item in parse_feed(xml_text, source.name):
        published = item["published"]
        if not _published_on_date(published, historical_date):
            continue
        url = str(item["url"])
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        cards.append(
            PostCard(
                subreddit=source.name,
                post_id=hashlib.sha256(url.encode()).hexdigest()[:24],
                title=str(item["title"]),
                author=source.name,
                created_utc=published,
                score=0,
                upvote_ratio=0.0,
                num_comments=0,
                url=url,
                selftext=str(item["description"]),
                link_flair_text=source.cluster,
                is_self=False,
                permalink=url,
                monitoring_type="ladder_historical_google_news",
                snapshot_date=snapshot_date,
                keyword=source.cluster,
            )
        )
    return cards


async def fetch_historical_ladder_google_news(
    snapshot_date: str,
    historical_date: str,
    sources: list[LadderSource] | None = None,
) -> list[PostCard]:
    """Recover historic Ladder coverage without relabelling a current listing.

    Ladder itself can only fetch current section listings.  Google News RSS provides
    a public, date-bounded discovery surface for the same source domains; the output
    deliberately carries a distinct ``monitoring_type`` so it is auditable downstream.
    """
    import aiohttp

    cards: list[PostCard] = []
    headers = {"User-Agent": "reddit-compass/0.2 (trend monitor)"}
    async with aiohttp.ClientSession(headers=headers) as session:
        for source in sources or LADDER_SOURCES:
            try:
                async with session.get(
                    historical_google_news_url(source, historical_date),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    if response.status != 200:
                        logger.warning(
                            "Historical Ladder %s: Google News HTTP %d",
                            source.name,
                            response.status,
                        )
                        continue
                    cards.extend(
                        historical_google_news_cards(
                            source,
                            await response.text(),
                            snapshot_date=snapshot_date,
                            historical_date=historical_date,
                        )
                    )
            except Exception as exc:
                logger.warning("Historical Ladder %s failed: %s", source.name, exc)
    logger.info(
        "Historical Ladder Google News %s: %d articles from %d sources",
        historical_date,
        len(cards),
        len(sources or LADDER_SOURCES),
    )
    return cards


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
    historical_date: str | None = None,
) -> list[PostCard]:
    """Загружает все Ladder-источники."""
    if historical_date:
        return await fetch_historical_ladder_google_news(
            snapshot_date,
            historical_date,
            sources=sources,
        )
    sources = sources or LADDER_SOURCES
    all_cards: list[PostCard] = []
    for source in sources:
        cards = await fetch_ladder_source(source, snapshot_date, max_pages)
        all_cards.extend(cards)
    logger.info("Ladder total: %d статей из %d источников", len(all_cards), len(sources))
    return all_cards
