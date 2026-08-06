"""Единая нормализация дат публикации для сортировки и показа.

`release_items.published_at` не нормализован по формату — он такой, каким его отдал
провайдер. В боевом broad-релизе на 4957 материалов это три разных представления:

    3219  ISO-8601      `2026-07-29T06:59:37Z`               (Reddit, Hacker News)
    1414  RFC 2822      `Wed, 29 Jul 2026 06:59:37 GMT`      (RSS)
     324  пусто         → откат на `observed_at`/`snapshot_date`

Пока дата была пятым ключом сортировки, сравнение этих строк как строк ни на что не
влияло — до него доходило только при полном совпадении четырёх предыдущих. Когда она
стала первичным ключом, `/news?sort=fresh` начал сортировать по **названию дня недели**:
лексикографически `W > T > S > M > F > "2"`, поэтому сначала шли все среды, затем
вторники, и только после всех RSS — 3219 материалов Reddit и HN независимо от свежести.

Отсюда две функции ниже: `sort_key` для порядка и `display_date` для показа.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

# Недатированный материал обязан оказаться последним в **обоих** направлениях. Пустая
# строка сортировалась бы первой по возрастанию, и «Сначала раннее» открывалось бы
# материалами вообще без даты.
_UNDATED_ASC = "9999-12-31T23:59:59+00:00"
_UNDATED_DESC = "0000-01-01T00:00:00+00:00"


def parse_published(raw: str | None) -> datetime | None:
    """Разобрать дату публикации в любом из форматов, которые отдают провайдеры.

    Возвращает ``None`` на пустом или неразбираемом значении: испорченная дата у одного
    материала не имеет права ронять выдачу целиком.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if parsed is None:
        return None
    # Без таймзоны считаем UTC: сравнивать naive и aware нельзя, а провайдеры,
    # опускающие смещение, публикуют в UTC.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def sort_key(raw: str | None, *, ascending: bool = False) -> str:
    """Сопоставимый ключ сортировки: один формат для всех провайдеров.

    ``ascending`` задаёт, куда уедут недатированные материалы, — в обоих случаях в конец.
    """
    parsed = parse_published(raw)
    if parsed is None:
        return _UNDATED_ASC if ascending else _UNDATED_DESC
    return parsed.isoformat()


def display_date(raw: str | None) -> str:
    """Дата для подписи: один вид вместо трёх соседствующих форматов.

    В одной колонке рядом стояли `Wed, 29 Jul 2026 06:59:37 GMT`, `2026-07-29T06:59:37Z`
    и `2026-07-27T21:19:31.983321Z` — последний с микросекундами, из отката на
    `observed_at`. Читателю нужен день, а не отметка времени с точностью до микросекунды.
    """
    parsed = parse_published(raw)
    if parsed is None:
        return ""
    return parsed.date().isoformat()
