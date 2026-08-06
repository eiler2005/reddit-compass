"""Маршрутизация ключей Qwen: массовые прогоны — на бесплатные квоты pay-as-you-go.

На token-plan уходит только то, чего на pay-as-you-go нет. Ошибка маршрутизации
здесь — это либо 404 в ночном прогоне, либо платные вызовы там, где есть бесплатная
квота.
"""

from __future__ import annotations

import pytest

from reddit_compass.models import PostCard
from reddit_compass.signals import _get_api_config


def _card(post_id: str) -> PostCard:
    return PostCard(
        subreddit="technology",
        post_id=post_id,
        title=f"Title for {post_id}",
        author="author",
        created_utc="2026-08-06T07:00:00Z",
        score=12,
        upvote_ratio=0.9,
        num_comments=4,
        url=f"https://example.com/{post_id}",
        selftext="Excerpt",
        link_flair_text=None,
        is_self=False,
        permalink=f"/r/technology/comments/{post_id}",
        monitoring_type="hot",
        snapshot_date="2026-08-06",
    )


def test_payg_takes_bulk_models_when_both_keys_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QWEN_TOKEN_PLAN_KEY", "tp-key")
    monkeypatch.setenv("QWEN_Pay_As_You_Go_PLAN_KEY", "payg-key")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    key, base_url, classify, synthesis = _get_api_config("qwen3.7-flash")

    assert key == "payg-key"
    assert "dashscope-intl" in base_url
    assert classify == "qwen3.7-flash"
    assert synthesis == "qwen3.8-max"


def test_ga_max_goes_to_payg_not_the_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    """`qwen3.8-max` вышел из превью и появился на payg — гнать его на подписку значит
    платить там, где лежит бесплатный грант."""
    monkeypatch.setenv("QWEN_TOKEN_PLAN_KEY", "tp-key")
    monkeypatch.setenv("QWEN_Pay_As_You_Go_PLAN_KEY", "payg-key")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    key, base_url, _, _ = _get_api_config("qwen3.8-max")

    assert key == "payg-key"
    assert "dashscope-intl" in base_url


def test_preview_identifier_stays_exclusive_to_the_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_TOKEN_PLAN_KEY", "tp-key")
    monkeypatch.setenv("QWEN_Pay_As_You_Go_PLAN_KEY", "payg-key")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    key, base_url, _, _ = _get_api_config("qwen3.8-max-preview")

    assert key == "tp-key"
    assert "token-plan" in base_url


def test_token_plan_only_key_still_routes_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QWEN_TOKEN_PLAN_KEY", "tp-key")
    monkeypatch.delenv("QWEN_Pay_As_You_Go_PLAN_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    key, _, _, _ = _get_api_config("qwen3.6-flash")

    assert key == "tp-key"


def test_total_classification_failure_is_raised_not_reported_as_zero_signals() -> None:
    """Провал всех батчей обязан быть виден, а не выглядеть как «сегодня нет сигналов».

    `analyze_posts` делала `continue` на каждой ошибке и возвращала `[]`. Неверный ключ
    → все батчи 401 → «Извлечено 0 сигналов», пустой `signals.jsonl`, отчёт, история тем,
    радар и код возврата 0. Пропуск отдельного батча остаётся допустимым.
    """
    import asyncio

    from reddit_compass import signals as signals_mod
    from reddit_compass.signals import QwenAllBatchesFailedError, QwenApiError, analyze_posts

    async def always_401(*args, **kwargs):
        raise QwenApiError(401, "invalid api key")

    original = signals_mod._call_qwen
    signals_mod._call_qwen = always_401
    try:
        with pytest.raises(QwenAllBatchesFailedError) as excinfo:
            asyncio.run(analyze_posts([_card(f"p{i}") for i in range(3)], model="qwen3.7-flash"))
    finally:
        signals_mod._call_qwen = original

    assert excinfo.value.batches == 1
    assert "401" in excinfo.value.reasons[0]


def test_partial_classification_failure_still_returns_its_signals() -> None:
    """Классификация Pulse не обязана быть полной: один упавший батч не роняет стадию."""
    import asyncio
    import json

    from reddit_compass import signals as signals_mod
    from reddit_compass.signals import QwenApiError, analyze_posts

    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise QwenApiError(429, "rate limited")
        return json.dumps(
            [
                {
                    "post_id": "p20",
                    "pain_points": [],
                    "buying_intent": False,
                    "business_relevance": 5,
                    "book_relevance": 5,
                    "themes": [],
                    "summary": "ok",
                }
            ]
        )

    original = signals_mod._call_qwen
    signals_mod._call_qwen = flaky
    try:
        # BATCH_SIZE = 20, поэтому 21 карточка даёт ровно два батча.
        result = asyncio.run(
            analyze_posts([_card(f"p{i}") for i in range(21)], model="qwen3.7-flash")
        )
    finally:
        signals_mod._call_qwen = original

    assert [signal.post_id for signal in result] == ["p20"]
