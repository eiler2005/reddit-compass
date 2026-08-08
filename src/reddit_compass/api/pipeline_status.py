"""Состояние ночного конвейера для читателя: факт отдельно, расписание отдельно.

Зачем: страница показывала дату последнего опубликованного выпуска и ничего не говорила
о том, почему она такая. Открыв `/today` в разгар прогона, читатель видел вчерашнее
число и не мог отличить «сегодняшние данные ещё считаются» от «конвейер сломался
неделю назад».

Ключевое решение — **никогда не выводить состояние из часов**. Соблазн велик: расписание
известно, время известно, значит в 16:10 можно нарисовать «идёт анализ». Но ровно в тот
момент, когда это важно — цикл упал в 16:02 — такая полоса будет бодро показывать
работу, которой нет. Поэтому «сделано» говорится только про наблюдаемый артефакт с его
временем, а расписание показывается как ожидание и подписано словом «ожидается».

Отсюда третье состояние. Стадия без артефакта — это либо `pending` (срок ещё не вышел),
либо `late` (вышел). Различает их только время, но ни то ни другое не выдаёт себя за
«работает»: изнутри БД неотличимо, идёт ли стадия прямо сейчас или упала минуту назад.

Прогресс при этом виден честно, без heartbeat: конвейер материализует артефакты по
порядку — Data Release, потом Stories, потом Trends, потом публикация. Замер на живом
прогоне 8 августа в 16:18 (цикл шёл 18 минут) показал ровно это:

    data_releases    2026-08-02_2026-08-08-broad-r1  finalized  16:00:02
    story_releases   stories_0918a22194487c4b5477    evaluated  16:00:15
    trend_releases   — пусто

То есть середина цикла наблюдаема сама по себе, и отдельная запись «я работаю» не нужна.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from datetime import date as date_cls
from typing import Any

# Расписание в UTC — то же, что в `deploy/hostkey/reddit-compass.cron`. Здесь оно нужно
# только чтобы отличить «ещё рано» от «уже поздно», и никогда — чтобы объявить стадию
# выполненной.
COLLECTION_DONE_AT = time(15, 15)
ENGINE_STARTS_AT = time(16, 0)
# Цикл идёт 25–30 минут (два прохода cross-encoder). Запас до часа: задержка сети или
# конкуренция за CPU не должны красить нормальный прогон в аварию.
ENGINE_GRACE = timedelta(minutes=60)
COLLECTION_GRACE = timedelta(minutes=45)

STATE_DONE = "done"
STATE_PENDING = "pending"
STATE_LATE = "late"


@dataclass(frozen=True)
class Stage:
    key: str
    title: str
    state: str
    detail: str
    at: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "title": self.title,
            "state": self.state,
            "detail": self.detail,
            "at": self.at,
        }


def _hhmm(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) >= 16 and text[10] in "T ":
        return text[11:16]
    return text[:16]


def _expected_state(now: datetime, deadline: time, grace: timedelta) -> str:
    """`pending`, пока срок не вышел; дальше `late`. Никогда не «работает»."""
    due = datetime.combine(now.date(), deadline, tzinfo=UTC) + grace
    return STATE_LATE if now > due else STATE_PENDING


def _collection_stage(corpus_conn: sqlite3.Connection | None, today: str, now: datetime) -> Stage:
    # Сбой чтения — не «ещё не начиналось». Прежде и недоступная база, и не наступивший
    # срок давали одно `pending`, то есть поломка выглядела благополучием — тот же
    # рисунок, что в `/health` и в `selfcheck.sh`.
    if corpus_conn is None:
        return Stage("collect", "Сбор источников", STATE_LATE, "нет доступа к корпусу")
    try:
        row = corpus_conn.execute(
            "SELECT run_id, status, started_at, finished_at FROM runs WHERE snapshot_date = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (today,),
        ).fetchone()
    except sqlite3.Error:
        return Stage("collect", "Сбор источников", STATE_LATE, "журнал запусков недоступен")
    if row is None:
        return Stage(
            "collect",
            "Сбор источников",
            _expected_state(now, COLLECTION_DONE_AT, COLLECTION_GRACE),
            f"ожидается к {COLLECTION_DONE_AT:%H:%M} UTC",
        )
    # Счётчика материалов в `runs` нет — он собирается по источникам в `source_health`.
    #
    # Фильтр по двоеточию обязателен: таблица держит два уровня разом. Сводные строки
    # адаптеров (`reddit`, `rss`, …) и построчные ленты (`bbc:business`,
    # `arstechnica:top`) дают одну и ту же сумму каждая. Замер 8 августа: 5 сводных строк
    # на 3060 материалов и 124 ленточных ещё на 3060. Простое `SUM(count)` показало
    # читателю 6120 материалов из 129 источников — оба числа неверны вдвое и в двадцать
    # шесть раз соответственно.
    try:
        health = corpus_conn.execute(
            "SELECT status, count FROM source_health WHERE run_id = ? AND source_id NOT LIKE '%:%'",
            (str(row["run_id"]),),
        ).fetchall()
    except sqlite3.Error:
        health = []
    count = sum(int(item["count"] or 0) for item in health)
    ready = sum(1 for item in health if str(item["status"]) == "ok")
    status = str(row["status"] or "")
    when = _hhmm(str(row["finished_at"] or row["started_at"]))
    detail = (
        f"{count} материалов, источников {ready}/{len(health)}"
        if health
        else "источники не записаны"
    )
    if status == "complete":
        return Stage("collect", "Сбор источников", STATE_DONE, detail, when)
    return Stage(
        "collect",
        "Сбор источников",
        _expected_state(now, COLLECTION_DONE_AT, COLLECTION_GRACE),
        f"{status}: {detail}",
        when,
    )


def _latest(conn: sqlite3.Connection, sql: str, since: datetime) -> sqlite3.Row | None:
    try:
        row: sqlite3.Row | None = conn.execute(sql, (since.isoformat(),)).fetchone()
    except sqlite3.Error:
        return None
    return row


def pipeline_status(
    engine_conn: sqlite3.Connection | None,
    corpus_conn: sqlite3.Connection | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Пять стадий сегодняшнего прогона, каждая — факт либо ожидание."""
    moment = now or datetime.now(UTC)
    today: str = moment.date().isoformat() if isinstance(moment.date(), date_cls) else ""
    # Отсчёт от полуночи UTC: артефакт вчерашнего прогона не должен красить сегодняшний
    # день в «готово».
    since = datetime.combine(moment.date(), time(0, 0), tzinfo=UTC)

    stages = [_collection_stage(corpus_conn, today, moment)]

    if engine_conn is None:
        for key, title in (
            ("freeze", "Заморозка выпуска"),
            ("stories", "Сюжеты"),
            ("trends", "Тренды"),
            ("publish", "Публикация"),
        ):
            stages.append(Stage(key, title, STATE_LATE, "движок недоступен"))
        return {"date": today, "stages": [s.as_dict() for s in stages], "channel_note": ""}

    data_row = _latest(
        engine_conn,
        "SELECT release_id, created_at FROM data_releases WHERE created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        since,
    )
    story_row = _latest(
        engine_conn,
        "SELECT story_release_id, created_at FROM story_releases WHERE created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        since,
    )
    trend_row = _latest(
        engine_conn,
        "SELECT trend_release_id, created_at FROM trend_releases WHERE created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        since,
    )
    shadow_row = _latest(
        engine_conn,
        "SELECT publication_id, channel, created_at FROM radar_publications "
        "WHERE created_at >= ? ORDER BY created_at DESC LIMIT 1",
        since,
    )

    engine_expected = f"ожидается после {ENGINE_STARTS_AT:%H:%M} UTC"
    for key, title, row, done_detail in (
        ("freeze", "Заморозка выпуска", data_row, "выпуск заморожен"),
        ("stories", "Сюжеты", story_row, "сюжеты собраны"),
        ("trends", "Тренды", trend_row, "тренды построены"),
    ):
        if row is None:
            stages.append(
                Stage(
                    key,
                    title,
                    _expected_state(moment, ENGINE_STARTS_AT, ENGINE_GRACE),
                    engine_expected,
                )
            )
        else:
            stages.append(Stage(key, title, STATE_DONE, done_detail, _hhmm(str(row["created_at"]))))

    # Стадию публикации меряем по каналу `broad`, а не по любому. Гейт применяется только
    # к нему: shadow публикуется всегда, поэтому «есть какая-то публикация» ничего не
    # говорит читателю. Выпуск, отклонённый гейтом, оставлял shadow свежим, broad
    # вчерашним — и все три сигнала наблюдаемости отвечали «всё хорошо», пока сайт молча
    # стоял: полоса показывала «Публикация ✓ канал shadow», возраст данных был под
    # 36-часовым порогом, вердикт писал OK. Автоматическое продвижение сделало этот
    # разрыв возможным, значит он обязан быть видимым.
    broad_row = _latest(
        engine_conn,
        "SELECT publication_id, channel, created_at FROM radar_publications "
        "WHERE created_at >= ? AND channel = 'broad' ORDER BY created_at DESC LIMIT 1",
        since,
    )
    if broad_row is not None:
        stages.append(
            Stage(
                "publish",
                "Публикация",
                STATE_DONE,
                "выпуск на сайте",
                _hhmm(str(broad_row["created_at"])),
            )
        )
    elif shadow_row is not None:
        # Выпуск построен и опубликован в служебный канал, но на сайт не пущен. Это не
        # ожидание: конвейер свою работу закончил, решение уже принято и оно
        # отрицательное.
        stages.append(
            Stage(
                "publish",
                "Публикация",
                STATE_LATE,
                "выпуск построен, но не прошёл гейт качества — на сайте прежний",
                _hhmm(str(shadow_row["created_at"])),
            )
        )
    else:
        stages.append(
            Stage(
                "publish",
                "Публикация",
                _expected_state(moment, ENGINE_STARTS_AT, ENGINE_GRACE),
                engine_expected,
            )
        )

    return {
        "date": today,
        "stages": [stage.as_dict() for stage in stages],
        # Раньше здесь было «переключается вручную». С 8 августа продвижение
        # автоматическое, и оставить прежний текст значило бы врать читателю о том,
        # как работает система, — та же ошибка, что и полоса, выводящая состояние из
        # часов вместо фактов.
        "channel_note": (
            "Свежий выпуск сначала попадает в служебный канал shadow и оттуда "
            "переходит на сайт автоматически — но только если пройдены полы качества "
            "и нет просадки относительно эталонного выпуска."
        ),
    }
