"""Сила тренда Engine: что она ранжирует и как затухает.

Не путать с `test_trend_strength.py`: тот проверяет доэнджинный расчёт по снапшотам
`signals.jsonl`, здесь — тренды immutable-выпуска.
"""

from __future__ import annotations

from datetime import date

import pytest

from reddit_compass.intelligence.trend_ranking import (
    HALF_LIFE_DAYS,
    is_dormant,
    trend_strength,
)

TODAY = date(2026, 8, 16)


def _s(story_count: int, actors: int, confirmed: bool, last_seen: str) -> float:
    return trend_strength(
        story_count=story_count,
        distinct_actors=actors,
        confirmed=confirmed,
        last_seen=last_seen,
        today=TODAY,
    )


def test_decay_halves_the_weight_every_two_days() -> None:
    fresh = _s(20, 5, False, "2026-08-16")
    two_days = _s(20, 5, False, "2026-08-14")
    four_days = _s(20, 5, False, "2026-08-12")

    assert two_days == pytest.approx(fresh / 2, rel=0.02)
    assert four_days == pytest.approx(fresh / 4, rel=0.02)
    assert HALF_LIFE_DAYS == 2.0


def test_volume_is_logarithmic_so_the_residual_bucket_cannot_own_the_top() -> None:
    """Разница между одним и пятью говорит больше, чем между сорока и пятьюдесятью.

    Без логарифма остаточная корзина «в прочих доменах» с полусотней сюжетов занимала бы
    верх выдачи вечно — она крупная просто потому, что вбирает всё неразобранное.
    """
    small_jump = _s(5, 5, False, "2026-08-16") - _s(1, 5, False, "2026-08-16")
    large_jump = _s(50, 5, False, "2026-08-16") - _s(40, 5, False, "2026-08-16")

    assert small_jump > large_jump


def test_breadth_separates_a_trend_from_one_company_chronicle() -> None:
    """Пять новостей одной компании — это хроника компании, а не тренд."""
    assert _s(10, 5, False, "2026-08-16") > _s(10, 1, False, "2026-08-16")


def test_verification_lifts_but_does_not_overturn_scale() -> None:
    """Подтверждение — сильный сигнал, но не сильнее разницы в порядок величины."""
    confirmed_small = _s(4, 3, True, "2026-08-16")

    assert confirmed_small > _s(4, 3, False, "2026-08-16")
    assert _s(60, 5, False, "2026-08-16") > confirmed_small


def test_freshness_beats_stale_bulk() -> None:
    """Смысл распада: свежий средний тренд важнее крупного позавчерашнего."""
    assert _s(12, 4, False, "2026-08-16") > _s(60, 5, False, "2026-08-12")


def test_dormancy_comes_later_for_a_trend_that_mattered_more() -> None:
    """Порог один, но крупный тренд держится дольше мелкого — и это правильно.

    Он и был заметнее, значит его затухание читателю интереснее. Мелкий уходит примерно
    через неделю без материала, крупный — через полторы.
    """
    assert not is_dormant(_s(20, 5, True, "2026-08-16"))
    assert is_dormant(_s(3, 1, False, "2026-08-08"))
    assert not is_dormant(_s(60, 5, False, "2026-08-08"))


def test_reader_survives_a_schema_older_than_its_code(tmp_path) -> None:
    """API читает базу, которую мигрирует писатель, — и может опередить его.

    Колонки добавляются при записи, то есть ночным циклом, а страницы читают базу
    только на чтение. Между выкаткой кода и первым прогоном схема на диске старше кода,
    и запрос, упомянувший новую колонку, уронил бы всю страницу в пятисотку.

    16 августа это едва не случилось на бою: колонку `strength` спас лишь порядок шагов
    деплоя — `version --record` открывает базу на запись и попутно мигрирует.
    """
    import sqlite3

    from reddit_compass.api.v2 import _has_column

    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE engine_trends (trend_id TEXT, review_status TEXT)")
    conn.commit()

    assert _has_column(conn, "engine_trends", "strength") is False
    assert _has_column(conn, "engine_trends", "review_status") is True
    # Несуществующая таблица тоже не роняет читателя.
    assert _has_column(conn, "no_such_table", "strength") is False
