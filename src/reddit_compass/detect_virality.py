"""Детекция виральности: cross-posting, резкий рост score, присутствие в нескольких сабреддитах."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from .models import PostCard, ViralitySignal

if TYPE_CHECKING:
    from .config import MonitorConfig

logger = logging.getLogger("reddit_compass")


def detect_virality(
    cards: list[PostCard],
    config: MonitorConfig,
    snapshot_date: str,
) -> list[ViralitySignal]:
    """Анализирует собранные посты и возвращает сигналы виральности."""
    signals: list[ViralitySignal] = []
    settings = config.settings

    # 1. Crosspost detection: посты с crosspost_parents
    for card in cards:
        if len(card.crosspost_parents) >= settings.virality_crosspost_min:
            signals.append(
                ViralitySignal(
                    post_id=card.post_id,
                    title=card.title,
                    original_subreddit=card.subreddit,
                    crossposted_to=card.crosspost_parents,
                    total_score=card.score,
                    total_comments=card.num_comments,
                    signal_type="crosspost",
                    detected_at=snapshot_date,
                    url=card.full_url,
                )
            )

    # 2. Score surge: посты с очень высоким score
    for card in cards:
        if card.score >= settings.virality_score_threshold:
            signals.append(
                ViralitySignal(
                    post_id=card.post_id,
                    title=card.title,
                    original_subreddit=card.subreddit,
                    crossposted_to=card.crosspost_parents,
                    total_score=card.score,
                    total_comments=card.num_comments,
                    signal_type="score_surge",
                    detected_at=snapshot_date,
                    url=card.full_url,
                )
            )

    # 3. Multi-subreddit: один и тот же заголовок/URL в разных сабреддитах
    title_map: dict[str, list[PostCard]] = defaultdict(list)
    for card in cards:
        normalized = " ".join(card.title.casefold().split())
        if len(normalized) > 20:
            title_map[normalized].append(card)

    for group in title_map.values():
        subreddits = {c.subreddit.lower() for c in group}
        if len(subreddits) >= settings.virality_crosspost_min:
            best = max(group, key=lambda c: c.score)
            signals.append(
                ViralitySignal(
                    post_id=best.post_id,
                    title=best.title,
                    original_subreddit=best.subreddit,
                    crossposted_to=sorted(subreddits),
                    total_score=sum(c.score for c in group),
                    total_comments=sum(c.num_comments for c in group),
                    signal_type="multi_subreddit",
                    detected_at=snapshot_date,
                    url=best.full_url,
                )
            )

    # Dedup по (post_id, signal_type)
    seen: set[tuple[str, str]] = set()
    deduped: list[ViralitySignal] = []
    for sig in signals:
        key = (sig.post_id, sig.signal_type)
        if key not in seen:
            seen.add(key)
            deduped.append(sig)

    logger.info("Virality: обнаружено %d сигналов", len(deduped))
    return deduped
