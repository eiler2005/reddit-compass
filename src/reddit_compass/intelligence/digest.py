"""Ежедневный дайджест: один сбор данных, три способа его показать.

Заготовки в ``notify.py`` строили дайджест из ``PostCard`` — старой модели,
где есть только Reddit: «73 поста, 19 сабреддитов, топ-5 по score». Это лента
популярного, а не компас: в ней нет ни сюжетов, ни трендов, ни главного —
разрыва между тем, что обсуждают люди, и тем, о чём пишут медиа.

Здесь дайджест собирается из **опубликованного выпуска**, то есть из тех же
данных, что показывает ``/today``. Благодаря этому страница, письмо и сообщение
в Telegram не могут разойтись: расходятся не рендеры, а источники, а источник
тут один.

Структура намеренно плоская и сериализуемая: её одинаково удобно отдать в Jinja,
свернуть в текст и сохранить в JSONL рядом с остальными уведомлениями.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Сколько объектов каждого рода попадает в дайджест. Письмо, которое читают
# за завтраком, не должно требовать прокрутки: это выжимка, а не витрина.
MAX_TRENDS = 5
MAX_GAPS = 5
MAX_READING = 7
MAX_REDDIT = 5


@dataclass(frozen=True)
class DigestItem:
    """Одна строка дайджеста: заголовок, ссылка и одна поясняющая фраза."""

    title: str
    url: str = ""
    note: str = ""
    meta: str = ""


@dataclass
class Digest:
    """Готовый дайджест за день."""

    date: str
    headline: str
    preview: bool = False
    publication_id: str = ""
    item_count: int = 0
    source_count: int = 0
    story_count: int = 0
    trend_count: int = 0
    cross_source_count: int = 0
    trends: list[DigestItem] = field(default_factory=list)
    gaps: list[DigestItem] = field(default_factory=list)
    reading: list[DigestItem] = field(default_factory=list)
    reddit: list[DigestItem] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Есть ли вообще о чём писать.

        Пустой дайджест лучше не отправлять вовсе, чем отправить бодрый
        заголовок и ни одной строки под ним.
        """
        return not (self.trends or self.gaps or self.reading or self.reddit)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clip(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _headline(date: str, cross_source: int, gaps: int) -> str:
    """Заголовок описывает день, а не пересказывает счётчики.

    «73 поста» ничего не сообщает: важно, нашлось ли подтверждённое разными
    источниками и есть ли то, о чём молчат СМИ.
    """
    if gaps and cross_source:
        return (
            f"{date}: {cross_source} подтверждено разными источниками, "
            f"{gaps} обсуждают только на Reddit"
        )
    if gaps:
        return f"{date}: {gaps} сюжетов живут только на Reddit"
    if cross_source:
        return f"{date}: {cross_source} сюжетов подтверждены разными источниками"
    return f"{date}: спокойный день, ярких расхождений не видно"


def build_digest(
    *,
    date: str,
    dashboard: dict[str, Any],
    trends: list[dict[str, Any]],
    reading: list[dict[str, Any]],
    reddit_new: list[dict[str, Any]],
    gap_signals: list[dict[str, Any]] | None = None,
    preview: bool = False,
    publication_id: str = "",
    base_url: str = "",
) -> Digest:
    """Собрать дайджест из уже загруженных кусков опубликованного выпуска.

    Функция намеренно ничего не читает сама: и веб-страница, и CLI получают эти
    куски одними и теми же helper'ами, поэтому сборщик остаётся чистым и легко
    проверяется без базы.
    """
    gap_signals = gap_signals or []

    def absolute(url: str) -> str:
        """Ссылки в письме обязаны быть абсолютными: относительный путь там мёртв."""
        if not url or not base_url:
            return url
        return f"{base_url.rstrip('/')}{url}" if url.startswith("/") else url

    trend_items = [
        DigestItem(
            title=_clip(trend.get("title"), 120),
            url=absolute(str(trend.get("url") or "")),
            note=_clip(trend.get("pattern"), 180),
            meta=" · ".join(
                part
                for part in (
                    str(trend.get("lifecycle_label") or ""),
                    str(trend.get("source_scope_label") or ""),
                    f"{trend.get('source_count')} источников" if trend.get("source_count") else "",
                )
                if part
            ),
        )
        for trend in trends[:MAX_TRENDS]
    ]

    gap_items = [
        DigestItem(
            title=_clip(signal.get("title"), 120),
            url=str(signal.get("discussion_url") or ""),
            note="",
            meta=f"r/{signal.get('subreddit')}" if signal.get("subreddit") else "",
        )
        for signal in gap_signals[:MAX_GAPS]
    ]

    reading_items = [
        DigestItem(
            title=_clip(item.get("title"), 120),
            url=str(item.get("primary_url") or ""),
            note=_clip(item.get("summary_ru") or item.get("excerpt"), 180),
            meta=str(item.get("provider") or ""),
        )
        for item in reading[:MAX_READING]
    ]

    reddit_items = [
        DigestItem(
            title=_clip(post.get("title"), 120),
            url=str(post.get("discussion_url") or ""),
            note="",
            meta=" · ".join(
                part
                for part in (
                    f"r/{post.get('subreddit')}" if post.get("subreddit") else "",
                    str(post.get("signal_type_label") or ""),
                )
                if part
            ),
        )
        for post in reddit_new[:MAX_REDDIT]
    ]

    cross_source = int(dashboard.get("cross_source_trend_count") or 0)

    return Digest(
        date=date,
        headline=_headline(date, cross_source, len(gap_items)),
        preview=preview,
        publication_id=publication_id,
        item_count=int(dashboard.get("item_count") or 0),
        source_count=int(dashboard.get("source_count") or 0),
        story_count=int(dashboard.get("source_cluster_count") or 0),
        trend_count=int(dashboard.get("trend_count") or 0),
        cross_source_count=cross_source,
        trends=trend_items,
        gaps=gap_items,
        reading=reading_items,
        reddit=reddit_items,
    )
