"""Bounded-Qwen contracts for the versioned Engine."""

from __future__ import annotations

import asyncio

import pytest

from reddit_compass import signals


def test_engine_json_review_times_out_without_waiting_for_bulk_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stalled_call(*_args: object, **_kwargs: object) -> str:
        await asyncio.sleep(1)
        return "{}"

    monkeypatch.setattr(signals, "_call_qwen", stalled_call)

    async def run() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await signals.call_qwen_json("{}", timeout_seconds=0.001)

    asyncio.run(run())
