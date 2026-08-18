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


def test_fading_trend_survives_the_window_then_goes_quiet(tmp_path) -> None:
    """Тренд, выпавший из окна, доживает на странице и уходит сам.

    До этого он исчезал в тот день, когда переставал попадать в семидневное окно, —
    читатель видел обрыв вместо затухания. Решает не счётчик выпусков, а сила: она
    падает сама, и тренд тонет в выдаче задолго до того, как пропасть.
    """
    from datetime import date as _date

    from reddit_compass.intelligence.engine import (
        carry_over_fading_trends,
        engine_db,
        record_trend_history,
    )

    conn = engine_db(tmp_path / "trend_engine.db")
    live = [
        (
            {
                "trend_id": "trend_launch_ai",
                "name_ru": "product launches in AI",
                "pattern": "p",
                "domain_ids": ["ai_technology"],
                "first_seen": "2026-08-10",
                "last_seen": "2026-08-12",
                "story_count": 30,
                "source_count": 6,
                "distinct_actors": ["OpenAI", "Anthropic", "Google", "Meta", "Mistral"],
                "review_status": "confirmed",
            },
            [],
        )
    ]
    record_trend_history(conn, live, story_release_id="stories_old", now="2026-08-12T16:00:00Z")

    # Через двое суток тренда нет в свежем выпуске — но он ещё заметен и переносится.
    carried = carry_over_fading_trends(conn, [], today=_date(2026, 8, 14))
    assert [t["trend_id"] for t, _ in carried] == ["trend_launch_ai"]
    faded = carried[0][0]
    assert faded["lifecycle"] == "fading"
    assert faded["carried_from_release"] == "stories_old"
    # Материал остался в прежнем выпуске: сюжетов у перенесённого нет.
    assert carried[0][1] == []

    # Через две недели он уже не переносится — угас.
    assert carry_over_fading_trends(conn, [], today=_date(2026, 8, 26)) == []

    # Пока тренд жив в сегодняшнем выпуске, дубля переносом не возникает.
    same_day = carry_over_fading_trends(conn, live, today=_date(2026, 8, 14))
    assert len(same_day) == 1


def test_fading_trends_are_capped_so_they_cannot_crowd_out_fresh(tmp_path) -> None:
    """За окном живёт длинный хвост слабых трендов — числом, а не силой.

    Замер 16 августа: порог 0.15 переносил 28 трендов из 59 спустя неделю, то есть
    архив рядом со свежим; порог 1.0 не переносил ни одного. Одним порогом это не
    разводится, потому что тренд покидает семидневное окно уже на 9 % от базы.
    Ограничение по числу отвечает на другой вопрос — сколько места затухающее вправе
    занимать рядом со свежим.
    """
    from datetime import date as _date

    from reddit_compass.intelligence.engine import (
        carry_over_fading_trends,
        engine_db,
        record_trend_history,
    )
    from reddit_compass.intelligence.trend_ranking import MAX_CARRIED_TRENDS

    conn = engine_db(tmp_path / "trend_engine.db")
    many = [
        (
            {
                "trend_id": f"trend_{index:03}",
                "name_ru": f"trend {index}",
                "pattern": "p",
                "domain_ids": ["business"],
                "first_seen": "2026-08-10",
                "last_seen": "2026-08-12",
                # Размер убывает — значит и сила, и порядок переноса предсказуемы.
                "story_count": 60 - index,
                "source_count": 4,
                "distinct_actors": ["a", "b", "c", "d", "e"],
                "review_status": "pending",
            },
            [],
        )
        for index in range(30)
    ]
    record_trend_history(conn, many, story_release_id="stories_old", now="2026-08-12T16:00:00Z")

    carried = carry_over_fading_trends(conn, [], today=_date(2026, 8, 13))

    assert len(carried) == MAX_CARRIED_TRENDS
    # Переносятся именно сильнейшие, а не первые попавшиеся.
    assert [t["trend_id"] for t, _ in carried] == [
        f"trend_{i:03}" for i in range(MAX_CARRIED_TRENDS)
    ]


def test_rejected_trend_is_not_resurrected_as_fading(tmp_path) -> None:
    """Отказ ревью — утверждение о связности, а не о свежести: затухать нечему.

    Замер 17 августа: все десять «затухающих» в выпуске оказались ровно теми трендами,
    которые ревью выбросило минутой раньше. Перенос молча отменял его работу — и делал
    это тем заметнее, чем крупнее был отвергнутый тренд, потому что сила ставила его
    высоко в выдаче.
    """
    from datetime import date as _date

    from reddit_compass.intelligence.engine import (
        carry_over_fading_trends,
        engine_db,
        record_trend_history,
    )

    conn = engine_db(tmp_path / "trend_engine.db")
    history = [
        (
            {
                "trend_id": tid,
                "name_ru": tid,
                "pattern": "p",
                "domain_ids": ["business"],
                "first_seen": "2026-08-10",
                "last_seen": "2026-08-15",
                "story_count": 40,
                "source_count": 5,
                "distinct_actors": ["a", "b", "c", "d", "e"],
                "review_status": "pending",
            },
            [],
        )
        for tid in ("trend_kept", "trend_rejected")
    ]
    record_trend_history(conn, history, story_release_id="stories_old", now="2026-08-15T16:00:00Z")

    carried = carry_over_fading_trends(
        conn, [], today=_date(2026, 8, 16), rejected_ids={"trend_rejected"}
    )

    assert [t["trend_id"] for t, _ in carried] == ["trend_kept"]


def test_fading_trend_keeps_an_evidence_snapshot(tmp_path) -> None:
    """У затухающего тренда есть чем себя показать, хотя его сюжеты вне выпуска.

    Обычный JOIN с сюжетами текущего релиза даёт пустоту: материал остался в прежнем.
    Без снимка страница показывала бы тренд без единой карточки, и читатель принимал бы
    нормальное состояние за поломку.
    """
    from datetime import date as _date

    from reddit_compass.intelligence.engine import (
        carry_over_fading_trends,
        engine_db,
        record_trend_history,
    )

    conn = engine_db(tmp_path / "trend_engine.db")
    conn.execute(
        """INSERT INTO engine_stories
           (story_release_id, story_id, canonical_key, title, source_count, item_count,
            first_seen, last_seen)
           VALUES ('stories_old', 'story_a', 'k', 'OpenAI выпустила модель', 3, 4,
                   '2026-08-12', '2026-08-14')"""
    )
    conn.commit()
    live = [
        (
            {
                "trend_id": "trend_launch_ai",
                "name_ru": "product launches in AI",
                "pattern": "p",
                "domain_ids": ["ai_technology"],
                "first_seen": "2026-08-10",
                "last_seen": "2026-08-14",
                "story_count": 20,
                "source_count": 5,
                "distinct_actors": ["a", "b", "c", "d", "e"],
                "review_status": "confirmed",
            },
            [("story_a", 1.0, "")],
        )
    ]
    record_trend_history(conn, live, story_release_id="stories_old", now="2026-08-14T16:00:00Z")

    carried = carry_over_fading_trends(conn, [], today=_date(2026, 8, 16))

    assert len(carried) == 1
    evidence = carried[0][0]["carried_evidence"]
    assert [item["title"] for item in evidence] == ["OpenAI выпустила модель"]
    # Дата снимка сохранена: старый материал не должен выглядеть сегодняшним.
    assert evidence[0]["last_seen"] == "2026-08-14"
