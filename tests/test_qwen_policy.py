"""Стоимостная маршрутизация pay-as-you-go: квоты и леджер — без сети.

Проверяется то, что решает о деньгах: явность бесплатной квоты, выбор дешёвой модели
после её исчерпания и учёт расхода.
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
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "1000000")


def test_offpeak_window_is_17_00_03_00_msk() -> None:
    assert qwen_policy.in_offpeak(datetime(2026, 8, 5, 14, 0, tzinfo=UTC))
    assert qwen_policy.in_offpeak(datetime(2026, 8, 5, 23, 30, tzinfo=UTC))
    assert qwen_policy.in_offpeak(datetime(2026, 8, 5, 23, 59, tzinfo=UTC))
    # 00:00 UTC = 03:00 МСК — окно уже закрылось.
    assert not qwen_policy.in_offpeak(datetime(2026, 8, 5, 0, 0, tzinfo=UTC))
    assert not qwen_policy.in_offpeak(datetime(2026, 8, 5, 13, 59, tzinfo=UTC))
    assert not qwen_policy.in_offpeak(datetime(2026, 8, 5, 8, 0, tzinfo=UTC))


def test_bulk_starts_at_the_cheapest_flash_not_the_strongest_model(both_keys) -> None:
    """Массовая стадия — это разбор одной строки, и модель под неё нужна самая дешёвая.

    Регрессия: цепочка открывалась `qwen3-235b-a22b-instruct-2507`, то есть 235B на
    тысяче вызовов «разбери заголовок».
    """
    model, endpoint, why = qwen_policy.pick_model("bulk")

    assert (model, endpoint) == ("qwen3.7-flash", "payg")
    assert "подтверждённая" in why
    assert all(
        candidate[0] != "qwen3-235b-a22b-instruct-2507" for candidate in qwen_policy.BULK_CHAIN
    )


def test_bulk_falls_through_exhausted_free_quotas(both_keys, monkeypatch) -> None:
    """Переход на следующую модель цепочки оправдан только подтверждённым грантом.

    `qwen3.6-flash` дороже `qwen3.7-flash` в 8.3× по input и 11.5× по output, поэтому
    уходить на неё имеет смысл, лишь если её грант действительно отдельный. Провайдер
    семантику пула не документирует — признак включается явно.
    """
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "100")
    monkeypatch.setenv("RC_QWEN_PAYG_GRANT_PER_MODEL", "1")
    qwen_policy.record_usage(
        model="qwen3.7-flash",
        endpoint="payg",
        prompt_tokens=60,
        completion_tokens=60,
    )

    model, endpoint, _ = qwen_policy.pick_model("bulk")

    assert (model, endpoint) == ("qwen3.6-flash", "payg")


def test_bulk_keeps_the_cheapest_flash_when_all_free_quotas_are_exhausted(
    both_keys, monkeypatch
) -> None:
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "10")
    for model in ("qwen3.7-flash", "qwen3.6-flash", "qwen3.5-flash", "qwen-flash"):
        qwen_policy.record_usage(
            model=model, endpoint="payg", prompt_tokens=10, completion_tokens=10
        )

    picked_model, endpoint, _ = qwen_policy.pick_model("bulk")

    assert (picked_model, endpoint) == ("qwen3.7-flash", "payg")


def test_free_grant_beats_the_discount_window(both_keys) -> None:
    """Ноль дешевле любой скидки, а грант ещё и перегорает — значит он всегда первый.

    Регрессия: окно ставило подписку впереди бесплатного гранта. Это неверно дважды —
    скидка не бывает дешевле нуля, и подписка возобновляется каждый месяц, тогда как
    грант сгорает через 90 дней безвозвратно.
    """
    model, endpoint, why = qwen_policy.pick_model(
        "synth", now=datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
    )

    assert (model, endpoint) == ("qwen3.8-max", "payg")
    assert "бесплатная" in why


def test_synth_keeps_max_on_payg_once_the_grant_is_gone(both_keys, monkeypatch) -> None:
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "10")
    for model in ("qwen3.8-max", "qwen3.7-max", "qwen3.5-plus"):
        qwen_policy.record_usage(
            model=model, endpoint="payg", prompt_tokens=10, completion_tokens=10
        )

    picked, endpoint, why = qwen_policy.pick_model(
        "synth", now=datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
    )

    assert (picked, endpoint) == ("qwen3.8-max", "payg")
    assert "list price" in why


def test_synth_peak_prefers_confirmed_free_quota_on_the_same_model(both_keys) -> None:
    model, endpoint, _ = qwen_policy.pick_model("synth", now=datetime(2026, 8, 5, 8, 0, tzinfo=UTC))

    assert (model, endpoint) == ("qwen3.8-max", "payg")


def test_synth_peak_keeps_payg_when_free_quota_is_exhausted(both_keys, monkeypatch) -> None:
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "10")
    for model in ("qwen3.8-max", "qwen3.7-max", "qwen3.5-plus"):
        qwen_policy.record_usage(
            model=model, endpoint="payg", prompt_tokens=10, completion_tokens=10
        )

    picked_model, endpoint, _ = qwen_policy.pick_model(
        "synth", now=datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    )

    assert (picked_model, endpoint) == ("qwen3.8-max", "payg")


def test_pick_endpoint_keeps_the_model_and_moves_only_the_endpoint(both_keys) -> None:
    """Модель ревью в ключе кэша `llm_reviews`, эндпоинт — нет; значит двигаем эндпоинт.

    Пока грант цел, endpoint один и тот же в любой час: сервис не переключается на
    интерактивный Token Plan.
    """
    offpeak, why_offpeak = qwen_policy.pick_endpoint(
        "qwen3.8-max", now=datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
    )
    peak, _ = qwen_policy.pick_endpoint("qwen3.8-max", now=datetime(2026, 8, 5, 8, 0, tzinfo=UTC))

    assert (offpeak, peak) == ("payg", "payg")
    assert "бесплатная" in why_offpeak


def test_pick_endpoint_keeps_payg_when_the_grant_is_gone(both_keys, monkeypatch) -> None:
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "10")
    qwen_policy.record_usage(
        model="qwen3.8-max", endpoint="payg", prompt_tokens=10, completion_tokens=10
    )

    peak, _ = qwen_policy.pick_endpoint("qwen3.8-max", now=datetime(2026, 8, 5, 8, 0, tzinfo=UTC))
    offpeak, why_offpeak = qwen_policy.pick_endpoint(
        "qwen3.8-max", now=datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
    )

    assert (peak, offpeak) == ("payg", "payg")
    assert "list price" in why_offpeak


def test_flash_review_stays_on_payg_when_its_free_grant_is_gone(both_keys, monkeypatch) -> None:
    """Сервис не должен уходить на интерактивный Token Plan."""
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "10")
    qwen_policy.record_usage(
        model="qwen3.7-flash", endpoint="payg", prompt_tokens=10, completion_tokens=10
    )

    endpoint, why = qwen_policy.pick_endpoint(
        "qwen3.7-flash", now=datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
    )

    assert endpoint == "payg"
    assert "list price" in why


def test_token_plan_quota_is_tracked_but_not_used_for_service_routing(
    both_keys, monkeypatch
) -> None:
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


def test_unconfigured_grant_never_claims_payg_is_free(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("QWEN_PAY_AS_YOU_GO_PLAN_KEY", "payg-key")
    monkeypatch.setenv("RC_QWEN_LEDGER_PATH", str(tmp_path / "usage.db"))
    monkeypatch.delenv("RC_QWEN_PAYG_FREE_TOKENS", raising=False)

    model, endpoint, why = qwen_policy.pick_model("bulk")

    assert (model, endpoint) == ("qwen3.7-flash", "payg")
    assert "list price" in why


def test_usage_before_grant_start_does_not_count(both_keys, monkeypatch) -> None:
    """Леджер копится вечно, грант живёт 90 дней: расход прошлого гранта — не наш."""
    qwen_policy.record_usage(
        model="qwen3.7-flash",
        endpoint="payg",
        prompt_tokens=900_000,
        completion_tokens=900_000,
    )
    monkeypatch.setenv("RC_QWEN_PAYG_GRANT_START", "2099-01-01")

    totals = qwen_policy.usage_totals(since=qwen_policy.payg_grant_start())

    assert totals == {}
    assert qwen_policy.pick_model("bulk")[:2] == ("qwen3.7-flash", "payg")


def test_expired_grant_leaves_no_free_room(both_keys, monkeypatch) -> None:
    """Истёкший грант — не бесплатное место, но модель остаётся на pay-as-you-go."""
    monkeypatch.setenv("RC_QWEN_PAYG_GRANT_START", "2020-01-01")

    assert qwen_policy.payg_grant_expired()
    assert qwen_policy.pick_model("bulk")[:2] == ("qwen3.7-flash", "payg")


def test_unknown_grant_start_keeps_counting_whole_history(both_keys, monkeypatch) -> None:
    """Дата не задана — поведение прежнее: молча ужесточать по неизвестной дате нельзя."""
    monkeypatch.delenv("RC_QWEN_PAYG_GRANT_START", raising=False)
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "100")
    qwen_policy.record_usage(
        model="qwen3.7-flash",
        endpoint="payg",
        prompt_tokens=60,
        completion_tokens=60,
    )

    monkeypatch.setenv("RC_QWEN_PAYG_GRANT_PER_MODEL", "1")

    assert not qwen_policy.payg_grant_expired()
    assert qwen_policy.pick_model("bulk")[:2] == ("qwen3.6-flash", "payg")


def test_payg_only_model_never_routes_to_token_plan(monkeypatch) -> None:
    """`qwen3.7-flash` на token-plan не существует — молча уводить её туда нельзя.

    Guard проверял `endpoint == "token-plan"`, но эту строку сюда не передаёт никто:
    обе цепочки политики — payg, а `pick_endpoint` отдаёт `"payg"` либо `""`, которое
    вызывающие приводят к `None`. Блок был недостижим, и на деплое только с
    `QWEN_TOKEN_PLAN_KEY` каждый review-джоб уходил на token-plan URL и получал 404.
    """
    from reddit_compass.signals import QwenConfigError, _get_api_config

    for var in ("DASHSCOPE_API_KEY", "QWEN_PAY_AS_YOU_GO_PLAN_KEY", "QWEN_Pay_As_You_Go_PLAN_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QWEN_TOKEN_PLAN_KEY", "tp-key")

    with pytest.raises(QwenConfigError) as excinfo:
        _get_api_config("qwen3.7-flash", None)

    assert "pay-as-you-go" in str(excinfo.value)


def test_payg_only_model_uses_the_payg_key_whatever_the_endpoint(monkeypatch) -> None:
    """При наличии payg-ключа модель обязана уйти на payg с любым endpoint-аргументом."""
    from reddit_compass.signals import _DASHSCOPE_INTL_URL, _get_api_config

    monkeypatch.setenv("QWEN_TOKEN_PLAN_KEY", "tp-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "payg-key")
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)

    for endpoint in (None, "payg", "token-plan"):
        key, base_url, _, _ = _get_api_config("qwen3.7-flash", endpoint)
        assert (key, base_url) == ("payg-key", _DASHSCOPE_INTL_URL)


def test_quota_env_garbage_falls_back_instead_of_crashing(monkeypatch) -> None:
    """`RC_QWEN_PAYG_FREE_TOKENS=1M` роняло стадию трейсбеком из голого `int(raw)`."""
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "1M")
    assert qwen_policy.payg_free_quota() == qwen_policy.DEFAULT_PAYG_FREE_TOKENS

    # Отрицательное принималось молча и означало «нет квоты» — неотличимо от опечатки.
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "-5")
    assert qwen_policy.payg_free_quota() == qwen_policy.DEFAULT_PAYG_FREE_TOKENS

    monkeypatch.setenv("RC_QWEN_TOKEN_PLAN_TOKENS", "не число")
    assert qwen_policy.token_plan_quota() is None


def test_malformed_grant_start_does_not_silently_disable_expiry(monkeypatch) -> None:
    """Битая дата гранта делала `payg_grant_expired` вечно ложной без единого следа."""
    monkeypatch.setenv("RC_QWEN_PAYG_GRANT_START", "05.08.2026")
    assert qwen_policy.payg_grant_start() is None

    # Смещение больше не отбрасывается: `.replace(tzinfo=UTC)` сдвигал окно на три часа.
    monkeypatch.setenv("RC_QWEN_PAYG_GRANT_START", "2026-08-05T00:00:00+03:00")
    start = qwen_policy.payg_grant_start()
    assert start is not None
    assert start.isoformat() == "2026-08-04T21:00:00+00:00"


def test_free_quota_is_an_account_pool_unless_confirmed_per_model(monkeypatch, tmp_path) -> None:
    """Общий пул по умолчанию: иначе роутер уводит извлечение на модель дороже в 8×.

    Цепочка bulk падает с `qwen3.7-flash` на `qwen3.6-flash`, который по собственной
    таблице цен дороже в 8.3× по input и 11.5× по output. Это оправдано, только если
    грант действительно свой у каждой модели. Провайдер этого не документирует, поэтому
    по умолчанию пул считается общим: ошибка в эту сторону оставляет на самой дешёвой
    модели, ошибка в обратную — платит по восьмикратному тарифу.
    """
    monkeypatch.setenv("RC_QWEN_LEDGER_PATH", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("RC_QWEN_PAYG_FREE_TOKENS", "1000")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "payg-key")
    monkeypatch.delenv("RC_QWEN_PAYG_GRANT_PER_MODEL", raising=False)
    qwen_policy.record_usage(
        model="qwen3.7-flash", endpoint="payg", prompt_tokens=900, completion_tokens=200
    )

    model, _, reason = qwen_policy.pick_model("bulk")
    assert model == "qwen3.7-flash"
    assert "list price" in reason

    monkeypatch.setenv("RC_QWEN_PAYG_GRANT_PER_MODEL", "1")
    per_model, _, per_model_reason = qwen_policy.pick_model("bulk")
    assert per_model == "qwen3.6-flash"
    assert "бесплатн" in per_model_reason


def test_cost_report_prices_by_stage_and_keeps_unpriced_separate(monkeypatch, tmp_path) -> None:
    """Отчёт считает по list price и не выдаёт неизвестную цену за ноль.

    Складывать вызовы без цены с нулевой стоимостью значило бы занизить расход именно
    там, где он не проверен.
    """
    monkeypatch.setenv("RC_QWEN_LEDGER_PATH", str(tmp_path / "ledger.db"))
    qwen_policy.record_usage(
        model="qwen3.7-flash",
        endpoint="payg",
        prompt_tokens=1_200_000,
        completion_tokens=300_000,
        stage="schema_extract",
    )
    qwen_policy.record_usage(
        model="qwen-unknown",
        endpoint="payg",
        prompt_tokens=1000,
        completion_tokens=500,
        stage="classify",
    )

    report = qwen_policy.cost_report()

    by_stage = {row["stage"]: row for row in report["rows"]}
    # 1.2M × 0.225/1M + 0.3M × 0.974/1M = 0.27 + 0.2922
    assert by_stage["schema_extract"]["cost_cny"] == 0.5622
    assert by_stage["classify"]["cost_cny"] is None
    assert report["total_cny"] == 0.5622
    assert report["unpriced_calls"] == 1
    assert report["price_source_date"] == qwen_policy.LIST_PRICES_SOURCE_DATE


def test_unlabelled_usage_is_reported_separately_not_dissolved(monkeypatch, tmp_path) -> None:
    """Записи без стадии (леджеры до этой версии) обязаны быть видны отдельной строкой."""
    monkeypatch.setenv("RC_QWEN_LEDGER_PATH", str(tmp_path / "ledger.db"))
    qwen_policy.record_usage(
        model="qwen3.7-flash", endpoint="payg", prompt_tokens=100, completion_tokens=100
    )

    stages = {row["stage"] for row in qwen_policy.cost_report()["rows"]}

    assert stages == {"(не размечено)"}


def test_spend_guard_stops_the_call_before_it_is_paid_for(monkeypatch, tmp_path) -> None:
    """Потолок проверяется до вызова: смысл в том, чтобы не потратить."""
    monkeypatch.setenv("RC_QWEN_LEDGER_PATH", str(tmp_path / "ledger.db"))
    monkeypatch.delenv("RC_QWEN_PAYG_GRANT_START", raising=False)
    qwen_policy.record_usage(
        model="qwen3.8-max",
        endpoint="payg",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        stage="synthesis",
    )

    # Без потолка поведение прежнее — неявного лимита не появляется.
    monkeypatch.delenv("RC_QWEN_MAX_SPEND_CNY", raising=False)
    qwen_policy.check_spend_guard("qwen3.8-max")

    monkeypatch.setenv("RC_QWEN_MAX_SPEND_CNY", "10")
    with pytest.raises(RuntimeError, match="достиг"):
        qwen_policy.check_spend_guard("qwen3.8-max")

    # Нечисловой потолок не роняет прогон, а лишь не применяется.
    monkeypatch.setenv("RC_QWEN_MAX_SPEND_CNY", "десять")
    qwen_policy.check_spend_guard("qwen3.8-max")


def test_timeout_marks_the_call_as_unmetered_instead_of_losing_it(monkeypatch, tmp_path) -> None:
    """Отменённый по timeout вызов провайдер уже оплатил — молчать об этом нельзя.

    Токены списываются в момент генерации, а не получения ответа: `record_usage`
    вызывается только после 200 и потому не срабатывает. Роутер прочитал бы расход как
    меньший, чем он есть, и решил бы, что бесплатной квоты ещё много. Записывать оценку
    токенов нельзя — это выдуманное число, поэтому фиксируется сам факт.
    """
    monkeypatch.setenv("RC_QWEN_LEDGER_PATH", str(tmp_path / "ledger.db"))
    qwen_policy.record_usage(
        model="qwen3.8-max",
        endpoint="payg",
        prompt_tokens=1000,
        completion_tokens=500,
        stage="trend_review",
    )
    qwen_policy.record_unmetered_call(
        model="qwen3.8-max", endpoint="payg", stage="trend_review", reason="timeout 240s"
    )

    report = qwen_policy.cost_report()

    assert report["unmetered_calls"] == 1
    assert report["unmetered_detail"][0]["stage"] == "trend_review"
    assert "timeout" in report["unmetered_detail"][0]["reason"]
    # Сумма остаётся нижней границей: токены отменённого вызова неизвестны и не выдуманы.
    assert report["total_cny"] > 0


def test_cost_report_without_timeouts_reports_zero_unmetered(monkeypatch, tmp_path) -> None:
    """Пустой счётчик — сигнал, что оценка расхода полна, а не что учёта нет."""
    monkeypatch.setenv("RC_QWEN_LEDGER_PATH", str(tmp_path / "ledger.db"))
    qwen_policy.record_usage(
        model="qwen3.7-flash", endpoint="payg", prompt_tokens=100, completion_tokens=50
    )

    report = qwen_policy.cost_report()

    assert report["unmetered_calls"] == 0
    assert report["unmetered_detail"] == []
