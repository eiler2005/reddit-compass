"""Маршрутизация ключей Qwen: массовые прогоны — на бесплатные квоты pay-as-you-go.

Семейство qwen3.8-max отсутствует на pay-as-you-go ключе (проверено по /v1/models),
поэтому только оно ходит через token-plan. Ошибка маршрутизации здесь — это либо
404 в ночном прогоне, либо платные вызовы там, где есть бесплатная квота.
"""

from __future__ import annotations

import pytest

from reddit_compass.signals import _get_api_config


def test_payg_takes_bulk_models_when_both_keys_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QWEN_TOKEN_PLAN_KEY", "tp-key")
    monkeypatch.setenv("QWEN_Pay_As_You_Go_PLAN_KEY", "payg-key")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    key, base_url, classify, synthesis = _get_api_config("qwen3.6-flash")

    assert key == "payg-key"
    assert "dashscope-intl" in base_url
    assert classify == "qwen3.6-flash"
    assert synthesis == "qwen3.8-max-preview"


def test_token_plan_keeps_the_max_family(monkeypatch: pytest.MonkeyPatch) -> None:
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
