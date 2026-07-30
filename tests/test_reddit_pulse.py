"""Tests for Reddit Pulse scoring and signal classification."""

from __future__ import annotations

from reddit_compass.intelligence.models import ContentItem
from reddit_compass.intelligence.reddit_pulse import (
    build_reddit_pulse_signals,
    classify_signal_type,
    compute_comment_score_ratio,
    compute_comment_velocity,
    compute_cross_subreddit_repetition,
    compute_discussion_depth,
    compute_novelty,
    compute_pulse_score,
    compute_score_velocity,
    compute_subreddit_percentile,
)


def _make_reddit_item(
    item_id: str = "r1",
    subreddit: str = "technology",
    title: str = "Test post",
    score: float = 100,
    comments: float = 50,
    upvote_ratio: float = 0.9,
    canonical_url: str = "https://www.reddit.com/r/technology/comments/r1",
    target_url: str = "",
    excerpt: str = "",
    metadata: dict[str, object] | None = None,
    published_at: str | None = None,
    observed_at: str = "",
) -> ContentItem:
    return ContentItem(
        item_id=item_id,
        provider="reddit",
        source_cluster="voices",
        external_id=item_id,
        canonical_url=canonical_url,
        title=title,
        excerpt=excerpt,
        source_section=subreddit,
        target_url=target_url,
        raw_engagement={"score": score, "comments": comments, "upvote_ratio": upvote_ratio},
        metadata=metadata or {},
        published_at=published_at,
        observed_at=observed_at,
    )


class TestClassifySignalType:
    def test_question_pattern(self):
        item = _make_reddit_item(title="How do I fix this bug?")
        assert classify_signal_type(item) == "question"

    def test_pain_pattern(self):
        item = _make_reddit_item(title="I hate this broken feature")
        assert classify_signal_type(item) == "pain_point"

    def test_ai_risk_pattern(self):
        item = _make_reddit_item(title="AI hallucination causes major problem")
        assert classify_signal_type(item) == "ai_risk"

    def test_ai_capability_pattern(self):
        item = _make_reddit_item(title="GPT-5 beats all benchmarks")
        assert classify_signal_type(item) == "ai_capability"

    def test_ai_tools_pattern(self):
        item = _make_reddit_item(title="Show HN: Built with Cursor and AI")
        assert classify_signal_type(item) == "ai_tools"

    def test_meme_pattern(self):
        item = _make_reddit_item(title="This meme is so funny lol")
        assert classify_signal_type(item) == "meme_culture"

    def test_career_subreddit(self):
        item = _make_reddit_item(subreddit="cscareerquestions", title="Salary discussion")
        assert classify_signal_type(item) == "career_labor"

    def test_market_subreddit(self):
        item = _make_reddit_item(subreddit="wallstreetbets", title="YOLO calls")
        assert classify_signal_type(item) == "market_investing"

    def test_policy_subreddit(self):
        item = _make_reddit_item(subreddit="politics", title="New bill passed")
        assert classify_signal_type(item) == "policy_politics"

    def test_news_link(self):
        item = _make_reddit_item(
            title="Breaking news",
            canonical_url="https://www.reddit.com/r/news/comments/r1",
            target_url="https://example.com/article",
        )
        # target_url is set but canonical_url is reddit — classify_signal_type checks canonical_url
        # Since canonical_url starts with reddit.com, it won't be news_link via URL check
        # But target_url is set... let's check the logic
        # The function checks canonical_url, not target_url for news_link
        # So this would be "other" since no patterns match and canonical_url is reddit
        assert classify_signal_type(item) == "other"

    def test_news_link_non_reddit_url(self):
        item = _make_reddit_item(
            title="Breaking news",
            canonical_url="https://example.com/article",
        )
        assert classify_signal_type(item) == "news_link"

    def test_self_post_without_patterns_is_discussion(self):
        item = _make_reddit_item(title="My experience this week", metadata={"is_self": True})
        assert classify_signal_type(item) == "discussion"


class TestSubredditPercentile:
    def test_percentile_within_subreddit_not_global(self):
        """Viral r/news score must not suppress small-subreddit high-percentile signal."""
        # r/news: scores 1000, 2000, 3000
        # r/smallsub: scores 10, 20, 30
        news_items = [
            _make_reddit_item(item_id=f"n{i}", subreddit="news", score=s)
            for i, s in enumerate([1000, 2000, 3000])
        ]
        small_items = [
            _make_reddit_item(item_id=f"s{i}", subreddit="smallsub", score=s)
            for i, s in enumerate([10, 20, 30])
        ]
        all_by_sub: dict[str, list[ContentItem]] = {
            "news": news_items,
            "smallsub": small_items,
        }
        # Top item in smallsub (score=30) should have 100% percentile within its sub
        pct = compute_subreddit_percentile(small_items[2], all_by_sub)
        assert pct == 100.0
        # Top item in news (score=3000) should also have 100% percentile
        pct_news = compute_subreddit_percentile(news_items[2], all_by_sub)
        assert pct_news == 100.0

    def test_single_item_gets_100_percentile(self):
        item = _make_reddit_item(item_id="only", subreddit="lonely", score=5)
        by_sub = {"lonely": [item]}
        assert compute_subreddit_percentile(item, by_sub) == 100.0

    def test_empty_peers_returns_50(self):
        item = _make_reddit_item(subreddit="ghost")
        assert compute_subreddit_percentile(item, {}) == 50.0


class TestCommentVelocity:
    def test_basic_velocity(self):
        item = _make_reddit_item(comments=48)
        v = compute_comment_velocity(item, hours_since_publish=24.0)
        assert v == 2.0

    def test_zero_hours_guard(self):
        item = _make_reddit_item(comments=10)
        v = compute_comment_velocity(item, hours_since_publish=0.0)
        assert v == 10.0  # max(0, 1) = 1


class TestScoreVelocity:
    def test_basic_score_velocity(self):
        item = _make_reddit_item(score=240)
        assert compute_score_velocity(item, hours_since_publish=24.0) == 10.0


class TestDiscussionDepth:
    def test_high_ratio(self):
        item = _make_reddit_item(comments=100, upvote_ratio=0.95)
        import math

        expected = math.log1p(100) * 0.95
        assert abs(compute_discussion_depth(item) - expected) < 0.01

    def test_low_ratio_returns_zero(self):
        item = _make_reddit_item(comments=100, upvote_ratio=0.3)
        assert compute_discussion_depth(item) == 0.0


class TestCommentScoreRatio:
    def test_ratio(self):
        item = _make_reddit_item(score=100, comments=200)
        assert compute_comment_score_ratio(item) == 2.0

    def test_zero_score_guard(self):
        item = _make_reddit_item(score=0, comments=50)
        assert compute_comment_score_ratio(item) == 50.0


class TestCrossSubredditRepetition:
    def test_no_repetition(self):
        item = _make_reddit_item(item_id="a", title="unique topic here")
        assert compute_cross_subreddit_repetition(item, [item], {"unique", "topic", "here"}) == 0.0

    def test_repetition_across_subs(self):
        item_a = _make_reddit_item(item_id="a", subreddit="sub1", title="ai agents are taking over")
        item_b = _make_reddit_item(
            item_id="b",
            subreddit="sub2",
            title="ai agents are taking over the world",
        )
        tokens_a = set(item_a.title.lower().split())
        rep = compute_cross_subreddit_repetition(item_a, [item_a, item_b], tokens_a)
        assert rep > 0.0


class TestNovelty:
    def test_novel_item(self):
        item = _make_reddit_item(title="brand new topic")
        tokens = set(item.title.lower().split())
        assert compute_novelty(item, set(), tokens) == 1.0

    def test_no_history_is_neutral_not_novel(self):
        item = _make_reddit_item(title="brand new topic")
        tokens = set(item.title.lower().split())
        assert compute_novelty(item, set(), tokens, history_available=False) == 0.5

    def test_seen_item(self):
        item = _make_reddit_item(title="old topic here")
        tokens = set(item.title.lower().split())
        seen = {"old topic here"}
        assert compute_novelty(item, seen, tokens) == 0.0


class TestPulseScore:
    def test_max_score(self):
        score = compute_pulse_score(100, 10, 10, 1.0, 1.0)
        assert score == 100.0

    def test_zero_score(self):
        score = compute_pulse_score(0, 0, 0, 0, 0)
        assert score == 0.0

    def test_weighted_components(self):
        # Only percentile contributes
        score = compute_pulse_score(50, 0, 0, 0, 0)
        assert abs(score - 15.0) < 0.01  # 0.30 * 50


class TestBuildRedditPulseSignals:
    def test_builds_signals_for_reddit_items_only(self):
        reddit_item = _make_reddit_item(item_id="r1", title="Reddit post")
        news_item = ContentItem(
            item_id="n1",
            provider="rss",
            source_cluster="news",
            external_id="n1",
            canonical_url="https://example.com",
            title="News article",
        )
        signals = build_reddit_pulse_signals([reddit_item, news_item])
        assert len(signals) == 1
        assert signals[0].item_id == "r1"

    def test_signal_has_correct_fields(self):
        item = _make_reddit_item(
            item_id="r1",
            subreddit="technology",
            title="How do I use AI tools?",
            score=500,
            comments=100,
            upvote_ratio=0.92,
        )
        signals = build_reddit_pulse_signals([item])
        assert len(signals) == 1
        s = signals[0]
        assert s.signal_id == "pulse_r1"
        assert s.subreddit == "technology"
        assert s.signal_type == "question"
        assert s.pulse_score > 0
        assert s.subreddit_percentile == 100.0  # only item in sub

    def test_show_hn_items_classified_as_ai_tools(self):
        item = _make_reddit_item(title="Show HN: I built an AI agent")
        signals = build_reddit_pulse_signals([item])
        assert signals[0].signal_type == "ai_tools"

    def test_build_uses_story_linkage_pack_and_mainstream_coverage(self):
        item = _make_reddit_item(item_id="r1", subreddit="ChatGPT")
        signals = build_reddit_pulse_signals(
            [item],
            pack_by_subreddit={"chatgpt": "ai_technology"},
            story_id_by_item_id={"r1": "story_1"},
            mainstream_coverage_by_story_id={"story_1": 2},
        )

        assert signals[0].pack_id == "ai_technology"
        assert signals[0].linked_story_id == "story_1"
        assert signals[0].mainstream_coverage_count == 2

    def test_build_neutralizes_novelty_when_history_missing(self):
        item = _make_reddit_item(item_id="r1", title="Completely new phrase")
        signals = build_reddit_pulse_signals([item], history_available=False)

        assert signals[0].novelty == 0.5
