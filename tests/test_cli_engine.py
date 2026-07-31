"""CLI contracts for independent Story and Trend review limits."""

from __future__ import annotations

from argparse import Namespace

from reddit_compass.cli import _engine_review_requested


def test_trend_only_review_still_creates_qwen_runner() -> None:
    assert _engine_review_requested(Namespace(review_limit=0, trend_review_limit=12)) is True


def test_no_engine_review_does_not_create_qwen_runner() -> None:
    assert _engine_review_requested(Namespace(review_limit=0, trend_review_limit=0)) is False
