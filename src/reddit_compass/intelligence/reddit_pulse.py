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
    if _PAIN_PATTERNS.search(text):
        return "pain_point"
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

    # Default
    if item.is_self if hasattr(item, "is_self") else False:
        return "discussion"
    return "other"


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
        other_tokens = set(other.title.lower().split())
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
) -> float:
    """1 - seen_similar_in_last_7d."""
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
) -> list[CommunitySignal]:
    """Build CommunitySignal for all Reddit items in a release."""
    seen = seen_titles_last_7d or set()

    # Group items by subreddit for percentile computation
    by_sub: dict[str, list[ContentItem]] = defaultdict(list)
    reddit_items = [i for i in items if i.provider == "reddit"]
    for item in reddit_items:
        by_sub[item.source_section.lower()].append(item)

    signals: list[CommunitySignal] = []
    for item in reddit_items:
        title_tokens = set(item.title.lower().split())
        percentile = compute_subreddit_percentile(item, by_sub)
        velocity = compute_comment_velocity(item)
        depth = compute_discussion_depth(item)
        ratio = compute_comment_score_ratio(item)
        cross_sub = compute_cross_subreddit_repetition(item, reddit_items, title_tokens)
        novelty = compute_novelty(item, seen, title_tokens)
        pulse = compute_pulse_score(percentile, velocity, depth, cross_sub, novelty)
        signal_type = classify_signal_type(item)

        signals.append(
            CommunitySignal(
                signal_id=f"pulse_{item.item_id}",
                item_id=item.item_id,
                subreddit=item.source_section,
                pack_id=item.metadata.get("pack_id", ""),
                signal_type=signal_type,
                title=item.title,
                discussion_url=item.discussion_url or item.canonical_url,
                target_url=item.target_url,
                pulse_score=round(pulse, 2),
                subreddit_percentile=round(percentile, 2),
                score_velocity=round(velocity, 2),
                comment_velocity=round(velocity, 2),
                discussion_depth=round(depth, 2),
                comment_score_ratio=round(ratio, 2),
                cross_subreddit_repetition=round(cross_sub, 2),
                novelty=round(novelty, 2),
                domain_ids=list(item.domain_ids),
                theme_ids=list(item.metadata.get("theme_ids", [])),
                pain_points=list(item.metadata.get("pain_points", [])),
                project_scores=dict(item.metadata.get("project_scores", {})),
            )
        )

    return signals
