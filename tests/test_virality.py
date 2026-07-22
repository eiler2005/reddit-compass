"""Тесты детектора виральности."""

from __future__ import annotations

from collections.abc import Callable

from reddit_compass.config import MonitorConfig, MonitorSettings
from reddit_compass.detect_virality import detect_virality
from reddit_compass.models import PostCard


def _cfg(**settings: int) -> MonitorConfig:
    return MonitorConfig(
        subreddits={"c": ["artificial", "deepfakes"]},
        settings=MonitorSettings(**settings),
    )


def test_score_surge(make_card: Callable[..., PostCard]) -> None:
    cards = [make_card(post_id="hi", score=5000)]
    signals = detect_virality(cards, _cfg(), "2026-07-22")
    assert any(s.signal_type == "score_surge" and s.post_id == "hi" for s in signals)


def test_crosspost(make_card: Callable[..., PostCard]) -> None:
    cards = [make_card(post_id="cp", score=10, crosspost_parents=["a/1", "b/2"])]
    signals = detect_virality(cards, _cfg(virality_crosspost_min=2), "2026-07-22")
    sig = next(s for s in signals if s.signal_type == "crosspost")
    assert sig.crossposted_to == ["a/1", "b/2"]
    assert sig.to_dict()["signal_type"] == "crosspost"  # exercises to_dict


def test_multi_subreddit(make_card: Callable[..., PostCard]) -> None:
    title = "This exact long headline appears in two subreddits today"
    cards = [
        make_card(post_id="m1", subreddit="artificial", title=title, score=100),
        make_card(post_id="m2", subreddit="deepfakes", title=title, score=200),
    ]
    signals = detect_virality(
        cards, _cfg(virality_crosspost_min=2, virality_score_threshold=100000), "2026-07-22"
    )
    multi = [s for s in signals if s.signal_type == "multi_subreddit"]
    assert len(multi) == 1
    assert multi[0].total_score == 300
    assert multi[0].post_id == "m2"  # лучший по score


def test_dedup_by_post_and_type(make_card: Callable[..., PostCard]) -> None:
    card = make_card(post_id="d", score=5000)
    signals = detect_virality([card, card], _cfg(), "2026-07-22")
    surges = [s for s in signals if s.signal_type == "score_surge"]
    assert len(surges) == 1
