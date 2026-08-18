"""CSRF-токен: подпись вместо длины строки."""

from __future__ import annotations

import time

from reddit_compass.api.ui import _generate_csrf_token, _validate_csrf_token


def test_generated_token_is_accepted() -> None:
    assert _validate_csrf_token(_generate_csrf_token()) is True


def test_arbitrary_string_of_the_right_length_is_rejected() -> None:
    """Прежняя проверка сравнивала только длину — годился любой набор символов.

    Пока UI только читал, цена была нулевой. С появлением кнопки публикации в нём
    появилось действие, меняющее то, что видят все.
    """
    assert _validate_csrf_token("a" * 64) is False
    assert _validate_csrf_token("") is False
    assert _validate_csrf_token("nonce.123.deadbeef") is False


def test_tampered_signature_is_rejected() -> None:
    token = _generate_csrf_token()
    nonce, issued, signature = token.split(".")
    assert _validate_csrf_token(f"{nonce}.{issued}.{'0' * len(signature)}") is False
    # Подмена времени выпуска тоже ломает подпись.
    assert _validate_csrf_token(f"{nonce}.{int(issued) + 1}.{signature}") is False


def test_expired_token_is_rejected(monkeypatch) -> None:
    """Час — столько, сколько человек держит страницу открытой перед нажатием."""
    token = _generate_csrf_token()
    # Оригинал берётся до патча: иначе подмена вызывает сама себя.
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 7200)
    assert _validate_csrf_token(token) is False
