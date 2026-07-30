"""Reddit Pulse: Reddit-native trendwatching layer.

Separate from news scoring. Answers:
- What are people discussing?
- Where is pain, fear, desire, backlash, meme/culture shift?
- What is gaining velocity before mainstream notices?
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from .models import ContentItem

SignalType = Literal[
    "news_link",
    "discussion",
    "question",
    "pain_point",
    "complaint",
    "meme_culture",
    "product_request",
    "career_labor",
    "market_investing",
    "policy_politics",
    "ai_capability",
    "ai_risk",
    "ai_tools",
    "other",
]

# Deterministic signal type hints
_QUESTION_PATTERNS = re.compile(
    r"\b(how do i|anyone else|what are you|what do you|has anyone|is it just me|"
    r"what's the best|recommend|looking for|need help|advice)\b",
    re.IGNORECASE,
)
_PAIN_PATTERNS = re.compile(
    r"\b(hate|broken|can't|cannot|burnout|laid off|fired|toxic|nightmare|"
    r"worst|frustrat|annoying|useless|scam|rip.?off|terrible|horrible)\b",
    re.IGNORECASE,
)
_AI_CAPABILITY_PATTERNS = re.compile(
    r"\b(gpt-?[45]|claude|gemini|llama|mistral|benchmark|sota|state.of.the.art|"
    r"outperform|surpass|beats?|achieves?|reasoning|coding|math)\b",
    re.IGNORECASE,
)
_AI_RISK_PATTERNS = re.compile(
    r"\b(hallucin|bias|discriminat|deepfake|misinformation|jailbreak|"
    r"prompt injection|safety|alignment|existential|rogue|escape)",
    re.IGNORECASE,
)
_AI_TOOLS_PATTERNS = re.compile(
    r"\b(cursor|copilot|windsurf|devin|bolt|v0|lovable|replit|vibe cod|"
    r"show hn|built with|made with|using ai|ai.?powered)\b",
    re.IGNORECASE,
)
_MEME_PATTERNS = re.compile(
    r"\b(meme|lol|lmao|haha|funny|joke|satire|parody|shitpost)\b",
    re.IGNORECASE,
)
_COMPLAINT_PATTERNS = re.compile(
    r"\b(complaint|refund|overcharged|billing|customer service|outage|down for me|"
    r"scammed|charged twice|no response|cancelled my|won't let me)\b",
    re.IGNORECASE,
)
_PRODUCT_REQUEST_PATTERNS = re.compile(
    r"\b(wish there was|someone should build|why is there no|alternative to|"
    r"looking for (an? )?(app|tool|service)|is there a tool|feature request|"
    r"recommend( me)? (an? )?(app|tool|library))\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'_-]*", re.IGNORECASE)

_CAREER_SUBS = {
    "cscareerquestions",
    "careerguidance",
    "jobs",
    "recruitinghell",
    "antiwork",
    "overemployed",
    "teachers",
    "professors",
}
_MARKET_SUBS = {"wallstreetbets", "stocks", "investing", "finance", "economy"}
_POLICY_SUBS = {"politics", "geopolitics", "worldnews", "news", "privacy"}


def classify_signal_type(item: ContentItem) -> SignalType:
    """Deterministic signal type classification."""
    text = f"{item.title} {item.excerpt}".lower()
    sub = item.source_section.lower()

    # Subreddit-based classification
    if sub in _CAREER_SUBS:
        return "career_labor"
    if sub in _MARKET_SUBS:
        return "market_investing"
    if sub in _POLICY_SUBS:
        return "policy_politics"

    # Pattern-based classification
    if _QUESTION_PATTERNS.search(text):
        return "question"
    if _PRODUCT_REQUEST_PATTERNS.search(text):
        return "product_request"
    if _PAIN_PATTERNS.search(text):
        return "pain_point"
    if _COMPLAINT_PATTERNS.search(text):
        return "complaint"
    if _AI_RISK_PATTERNS.search(text):
        return "ai_risk"
    if _AI_CAPABILITY_PATTERNS.search(text):
        return "ai_capability"
    if _AI_TOOLS_PATTERNS.search(text):
        return "ai_tools"
    if _MEME_PATTERNS.search(text):
        return "meme_culture"

    # News link detection
    if item.canonical_url and not item.canonical_url.startswith("https://www.reddit.com"):
        return "news_link"

    # Default: self-post без тематического паттерна — это обсуждение, а не «other».
    if bool(item.metadata.get("is_self")):
        return "discussion"
    return "other"


def perspective_gap_available_counts(
    voices: int,
    mainstream: int,
    *,
    min_mainstream: int = 100,
    min_ratio: float = 0.2,
) -> bool:
    """Достаточно ли голосов и mainstream для измерения разрыва (по счётчикам).

    Разрыв имеет смысл, когда Reddit (voices) и mainstream представлены в сопоставимых
    объёмах. Релиз вроде ai-native (1600 reddit / 126 mainstream) — неизмерим.
    """

    if voices == 0 or mainstream < min_mainstream:
        return False
    return mainstream >= min_ratio * voices


def perspective_gap_available(
    items: list[ContentItem],
    *,
    min_mainstream: int = 100,
    min_ratio: float = 0.2,
) -> bool:
    """Достаточно ли сбалансирован релиз для измерения разрыва перспективы."""

    voices = sum(1 for item in items if item.source_cluster == "voices")
    mainstream = sum(1 for item in items if item.source_cluster == "mainstream")
    return perspective_gap_available_counts(
        voices, mainstream, min_mainstream=min_mainstream, min_ratio=min_ratio
    )


def compute_signal_perspective_gap(pulse_score: float, mainstream_coverage_count: int) -> float:
    """Разрыв перспективы сигнала: высокий Reddit-пульс при низком покрытии СМИ.

    0..1: тем выше, чем сильнее обсуждение в Reddit и чем меньше mainstream-источников
    осветили связанную историю. Насыщение mainstream на 5 источниках → разрыв 0.
    """

    reddit_weight = max(0.0, min(pulse_score, 100.0)) / 100.0
    mainstream_saturation = min(max(mainstream_coverage_count, 0), 5) / 5.0
    return round(reddit_weight * (1.0 - mainstream_saturation), 4)


def compute_subreddit_percentile(
    item: ContentItem,
    all_items_by_sub: dict[str, list[ContentItem]],
) -> float:
    """Percentile of item's score within its subreddit for the same date."""
    sub = item.source_section.lower()
    peers = all_items_by_sub.get(sub, [])
    if not peers:
        return 50.0
    scores = sorted(_raw_score(p) for p in peers)
    item_score = _raw_score(item)
    rank = sum(1 for s in scores if s <= item_score)
    return (rank / len(scores)) * 100.0


def compute_score_velocity(item: ContentItem, hours_since_publish: float = 24.0) -> float:
    """Score per hour, normalized by item age."""
    score = _raw_score(item)
    hours = max(hours_since_publish, 1.0)
    return score / hours


def compute_comment_velocity(item: ContentItem, hours_since_publish: float = 24.0) -> float:
    """Comments per hour, normalized."""
    comments = item.raw_engagement.get("comments", 0)
    hours = max(hours_since_publish, 1.0)
    return comments / hours


def compute_discussion_depth(item: ContentItem) -> float:
    """Log-scaled comment depth with upvote ratio guard."""
    comments = item.raw_engagement.get("comments", 0)
    ratio = item.raw_engagement.get("upvote_ratio", 0.5)
    if ratio < 0.5:
        return 0.0  # Controversial/downvoted — not a quality discussion
    return math.log1p(comments) * min(ratio, 1.0)


def compute_comment_score_ratio(item: ContentItem) -> float:
    """Comments / max(score, 1). High ratio = discussion-heavy."""
    score = max(_raw_score(item), 1)
    comments = item.raw_engagement.get("comments", 0)
    return comments / score


def compute_cross_subreddit_repetition(
    item: ContentItem,
    all_items: list[ContentItem],
    title_tokens: set[str],
) -> float:
    """Normalized count of similar high-confidence Reddit stories in other subs."""
    if not title_tokens:
        return 0.0
    own_sub = item.source_section.lower()
    similar_subs: set[str] = set()
    for other in all_items:
        if other.item_id == item.item_id:
            continue
        if other.provider != "reddit":
            continue
        other_sub = other.source_section.lower()
        if other_sub == own_sub:
            continue
        other_tokens = tokenize_title(other.title)
        overlap = len(title_tokens & other_tokens) / max(len(title_tokens | other_tokens), 1)
        if overlap >= 0.5:
            similar_subs.add(other_sub)
    # Normalize: 0 subs = 0, 1 sub = 0.25, 2 = 0.5, 3 = 0.75, 4+ = 1.0
    n = len(similar_subs)
    return min(n * 0.25, 1.0) if n > 0 else 0.0


def compute_novelty(
    item: ContentItem,
    seen_titles_last_7d: set[str],
    title_tokens: set[str],
    *,
    history_available: bool = True,
) -> float:
    """1 - seen_similar_in_last_7d."""
    if not history_available:
        return 0.5
    if not title_tokens:
        return 0.5
    for seen_entry in seen_titles_last_7d:
        seen_tokens: set[str] = (
            set(seen_entry.split()) if isinstance(seen_entry, str) else seen_entry
        )
        overlap = len(title_tokens & seen_tokens) / max(len(title_tokens | seen_tokens), 1)
        if overlap >= 0.6:
            return 0.0
    return 1.0


def compute_pulse_score(
    subreddit_percentile: float,
    comment_velocity: float,
    discussion_depth: float,
    cross_sub_repetition: float,
    novelty: float,
) -> float:
    """Reddit Pulse score (0-100)."""
    raw = (
        0.30 * subreddit_percentile
        + 0.25 * min(comment_velocity * 10, 100)  # normalize velocity
        + 0.20 * min(discussion_depth * 10, 100)  # normalize depth
        + 0.15 * cross_sub_repetition * 100
        + 0.10 * novelty * 100
    )
    return min(raw, 100.0)


def _raw_score(item: ContentItem) -> float:
    return item.raw_engagement.get("score", 0)


def tokenize_title(title: str) -> set[str]:
    """Stable lightweight tokenization for Reddit repetition and novelty."""
    return {token.lower() for token in _TOKEN_RE.findall(title) if len(token) > 1}


def compute_hours_since_publish(item: ContentItem) -> float:
    """Best-effort item age in hours from frozen release timestamps."""
    if not item.published_at:
        return 24.0
    observed = item.observed_at or item.snapshot_date
    try:
        published_dt = _parse_datetime(item.published_at)
        observed_dt = _parse_datetime(observed)
    except ValueError:
        return 24.0
    hours = (observed_dt - published_dt).total_seconds() / 3600.0
    return max(hours, 1.0)


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if len(normalized) == 10:
        normalized = f"{normalized}T23:59:59Z"
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass
class CommunitySignal:
    """Reddit-native community signal."""

    signal_id: str
    item_id: str
    subreddit: str
    pack_id: str
    signal_type: SignalType
    title: str
    discussion_url: str
    target_url: str
    pulse_score: float
    subreddit_percentile: float
    score_velocity: float
    comment_velocity: float
    discussion_depth: float
    comment_score_ratio: float
    cross_subreddit_repetition: float
    novelty: float
    domain_ids: list[str] = field(default_factory=list)
    theme_ids: list[str] = field(default_factory=list)
    pain_points: list[str] = field(default_factory=list)
    project_scores: dict[str, int] = field(default_factory=dict)
    linked_story_id: str | None = None
    mainstream_coverage_count: int = 0
    perspective_gap: float = 0.0


def build_reddit_pulse_signals(
    items: list[ContentItem],
    seen_titles_last_7d: set[str] | None = None,
    *,
    pack_by_subreddit: dict[str, str] | None = None,
    story_id_by_item_id: dict[str, str] | None = None,
    mainstream_coverage_by_story_id: dict[str, int] | None = None,
    history_available: bool | None = None,
    gap_available: bool | None = None,
) -> list[CommunitySignal]:
    """Build CommunitySignal for all Reddit items in a release.

    ``gap_available=None`` вычисляет доступность разрыва перспективы из баланса релиза;
    на несбалансированном релизе ``perspective_gap`` не считается (остаётся 0.0), чтобы
    не выдавать отсутствие mainstream-данных за нулевой разрыв.
    """
    seen = seen_titles_last_7d or set()
    has_history = bool(seen) if history_available is None else history_available
    gap_ok = perspective_gap_available(items) if gap_available is None else gap_available
    pack_by_sub = {k.lower(): v for k, v in (pack_by_subreddit or {}).items()}
    story_by_item = story_id_by_item_id or {}
    mainstream_by_story = mainstream_coverage_by_story_id or {}

    # Group items by subreddit for percentile computation
    by_sub: dict[str, list[ContentItem]] = defaultdict(list)
    reddit_items = [i for i in items if i.provider == "reddit"]
    for item in reddit_items:
        by_sub[item.source_section.lower()].append(item)

    signals: list[CommunitySignal] = []
    for item in reddit_items:
        title_tokens = tokenize_title(item.title)
        hours_since_publish = compute_hours_since_publish(item)
        percentile = compute_subreddit_percentile(item, by_sub)
        score_velocity = compute_score_velocity(item, hours_since_publish)
        velocity = compute_comment_velocity(item, hours_since_publish)
        depth = compute_discussion_depth(item)
        ratio = compute_comment_score_ratio(item)
        cross_sub = compute_cross_subreddit_repetition(item, reddit_items, title_tokens)
        novelty = compute_novelty(
            item,
            seen,
            title_tokens,
            history_available=has_history,
        )
        pulse = compute_pulse_score(percentile, velocity, depth, cross_sub, novelty)
        signal_type = classify_signal_type(item)
        linked_story_id = story_by_item.get(item.item_id)
        mainstream_coverage = mainstream_by_story.get(linked_story_id or "", 0)
        gap = compute_signal_perspective_gap(pulse, mainstream_coverage) if gap_ok else 0.0

        signals.append(
            CommunitySignal(
                signal_id=f"pulse_{item.item_id}",
                item_id=item.item_id,
                subreddit=item.source_section,
                pack_id=item.metadata.get("pack_id", "")
                or pack_by_sub.get(item.source_section.lower(), ""),
                signal_type=signal_type,
                title=item.title,
                discussion_url=item.discussion_url or item.canonical_url,
                target_url=item.target_url,
                pulse_score=round(pulse, 2),
                subreddit_percentile=round(percentile, 2),
                score_velocity=round(score_velocity, 2),
                comment_velocity=round(velocity, 2),
                discussion_depth=round(depth, 2),
                comment_score_ratio=round(ratio, 2),
                cross_subreddit_repetition=round(cross_sub, 2),
                novelty=round(novelty, 2),
                domain_ids=list(item.domain_ids),
                theme_ids=list(item.metadata.get("theme_ids", [])),
                pain_points=list(item.metadata.get("pain_points", [])),
                project_scores=dict(item.metadata.get("project_scores", {})),
                linked_story_id=linked_story_id,
                mainstream_coverage_count=mainstream_coverage,
                perspective_gap=gap,
            )
        )

    return signals
