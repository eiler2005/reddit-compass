"""Bounded-Qwen contracts for the versioned Engine."""

from __future__ import annotations

import asyncio

import pytest

from reddit_compass import signals


def test_engine_json_review_times_out_without_waiting_for_bulk_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    async def stalled_call(*_args: object, **kwargs: object) -> str:
        received.update(kwargs)
        await asyncio.sleep(1)
        return "{}"

    monkeypatch.setattr(signals, "_call_qwen", stalled_call)

    async def run() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await signals.call_qwen_json("{}", timeout_seconds=0.001)

    asyncio.run(run())
    # The bounded Engine budget reaches aiohttp as well as asyncio.wait_for;
    # without this, a half-closed socket can outlive the outer cancellation.
    assert received["timeout_seconds"] == 0.001
