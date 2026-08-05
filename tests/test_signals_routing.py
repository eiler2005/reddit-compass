"""Маршрутизация ключей Qwen: массовые прогоны — на бесплатные квоты pay-as-you-go.

На token-plan уходит только то, чего на pay-as-you-go нет. Ошибка маршрутизации
здесь — это либо 404 в ночном прогоне, либо платные вызовы там, где есть бесплатная
квота.
"""

from __future__ import annotations

import pytest

from reddit_compass.signals import _get_api_config


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
