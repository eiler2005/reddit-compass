"""Стоимостная маршрутизация: скидки, квоты, леджер — без сети.

Проверяется то, что решает о деньгах: порядок цепочек, переход на бесплатные квоты
при исчерпании, скидочное окно подписки и учёт расхода.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from reddit_compass import qwen_policy


@pytest.fixture()
def both_keys(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("QWEN_TOKEN_PLAN_KEY", "tp-key")
    monkeypatch.setenv("QWEN_PAY_AS_YOU_GO_PLAN_KEY", "payg-key")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("RC_QWEN_LEDGER_PATH", str(tmp_path / "usage.db"))


def test_offpeak_window_is_17_00_03_00_msk() -> None:
    assert qwen_policy.in_offpeak(datetime(2026, 8, 5, 14, 0, tzinfo=UTC))
    assert qwen_policy.in_offpeak(datetime(2026, 8, 5, 23, 30, tzinfo=UTC))
    assert qwen_policy.in_offpeak(datetime(2026, 8, 5, 23, 59, tzinfo=UTC))
    # 00:00 UTC = 03:00 МСК — окно уже закрылось.
    assert not qwen_policy.in_offpeak(datetime(2026, 8, 5, 0, 0, tzinfo=UTC))
    assert not qwen_policy.in_offpeak(datetime(2026, 8, 5, 13, 59, tzinfo=UTC))
    assert not qwen_policy.in_offpeak(datetime(2026, 8, 5, 8, 0, tzinfo=UTC))


def test_bulk_prefers_free_payg_quota(both_keys) -> None:
    model, endpoint, why = qwen_policy.pick_model("bulk")

    assert (model, endpoint) == ("qwen3-235b-a22b-instruct-2507", "payg")
    assert "бесплатная" in why


def test_bulk_falls_through_exhausted_free_quotas(both_keys, monkeypatch) -> None:
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "100")
    qwen_policy.record_usage(
        model="qwen3-235b-a22b-instruct-2507",
        endpoint="payg",
        prompt_tokens=60,
        completion_tokens=60,
    )

    model, endpoint, _ = qwen_policy.pick_model("bulk")

    assert (model, endpoint) == ("qwen3.6-flash", "payg")


def test_bulk_last_resort_is_subscription_when_free_exhausted(both_keys, monkeypatch) -> None:
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "10")
    for model in ("qwen3-235b-a22b-instruct-2507", "qwen3.6-flash", "qwen3.5-flash", "qwen-flash"):
        qwen_policy.record_usage(
            model=model, endpoint="payg", prompt_tokens=10, completion_tokens=10
        )

    picked_model, endpoint, _ = qwen_policy.pick_model("bulk")

    assert (picked_model, endpoint) == ("qwen3.6-flash", "token-plan")


def test_synth_offpeak_uses_discounted_subscription(both_keys) -> None:
    model, endpoint, why = qwen_policy.pick_model(
        "synth", now=datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
    )

    assert (model, endpoint) == ("qwen3.8-max-preview", "token-plan")
    assert "скидочное" in why


def test_synth_peak_prefers_free_quota(both_keys) -> None:
    model, endpoint, _ = qwen_policy.pick_model("synth", now=datetime(2026, 8, 5, 8, 0, tzinfo=UTC))

    assert (model, endpoint) == ("qwen3-max", "payg")


def test_synth_peak_uses_subscription_when_free_exhausted(both_keys, monkeypatch) -> None:
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "10")
    for model in ("qwen3-max", "qwen3-235b-a22b-instruct-2507"):
        qwen_policy.record_usage(
            model=model, endpoint="payg", prompt_tokens=10, completion_tokens=10
        )

    picked_model, endpoint, _ = qwen_policy.pick_model(
        "synth", now=datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    )

    assert (picked_model, endpoint) == ("qwen3.8-max-preview", "token-plan")


def test_token_plan_quota_is_shared_across_models(both_keys, monkeypatch) -> None:
    monkeypatch.setenv("RC_QWEN_TOKEN_PLAN_TOKENS", "100")
    qwen_policy.record_usage(
        model="qwen3.8-max-preview", endpoint="token-plan", prompt_tokens=60, completion_tokens=60
    )

    totals = qwen_policy.usage_totals()

    assert not qwen_policy._room_left("qwen3.6-flash", "token-plan", totals)


def test_ledger_records_and_aggregates(both_keys) -> None:
    qwen_policy.record_usage(model="m", endpoint="payg", prompt_tokens=7, completion_tokens=3)
    qwen_policy.record_usage(model="m", endpoint="payg", prompt_tokens=5, completion_tokens=5)

    assert qwen_policy.usage_totals()[("m", "payg")] == 20
