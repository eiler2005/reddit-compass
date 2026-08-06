"""Транспортный отказ источника как факт, а не как пустой список.

Адаптеры ловят HTTP- и сетевые ошибки внутри себя и возвращали ``[]``. Дальше по цепочке
``status="ok" if cards else "empty"`` превращало отказ в «пустой день», набор статусов
``{"ok", "empty"}`` давал run ``complete``, а ``collection_coverage`` считала такой день
собранным и не показывала gap. Ночь, где Algolia отдаёт 429, а фиды 503, записывалась как
полный день с одним Reddit и навсегда исчезала из поля зрения оператора.

Пустой день — нормальное явление (у Product Hunt бывают тихие сутки), поэтому отличать
надо не «нет карточек», а «ни один запрос не удался». Отсюда контракт ниже: адаптер
считает попытки и отказы и поднимает `SourceTransportError`, только если не удалось
**ничего**. Частичный отказ остаётся ``ok`` — данные за день есть, пусть и неполные, —
но его причина попадает в сообщение source health.
"""

from __future__ import annotations


class SourceTransportError(RuntimeError):
    """Ни один запрос адаптера не удался — за день нет данных и нет доказательства пустоты."""

    def __init__(self, source_id: str, attempted: int, failures: list[str]) -> None:
        self.source_id = source_id
        self.attempted = attempted
        self.failures = failures
        detail = "; ".join(failures[:3])
        super().__init__(f"{source_id}: all {attempted} request(s) failed: {detail}")


class RequestTally:
    """Счётчик попыток и отказов одного адаптера за один прогон."""

    def __init__(self, source_id: str) -> None:
        self._source_id = source_id
        self._attempted = 0
        self._failures: list[str] = []

    def attempt(self) -> None:
        self._attempted += 1

    def failed(self, reason: str) -> None:
        self._failures.append(reason)

    @property
    def failures(self) -> list[str]:
        return list(self._failures)

    def summary(self) -> str:
        """Сообщение для source health, когда часть запросов не удалась."""
        if not self._failures:
            return ""
        return f"{len(self._failures)}/{self._attempted} requests failed: " + "; ".join(
            self._failures[:3]
        )

    def raise_if_total_failure(self) -> None:
        """Поднять отказ, если провалились все попытки. Ноль попыток — не отказ."""
        if self._attempted and len(self._failures) >= self._attempted:
            raise SourceTransportError(self._source_id, self._attempted, self._failures)
