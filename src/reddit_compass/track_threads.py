"""Мониторинг конкретных тредов через Reddit JSON API."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .client import (
    RedditEngine,
    rate_limit_pause,
)
from .models import TrackedThreadState

if TYPE_CHECKING:
    from .config import MonitorConfig

logger = logging.getLogger("reddit_compass")

REDDIT_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|old\.)?reddit\.com/r/(\w+)/comments/(\w+)",
    re.IGNORECASE,
)


def parse_thread_url(url: str) -> tuple[str, str] | None:
    m = REDDIT_URL_RE.search(url)
    if m:
        return m.group(1), m.group(2)
    return None


async def check_thread(
    engine: RedditEngine,
    url: str,
    snapshot_date: str,
    prev_state: TrackedThreadState | None = None,
) -> TrackedThreadState | None:
    parsed = parse_thread_url(url)
    if parsed is None:
        logger.warning("Не удалось распарсить URL треда: %s", url)
        return None

    subreddit_name, post_id = parsed
    json_url = f"https://www.reddit.com/r/{subreddit_name}/comments/{post_id}.json?limit=1"

    data = await engine.fetch_json(json_url)
    if data is None or not isinstance(data, list) or len(data) < 1:
        logger.warning("JSON треда недоступен: %s", url)
        return prev_state

    post_data = data[0].get("data", {}).get("children", [])
    if not post_data:
        return prev_state

    d = post_data[0].get("data", {})
    current_score = d.get("score", 0)
    current_comments = d.get("num_comments", 0)
    title = d.get("title", "")

    new_comments = 0
    score_delta = 0
    if prev_state is not None:
        new_comments = max(0, current_comments - prev_state.num_comments)
        score_delta = current_score - prev_state.score

    return TrackedThreadState(
        url=url,
        post_id=post_id,
        subreddit=subreddit_name,
        title=title,
        score=current_score,
        num_comments=current_comments,
        last_checked=snapshot_date,
        new_comments_since_last=new_comments,
        score_delta=score_delta,
    )


def load_previous_states(state_file: Path) -> dict[str, TrackedThreadState]:
    states: dict[str, TrackedThreadState] = {}
    if not state_file.exists():
        return states
    for line in state_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            states[raw["url"]] = TrackedThreadState(**raw)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Пропущена битая строка в %s: %s", state_file, exc)
    return states


async def track_all_threads(
    config: MonitorConfig,
    snapshot_date: str,
    state_file: Path | None = None,
) -> list[TrackedThreadState]:
    prev_states: dict[str, TrackedThreadState] = {}
    if state_file is not None:
        prev_states = load_previous_states(state_file)

    engine = RedditEngine(stealth=config.settings.stealth)
    await engine.start()
    results: list[TrackedThreadState] = []
    try:
        for url in config.tracked_threads:
            prev = prev_states.get(url)
            state = await check_thread(engine, url, snapshot_date, prev)
            if state is not None:
                results.append(state)
            await rate_limit_pause(config.settings.stealth)
    finally:
        await engine.close()

    if not results and config.tracked_threads:
        logger.info("JSON API недоступен для track — возвращены предыдущие состояния")
        return list(prev_states.values())

    logger.info("Tracked threads: проверено %d из %d", len(results), len(config.tracked_threads))
    return results
