"""LinkedIn-источник: публичные посты и статьи авторов, гостевой доступ без логина.

Модель доступа проверена спайком 2026-08-10 (VPS + Mac):

- Профили и ленты активности закрыты от гостей authwall'ом — не используются.
- Прямые URL постов ``/posts/<vanity>_…-activity-<id>`` отдаются гостю по голому
  HTTP (200). JSON-LD ``SocialMediaPosting`` даёт headline, превью текста
  (``articleBody``, ~260 символов), точный ``datePublished``, реакции
  (``interactionStatistic``), ``commentCount`` и первые комментарии. Полный текст
  закрыт (``hasPart.isAccessibleForFree = False``), поэтому scope — excerpt.
- Discovery: Brave Search HTML (``site:linkedin.com/posts <slug>``); свежесть
  кандидата определяется из snowflake activity-id (``id >> 22`` = миллисекунды
  UTC) без загрузки страницы. DDG/Bing/Mojeek/Ecosia с VPS душатся — не нужны.
- Статьи ``/pulse/`` тоже доступны гостю; из найденных оставляем только те,
  где JSON-LD ``author`` совпадает с нашим автором (поиск выдаёт и чужие
  статьи об авторе).

Границы: read-only, без credentials, без обхода authwall; на 429 — пауза
и backoff, давление не наращиваем. Выход: PostCard (source="linkedin") →
linkedin.jsonl.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from ..models import CommentCard, PostCard, iso_utc
from .errors import RequestTally

logger = logging.getLogger("reddit_compass")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BRAVE_SEARCH_URL = "https://search.brave.com/search?q={query}"

REQUEST_PAUSE = 4.0  # между загрузками страниц (правило проекта)
SEARCH_PAUSE = 12.0  # между поисковыми запросами: квота Brave на IP ограничена
MAX_RETRIES = 2  # на HTTP 429 (правило проекта)
RETRY_PAUSE = 10.0
POSTS_PER_AUTHOR = 8
ARTICLE_CANDIDATES_PER_AUTHOR = 4
# Поисковый индекс запаздывает на недели-месяцы (спайк 2026-08-10: самые свежие
# индексированные посты авторов — весна 2026), поэтому окно шире месяца.
DEFAULT_DAYS_BACK = 180
TOP_COMMENTS_PER_POST = 5

LINKEDIN_URL_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/[\w/\-%]+")
# Третья группа — хвост после activity-id (например, `--nLq`): LinkedIn требует
# его в URL поста, отбрасывать нельзя.
POST_URL_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/posts/([\w\-%]+)-activity-(\d{10,})([\w\-]*)"
)
PULSE_URL_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/pulse/[\w\-]+")
JSONLD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
META_OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)="og:(title|description)"[^>]+content="([^"]*)"', re.I
)

# Слаги подтверждены спайком 2026-08-10: и discovery, и гостевые страницы постов.
# Если смена слага ломает discovery — обновить здесь, а не «чинить» обходом.


@dataclass(frozen=True)
class LinkedinAuthor:
    """Автор, чьи публичные посты собирает адаптер."""

    name: str
    slug: str


LINKEDIN_AUTHORS: list[LinkedinAuthor] = [
    LinkedinAuthor(name="Andrew Ng", slug="andrewyng"),
    LinkedinAuthor(name="Fei-Fei Li", slug="fei-fei-li-4541247"),
    LinkedinAuthor(name="Allie K. Miller", slug="alliekmiller"),
    LinkedinAuthor(name="Cassie Kozyrkov", slug="kozyrkov"),
]

# Гарантированный минимум на случай деградации поискового discovery: прямые URL
# не требуют поисковика (проверены спайком 2026-08-10). Фильтр свежести к ним
# применяется как ко всем: устаревшие seed'ы просто выпадут из окна.
SEED_POST_URLS: dict[str, list[str]] = {
    "kozyrkov": [
        "https://www.linkedin.com/posts/kozyrkov_aafa-decisionintelligence-aileadership-activity-7454499621982281728-Oo3Q",
        "https://www.linkedin.com/posts/kozyrkov_decisionintelligence-aileadership-responsibleai-activity-7444715319836868608-BWc1",
        "https://www.linkedin.com/posts/kozyrkov_aafa-aileadership-decisionintelligence-activity-7424059015716069376-WZoc",
    ],
}


# ── Разбор структуры страниц ───────────────────────────────────────────────


def canonicalize_linkedin_url(url: str) -> str:
    """Приводит URL к https://www.linkedin.com/… (региональные поддомены → www)."""
    parts = urlsplit(url)
    return urlunsplit(("https", "www.linkedin.com", parts.path, "", ""))


def activity_timestamp(activity_id: str) -> datetime | None:
    """Время активности из snowflake-id: миллисекунды UTC в старших битах (>>22).

    Проверено на JSON-LD ``datePublished``: расхождение — минуты (activity
    создаётся чуть позже публикации), для фильтра свежести этого достаточно.
    """
    try:
        ms = int(activity_id) >> 22
    except ValueError:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def extract_post_urls(html: str, slug: str) -> list[tuple[str, str, datetime | None]]:
    """URL постов автора из поисковой выдачи.

    Возвращает кортежи (canonical_url, activity_id, timestamp); чужие посты
    (упоминания автора) отсекаются по vanity-слагу в URL.
    """
    found: dict[str, tuple[str, datetime | None]] = {}
    for seg, activity_id, suffix in POST_URL_RE.findall(html):
        if not seg.lower().startswith(f"{slug.lower()}_") and seg.lower() != slug.lower():
            continue
        url = canonicalize_linkedin_url(
            f"https://www.linkedin.com/posts/{seg}-activity-{activity_id}{suffix}"
        )
        found.setdefault(url, (activity_id, activity_timestamp(activity_id)))
    return [(url, aid, ts) for url, (aid, ts) in found.items()]


def _jsonld_blocks(html: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for m in JSONLD_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            blocks.append(data)
    return blocks


def _og_value(html: str, key: str) -> str:
    for m in META_OG_RE.finditer(html):
        if m.group(1).lower() == key:
            return m.group(2).strip()
    return ""


def _reaction_count(data: dict[str, Any]) -> int:
    stats = data.get("interactionStatistic")
    if isinstance(stats, dict):
        stats = [stats]
    for stat in stats or []:
        if isinstance(stat, dict) and "LikeAction" in str(stat.get("interactionType", "")):
            try:
                return int(stat.get("userInteractionCount", 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _comment_cards(data: dict[str, Any]) -> list[CommentCard]:
    comments = data.get("comment")
    if isinstance(comments, dict):
        comments = [comments]
    cards: list[CommentCard] = []
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("text", "")).strip()
        author = comment.get("author")
        author_name = author.get("name", "") if isinstance(author, dict) else str(author or "")
        if not body and not author_name:
            continue
        fingerprint = f"{author_name}|{comment.get('datePublished', '')}|{body[:64]}"
        cards.append(
            CommentCard(
                comment_id=hashlib.sha1(fingerprint.encode()).hexdigest()[:12],
                author=author_name,
                score=_reaction_count(comment),
                body=body[:2000],
                created_utc=str(comment.get("datePublished") or "") or None,
            )
        )
        if len(cards) >= TOP_COMMENTS_PER_POST:
            break
    return cards


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# LinkedIn меняет JSON-LD тип по виду поста: текст — SocialMediaPosting,
# видео — VideoObject (автор в creator, текст в description), картинка —
# ImageObject. Принимаем все постовые типы.
_POST_JSONLD_TYPES = frozenset(
    {"SocialMediaPosting", "Article", "BlogPosting", "VideoObject", "ImageObject"}
)


def parse_post_card(
    html: str,
    url: str,
    fallback_author: str,
    slug: str,
    snapshot_date: str,
) -> PostCard | None:
    """PostCard из гостевой страницы поста (JSON-LD любого постового типа)."""
    data = next((b for b in _jsonld_blocks(html) if b.get("@type") in _POST_JSONLD_TYPES), None)
    if data is None:
        return None

    headline = str(data.get("headline") or data.get("name") or "").strip()
    preview = str(data.get("articleBody") or "").strip()
    text_candidates = [
        preview,
        str(data.get("text") or "").strip(),
        str(data.get("description") or "").strip(),
        _og_value(html, "description"),
    ]
    selftext = max(text_candidates, key=len)[:5000]
    title = headline or _og_value(html, "title")[:200]
    if not title and selftext:
        title = selftext[:120]
    if not title:
        return None

    author = data.get("author") or data.get("creator")
    author_name = author.get("name", "") if isinstance(author, dict) else str(author or "")

    m = POST_URL_RE.search(url)
    activity_id = m.group(2) if m else hashlib.sha256(url.encode()).hexdigest()[:24]

    return PostCard(
        subreddit="linkedin",
        post_id=activity_id,
        title=title,
        author=author_name or fallback_author,
        created_utc=iso_utc(_parse_datetime(str(data.get("datePublished") or ""))),
        score=_reaction_count(data),
        upvote_ratio=0.0,
        num_comments=int(data.get("commentCount") or 0),
        url=canonicalize_linkedin_url(url),
        selftext=selftext,
        link_flair_text=None,
        is_self=True,
        permalink=urlsplit(canonicalize_linkedin_url(url)).path,
        monitoring_type="linkedin",
        snapshot_date=snapshot_date,
        keyword=slug,
        top_comments=_comment_cards(data),
    )


def parse_article_card(
    html: str,
    url: str,
    snapshot_date: str,
    slug: str,
) -> PostCard | None:
    """PostCard из гостевой страницы статьи /pulse/ (JSON-LD Article)."""
    data = next((b for b in _jsonld_blocks(html) if b.get("@type") == "Article"), None)
    if data is None:
        return None

    title = str(data.get("headline") or "").strip() or _og_value(html, "title")[:200]
    if not title:
        return None
    preview = str(data.get("articleBody") or "").strip()
    selftext = max([preview, _og_value(html, "description")], key=len)[:5000]

    author = data.get("author")
    author_name = author.get("name", "") if isinstance(author, dict) else str(author or "")
    canonical = canonicalize_linkedin_url(url)

    return PostCard(
        subreddit="linkedin",
        post_id=f"pulse-{hashlib.sha256(canonical.encode()).hexdigest()[:20]}",
        title=title,
        author=author_name,
        created_utc=iso_utc(_parse_datetime(str(data.get("datePublished") or ""))),
        score=_reaction_count(data),
        upvote_ratio=0.0,
        num_comments=int(data.get("commentCount") or 0),
        url=canonical,
        selftext=selftext,
        link_flair_text=None,
        is_self=True,
        permalink=urlsplit(canonical).path,
        monitoring_type="linkedin",
        snapshot_date=snapshot_date,
        keyword=slug,
    )


def article_matches_author(card: PostCard, author: LinkedinAuthor) -> bool:
    """Поиск выдаёт и чужие статьи об авторе — оставляем только авторские."""
    return card.author.strip().lower() == author.name.strip().lower()


# ── Discovery: Brave Search HTML ───────────────────────────────────────────


async def _get_with_retries(
    session: Any,
    url: str,
    tally: RequestTally,
    label: str,
) -> tuple[int, str] | None:
    """GET с retry на 429 (правило проекта). 999 и прочее — отказ без retry."""
    import aiohttp

    for attempt in range(MAX_RETRIES + 1):
        tally.attempt()
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                if resp.status == 429 and attempt < MAX_RETRIES:
                    pause = RETRY_PAUSE * (attempt + 1)
                    logger.warning(
                        "LinkedIn %s: 429, retry %d/%d (пауза %.0fс)",
                        label,
                        attempt + 1,
                        MAX_RETRIES,
                        pause,
                    )
                    await asyncio.sleep(pause)
                    continue
                if resp.status != 200:
                    tally.failed(f"{label}: HTTP {resp.status}")
                    return None
                return resp.status, await resp.text(errors="replace")
        except Exception as exc:
            tally.failed(f"{label}: {type(exc).__name__}")
            return None
    tally.failed(f"{label}: HTTP 429 после retries")
    return None


async def search_brave(session: Any, query: str, tally: RequestTally) -> list[str]:
    """HTML-выдача Brave Search; пустой список при 429/блоке (tally помнит отказ)."""
    result = await _get_with_retries(
        session, BRAVE_SEARCH_URL.format(query=quote(query)), tally, f"search {query!r}"
    )
    if result is None:
        return []
    _, html = result
    return LINKEDIN_URL_RE.findall(html)


def filter_fresh_candidates(
    entries: list[tuple[str, str, datetime | None]],
    cutoff: datetime,
    limit: int,
) -> list[str]:
    """URL постов свежее cutoff, новые сверху, без дублей.

    Кандидаты без расшифрованного timestamp не отбрасываются — финальное
    слово за JSON-LD ``datePublished`` при загрузке, — но сортируются после
    датированных. Чистая функция: фильтр свежести тестируется без сети.
    """
    found: dict[str, datetime | None] = {}
    for url, _, ts in entries:
        if ts is not None and ts < cutoff:
            continue
        found.setdefault(url, ts)
    ordered = sorted(
        found.items(), key=lambda kv: kv[1] or datetime.min.replace(tzinfo=UTC), reverse=True
    )
    return [url for url, _ in ordered[:limit]]


async def discover_author_posts(
    session: Any,
    author: LinkedinAuthor,
    cutoff: datetime,
    tally: RequestTally,
) -> list[str]:
    """Прямые URL свежих постов автора: поиск по слагу, по имени — только если
    слаговый запрос ничего не дал (квота поисковика ограничена)."""
    entries: list[tuple[str, str, datetime | None]] = []

    for query in (
        f"site:linkedin.com/posts {author.slug}",
        f'site:linkedin.com/posts "{author.name}"',
    ):
        if entries:
            break
        html_blob = " ".join(await search_brave(session, query, tally))
        entries.extend(extract_post_urls(html_blob, author.slug))
        await asyncio.sleep(SEARCH_PAUSE + random.uniform(0, 3))

    return filter_fresh_candidates(entries, cutoff, POSTS_PER_AUTHOR)


async def discover_author_articles(
    session: Any,
    author: LinkedinAuthor,
    tally: RequestTally,
) -> list[str]:
    """Кандидаты статей /pulse/ (фильтр по авторству — на стадии загрузки)."""
    query = f'site:linkedin.com/pulse "{author.name}"'
    html_blob = " ".join(await search_brave(session, query, tally))
    await asyncio.sleep(SEARCH_PAUSE + random.uniform(0, 3))
    pulse = sorted(set(PULSE_URL_RE.findall(html_blob)))
    return [canonicalize_linkedin_url(u) for u in pulse[:ARTICLE_CANDIDATES_PER_AUTHOR]]


# ── Основной вход ──────────────────────────────────────────────────────────


async def fetch_linkedin_authors(
    snapshot_date: str = "",
    authors: list[LinkedinAuthor] | None = None,
    days_back: int = DEFAULT_DAYS_BACK,
    include_articles: bool = True,
    seed_post_urls: dict[str, list[str]] | None = None,
) -> list[PostCard]:
    """Собирает публичные посты (и статьи) авторов гостевым доступом.

    Args:
        snapshot_date: дата снапшота YYYY-MM-DD (по умолчанию сегодня UTC).
        authors: список авторов (по умолчанию LINKEDIN_AUTHORS).
        days_back: окно свежести для фильтра по activity-id.
        include_articles: собирать ли статьи /pulse/ (авторство проверяется).
        seed_post_urls: прямые URL постов по слагу — гарантированный минимум,
            если поисковый discovery деградировал (429/блок); по умолчанию
            SEED_POST_URLS. Явный пустой dict отключает seeds.
    """
    import aiohttp

    authors = authors or LINKEDIN_AUTHORS
    seeds = SEED_POST_URLS if seed_post_urls is None else seed_post_urls
    date = snapshot_date or datetime.now(tz=UTC).strftime("%Y-%m-%d")
    snapshot_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    cutoff = snapshot_dt - timedelta(days=days_back)
    tally = RequestTally("linkedin")
    cards: list[PostCard] = []
    seen: set[str] = set()

    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    async with aiohttp.ClientSession(headers=headers) as session:
        for author in authors:
            discovered = await discover_author_posts(session, author, cutoff, tally)
            seed_entries: list[tuple[str, str, datetime | None]] = []
            for url in seeds.get(author.slug, []):
                canonical = canonicalize_linkedin_url(url)
                m = POST_URL_RE.search(canonical)
                seed_entries.append(
                    (
                        canonical,
                        m.group(2) if m else "",
                        activity_timestamp(m.group(2)) if m else None,
                    )
                )
            seed_urls = filter_fresh_candidates(seed_entries, cutoff, POSTS_PER_AUTHOR)
            post_urls = discovered + [u for u in seed_urls if u not in discovered]
            for url in post_urls[:POSTS_PER_AUTHOR]:
                result = await _get_with_retries(session, url, tally, f"post {url}")
                if result is None:
                    continue
                _, html = result
                if "authwall" in html[:3000].lower():
                    logger.warning("LinkedIn пост %s закрыт authwall — пропускаем", url)
                    continue
                card = parse_post_card(html, url, author.name, author.slug, date)
                if card is not None and card.post_id not in seen:
                    seen.add(card.post_id)
                    cards.append(card)
                await asyncio.sleep(REQUEST_PAUSE + random.uniform(0, 2))

            if include_articles:
                for url in await discover_author_articles(session, author, tally):
                    result = await _get_with_retries(session, url, tally, f"article {url}")
                    if result is None:
                        continue
                    _, html = result
                    card = parse_article_card(html, url, date, author.slug)
                    if (
                        card is not None
                        and article_matches_author(card, author)
                        and card.post_id not in seen
                    ):
                        seen.add(card.post_id)
                        cards.append(card)
                    await asyncio.sleep(REQUEST_PAUSE + random.uniform(0, 2))

            logger.info(
                "LinkedIn %s: %d материалов собрано",
                author.name,
                sum(1 for c in cards if c.keyword == author.slug),
            )

    # Пустой список после того, как отвалились все запросы, — не пустой день.
    tally.raise_if_total_failure()
    logger.info("LinkedIn total: %d постов/статей у %d авторов", len(cards), len(authors))
    return cards


# ── Markdown-дайджест ──────────────────────────────────────────────────────


def render_linkedin_report(cards: list[PostCard], snapshot_date: str) -> str:
    """Markdown-дайджест «почитать»: авторы → посты с метриками и ссылками."""
    lines: list[str] = [
        "# LinkedIn-дайджест",
        "",
        f"> Snapshot: {snapshot_date}. Гостевой доступ, только публичные посты.",
        "> Полный текст постов закрыт LinkedIn (excerpt ~260 символов); метрики точные.",
        "",
    ]
    by_author: dict[str, list[PostCard]] = {}
    for card in cards:
        by_author.setdefault(card.author or card.keyword or "unknown", []).append(card)

    if not cards:
        lines.append("Материалы не собраны (discovery или доступ деградировали).")
        return "\n".join(lines)

    for author in sorted(by_author):
        author_cards = sorted(by_author[author], key=lambda c: c.created_utc or "", reverse=True)
        lines.append(f"## {author} ({len(author_cards)})")
        lines.append("")
        for card in author_cards:
            date = (card.created_utc or "")[:10] or "?"
            kind = "статья" if card.post_id.startswith("pulse-") else "пост"
            lines.append(f"### {card.title}")
            lines.append("")
            lines.append(
                f"{date} · {kind} · реакций: {card.score} · комментариев: {card.num_comments} "
                f"· [ссылка]({card.url})"
            )
            if card.selftext:
                lines.append("")
                excerpt = card.selftext[:600]
                lines.append(f"> {excerpt.replace(chr(10), ' ')}")
            for comment in card.top_comments[:2]:
                lines.append("")
                lines.append(f"💬 **{comment.author}**: {comment.body[:200]}")
            lines.append("")
        lines.append("")
    return "\n".join(lines)
