"""CLI contracts for independent Story and Trend review limits."""

from __future__ import annotations

import asyncio
from argparse import Namespace

from reddit_compass.cli import _engine_review_requested, _review_trend_jobs


def test_trend_only_review_still_creates_qwen_runner() -> None:
    assert _engine_review_requested(Namespace(review_limit=0, trend_review_limit=12)) is True


def test_no_engine_review_does_not_create_qwen_runner() -> None:
    assert _engine_review_requested(Namespace(review_limit=0, trend_review_limit=0)) is False


def test_trend_review_timeout_does_not_abort_remaining_jobs() -> None:
    jobs = [
        {
            "target_id": "trend-1",
            "input_hash": "hash-1",
            "prompt": "first",
            "story_ids": ["story-1"],
            "prompt_version": "v1",
        },
        {
            "target_id": "trend-2",
            "input_hash": "hash-2",
            "prompt": "second",
            "story_ids": ["story-2"],
            "prompt_version": "v1",
        },
    ]
    attempted: list[str] = []
    stored: list[str] = []

    async def review_runner(prompt: str, _model: str) -> str:
        attempted.append(prompt)
        if prompt == "first":
            raise TimeoutError
        return "{}"

    def store_response(**kwargs: object) -> dict[str, object]:
        stored.append(str(kwargs["target_id"]))
        return {"target_id": kwargs["target_id"], "valid": False}

    results, errors = asyncio.run(
        _review_trend_jobs(
            jobs,
            model="qwen-test",
            review_runner=review_runner,
            store_response=store_response,
        )
    )

    assert attempted == ["first", "second"]
    assert errors == ["trend-1:TimeoutError"]
    assert results[0] == {
        "target_id": "trend-1",
        "decision": "error",
        "valid": False,
        "error": "TimeoutError",
    }
    assert stored == ["trend-2"]
