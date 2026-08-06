"""RSS-адаптер: бесплатные источники (Reuters, BBC, Guardian, TechCrunch, Verge, Ars).

Без paywall, без Ladder. aiohttp + parse RSS/Atom → PostCard (source="rss").
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import PostCard
from .errors import RequestTally, SourceTransportError

logger = logging.getLogger("reddit_compass")

# ── Конфигурация RSS-источников ────────────────────────────────────────────

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class RSSSource:
    """Описание RSS-источника."""

    name: str
    cluster: str
    feeds: list[str]
    country: str = ""


# Кластер 1: Мейнстрим-нарратив
# Кластер 2: Бизнес и финансы
# Кластер 3: Технологии и культура
RSS_SOURCES: list[RSSSource] = [
    # Кластер 1: Мейнстрим
    RSSSource(
        name="bbc",
        cluster="mainstream",
        country="UK",
        feeds=[
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://feeds.bbci.co.uk/news/technology/rss.xml",
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://feeds.bbci.co.uk/sport/rss.xml",
        ],
    ),
    RSSSource(
        name="guardian",
        cluster="mainstream",
        country="UK",
        feeds=[
            "https://www.theguardian.com/world/rss",
            "https://www.theguardian.com/technology/rss",
            "https://www.theguardian.com/business/rss",
            "https://www.theguardian.com/culture/rss",
            "https://www.theguardian.com/sport/rss",
            "https://www.theguardian.com/environment/rss",
        ],
    ),
    # Кластер 2: Бизнес/финансы
    RSSSource(
        name="reuters",
        cluster="business",
        country="US",
        feeds=[
            "https://news.google.com/rss/search?q=site:reuters.com+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:reuters.com+world+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:reuters.com+markets+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:reuters.com+sports+when:1d&hl=en-US&gl=US&ceid=US:en",
        ],
    ),
    # Кластер 3: Tech/культура
    RSSSource(
        name="techcrunch",
        cluster="tech",
        country="US",
        feeds=[
            "https://techcrunch.com/category/artificial-intelligence/feed/",
            "https://techcrunch.com/feed/",
        ],
    ),
    RSSSource(
        name="verge",
        cluster="tech",
        country="US",
        feeds=[
            "https://www.theverge.com/rss/index.xml",
        ],
    ),
    RSSSource(
        name="arstechnica",
        cluster="tech",
        country="US",
        feeds=[
            "https://feeds.arstechnica.com/arstechnica/index",
        ],
    ),
    # SPA-сайты (Ladder не парсит JS → используем Google News RSS)
    RSSSource(
        name="nytimes",
        cluster="mainstream",
        country="US",
        feeds=[
            "https://news.google.com/rss/search?q=site:nytimes.com+home+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:nytimes.com+world+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:nytimes.com+business+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:nytimes.com+technology+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:nytimes.com+arts+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:nytimes.com+sports+when:1d&hl=en-US&gl=US&ceid=US:en",
        ],
    ),
    RSSSource(
        name="washingtonpost",
        cluster="mainstream",
        country="US",
        feeds=[
            "https://news.google.com/rss/search?q=site:washingtonpost.com+world+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:washingtonpost.com+politics+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:washingtonpost.com+business+when:1d&hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=site:washingtonpost.com+sports+when:1d&hl=en-US&gl=US&ceid=US:en",
        ],
    ),
    RSSSource(
        name="usatoday",
        cluster="mainstream",
        country="US",
        feeds=[
            "https://news.google.com/rss/search?q=site:usatoday.com+when:1d&hl=en-US&gl=US&ceid=US:en",
        ],
    ),
    RSSSource(
        name="foxbusiness",
        cluster="business",
        country="US",
        feeds=[
            "https://news.google.com/rss/search?q=site:foxbusiness.com+when:1d&hl=en-US&gl=US&ceid=US:en",
        ],
    ),
    RSSSource(
        name="ft",
        cluster="business",
        country="UK",
        feeds=[
            "https://news.google.com/rss/search?q=site:ft.com+when:1d&hl=en-US&gl=US&ceid=US:en",
        ],
    ),
    RSSSource(
        name="medium",
        cluster="voices",
        country="Global",
        feeds=[
            "https://medium.com/feed/tag/artificial-intelligence",
        ],
    ),
]


def _section_from_feed_url(feed_url: str) -> str:
    text = feed_url.lower()
    for section in (
        "world",
        "business",
        "markets",
        "technology",
        "innovation",
        "science",
        "culture",
        "sport",
        "sports",
        "environment",
        "politics",
        "arts",
        "style",
        "health",
    ):
        if section in text:
            return "sports" if section == "sport" else section
    return "top"


def _strip_html(html: str) -> str:
    text = HTML_TAG_RE.sub(" ", html)
    return text.strip()[:2000]


def _parse_rss_item(item: ET.Element, source_name: str) -> dict[str, Any] | None:
    """Парсит один <item> из RSS 2.0."""
    title_el = item.find("title")
    title = title_el.text.strip() if title_el is not None and title_el.text else ""
    if not title:
        return None

    link_el = item.find("link")
    link = link_el.text.strip() if link_el is not None and link_el.text else ""

    desc_el = item.find("description")
    desc = _strip_html(desc_el.text or "") if desc_el is not None else ""

    pub_el = item.find("pubDate")
    pub_date = pub_el.text.strip() if pub_el is not None and pub_el.text else None

    return {
        "title": title,
        "url": link,
        "description": desc,
        "published": pub_date,
        "source": source_name,
    }


def _parse_atom_entry(entry: ET.Element, source_name: str) -> dict[str, Any] | None:
    """Парсит один <entry> из Atom."""
    title_el = entry.find("atom:title", ATOM_NS)
    title = title_el.text.strip() if title_el is not None and title_el.text else ""
    if not title:
        return None

    link_el = entry.find("atom:link", ATOM_NS)
    link = link_el.get("href", "") if link_el is not None else ""

    summary_el = entry.find("atom:summary", ATOM_NS)
    if summary_el is None:
        summary_el = entry.find("atom:content", ATOM_NS)
    desc = _strip_html(summary_el.text or "") if summary_el is not None else ""

    updated_el = entry.find("atom:updated", ATOM_NS)
    pub_el = entry.find("atom:published", ATOM_NS)
    date = None
    if pub_el is not None and pub_el.text:
        date = pub_el.text.strip()
    elif updated_el is not None and updated_el.text:
        date = updated_el.text.strip()

    return {
        "title": title,
        "url": link,
        "description": desc,
        "published": date,
        "source": source_name,
    }


def parse_feed(xml_text: str, source_name: str) -> list[dict[str, Any]]:
    """Парсит RSS 2.0 или Atom фид."""
    if not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("RSS parse error (%s): %s", source_name, exc)
        return []

    items: list[dict[str, Any]] = []

    # RSS 2.0: <rss><channel><item>
    for item in root.iter("item"):
        parsed = _parse_rss_item(item, source_name)
        if parsed:
            items.append(parsed)

    # Atom: <feed><entry>
    if not items:
        for entry in root.findall("atom:entry", ATOM_NS):
            parsed = _parse_atom_entry(entry, source_name)
            if parsed:
                items.append(parsed)

    return items


def _published_on_date(value: str | None, historical_date: str) -> bool:
    if not value:
        return False
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date().isoformat() == historical_date


def _historical_feed_url(feed_url: str, historical_date: str) -> str:
    """Ask Google News RSS for one UTC day; direct feeds are filtered locally."""
    parts = urlsplit(feed_url)
    if parts.netloc != "news.google.com" or not parts.path.endswith("/rss/search"):
        return feed_url
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    query = re.sub(r"(?:\s|\+)when:\d+d\b", "", params.get("q", "")).strip()
    end = (datetime.strptime(historical_date, "%Y-%m-%d") + timedelta(days=1)).date()
    params["q"] = f"{query} after:{historical_date} before:{end.isoformat()}".strip()
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


async def fetch_rss_source(
    source: RSSSource,
    snapshot_date: str,
    max_items_per_feed: int = 20,
    historical_date: str | None = None,
) -> list[PostCard]:
    """Загружает один RSS-источник (все фиды)."""
    import aiohttp

    cards: list[PostCard] = []
    seen_urls: set[str] = set()
    tally = RequestTally(f"rss:{source.name}")
    headers = {"User-Agent": "reddit-compass/0.2 (trend monitor)"}

    async with aiohttp.ClientSession(headers=headers) as session:
        for feed_url in source.feeds:
            request_url = (
                _historical_feed_url(feed_url, historical_date) if historical_date else feed_url
            )
            tally.attempt()
            try:
                async with session.get(
                    request_url,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("RSS %s (%s): HTTP %d", source.name, feed_url, resp.status)
                        tally.failed(f"{feed_url}: HTTP {resp.status}")
                        continue
                    text = await resp.text()
                    items = parse_feed(text, source.name)
                    section = _section_from_feed_url(feed_url)
                    # Фильтр по дате идёт до обрезания, а не после. При обратном порядке
                    # лимит съедали самые свежие записи, а до нужного дня очередь просто
                    # не доходила: у шести прямых фидов (BBC, Guardian, TechCrunch, Verge,
                    # Ars, Medium) `_historical_feed_url` возвращает URL без изменений,
                    # поэтому историческое восстановление структурно давало почти ноль.
                    if historical_date:
                        items = [
                            item
                            for item in items
                            if _published_on_date(item["published"], historical_date)
                        ]

                    for item in items[:max_items_per_feed]:
                        url = item["url"]
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)

                        cards.append(
                            PostCard(
                                subreddit=source.name,
                                post_id=hashlib.sha256(url.encode()).hexdigest()[:24],
                                title=item["title"],
                                author=source.name,
                                created_utc=item["published"],
                                score=0,
                                upvote_ratio=0.0,
                                num_comments=0,
                                url=url,
                                selftext=item["description"],
                                link_flair_text=source.cluster,
                                is_self=True,
                                permalink=url,
                                monitoring_type="rss",
                                snapshot_date=snapshot_date,
                                keyword=section,
                            )
                        )

            except Exception as exc:
                logger.warning("RSS fetch error (%s): %s", source.name, exc)
                tally.failed(f"{feed_url}: {type(exc).__name__}")
                continue

    logger.info("RSS %s: %d статей", source.name, len(cards))
    # Отвалились все фиды источника — это отказ транспорта, а не тихий день.
    tally.raise_if_total_failure()
    return cards


async def fetch_all_rss(
    snapshot_date: str,
    sources: list[RSSSource] | None = None,
    max_items_per_feed: int = 20,
    historical_date: str | None = None,
) -> list[PostCard]:
    """Загружает все RSS-источники."""
    sources = sources or RSS_SOURCES
    all_cards: list[PostCard] = []
    # Коллектор считает весь RSS одним источником, поэтому падение одного издания не
    # должно ронять день: провал каждого издания здесь лишь попытка. Отказом становится
    # только ситуация, где не ответило ни одно издание.
    tally = RequestTally("rss")
    for source in sources:
        tally.attempt()
        try:
            cards = await fetch_rss_source(
                source,
                snapshot_date,
                max_items_per_feed,
                historical_date,
            )
        except SourceTransportError as exc:
            logger.warning("RSS source %s unavailable: %s", source.name, exc)
            tally.failed(f"{source.name}: all feeds failed")
            continue
        all_cards.extend(cards)
    logger.info("RSS total: %d статей из %d источников", len(all_cards), len(sources))
    tally.raise_if_total_failure()
    return all_cards
