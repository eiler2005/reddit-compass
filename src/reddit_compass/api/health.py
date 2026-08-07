"""Свежесть данных для ``/health``.

Зачем отдельно от «жив/не жив»: ночной конвейер стоит из семи стадий по крону, и его
отказ ничем себя не проявляет. Контейнер продолжает отвечать `ok`, UI продолжает
показывать вчерашний выпуск, а узнать о простое можно было только открыв страницу и
заметив старую дату. Логи при этом лежат в файле, который никто не читает.

Поэтому `/health` отвечает ещё и на вопрос «когда конвейер в последний раз доводил
работу до публикации». Признак завершённого цикла — именно публикация: она создаётся
последней и только после того, как полы качества пройдены.

Три состояния, а не два. Отсутствие данных вообще (свежая установка, локальный запуск,
тесты) — это `unknown`, а не тревога: иначе первый же деплой на пустой базе объявлял бы
аварию, и сигнал обесценился бы до того, как понадобился.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from typing import Any

# Порог с запасом на одну пропущенную ночь: `flock -n` пропускает прогон, а не ставит его
# в очередь, поэтому одиночный пропуск — штатное поведение, а не отказ. Тревожит второй
# подряд. Сутки + запас на сдвиг расписания = 36 часов.
DEFAULT_MAX_DATA_AGE_HOURS = 36.0


def max_data_age_hours() -> float:
    """Порог простоя; нечисловое значение не роняет ``/health``, а берёт умолчание."""
    raw = os.environ.get("RC_HEALTH_MAX_DATA_AGE_HOURS", "").strip()
    if not raw:
        return DEFAULT_MAX_DATA_AGE_HOURS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MAX_DATA_AGE_HOURS
    return value if value > 0 else DEFAULT_MAX_DATA_AGE_HOURS


def _parse_utc(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def data_freshness(
    conn: sqlite3.Connection | None,
    *,
    now: datetime | None = None,
    max_age_hours: float | None = None,
) -> dict[str, Any]:
    """Когда конвейер в последний раз довёл цикл до публикации.

    Берётся самая свежая публикация в любом канале: ночной прогон публикует в `shadow`,
    ручной релиз — в `broad`, и для вопроса «конвейер работает?» важнее свежесть, чем
    канал.
    """
    threshold = max_age_hours if max_age_hours is not None else max_data_age_hours()
    moment = now or datetime.now(UTC)
    empty: dict[str, Any] = {
        "data_status": "unknown",
        "last_publication_at": "",
        "age_hours": None,
        "max_age_hours": threshold,
    }
    if conn is None:
        return empty
    try:
        row = conn.execute(
            "SELECT publication_id, channel, created_at FROM radar_publications "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        # База есть, но таблицы ещё нет либо она занята: это неизвестность, а не авария.
        return empty
    if row is None:
        return empty
    created = _parse_utc(str(row["created_at"]))
    if created is None:
        return empty
    age = (moment - created).total_seconds() / 3600
    return {
        "data_status": "stale" if age > threshold else "ok",
        "last_publication_at": str(row["created_at"]),
        "last_publication_id": str(row["publication_id"]),
        "last_publication_channel": str(row["channel"]),
        "age_hours": round(age, 1),
        "max_age_hours": threshold,
    }
