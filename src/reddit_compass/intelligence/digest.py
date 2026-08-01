"""Заготовка недельного разбора: материал, разложенный по оптике книги.

Дайджест как продукт живёт не здесь, а в ``AiNativeBook_Draft_26/services/
digest-service`` — там утверждённый редакционный контракт: бренд, порядок
блоков выпуска и обещание «один проверяемый сдвиг в неделю». Этот модуль
не публикует выпуск и не подменяет его: он готовит **материал** в той форме,
в которой его удобно превратить в выпуск.

Оптика взята оттуда же — четыре вопроса, через которые проходит каждая тема:

1. Что реально изменилось? Факт и подтверждённые данные, а не анонс.
2. Что стало дешевле, а что — ценнее?
3. Что это меняет для человека и его работы?
4. Что это меняет для команды, бизнеса и доверия?

Раскладка по этим вопросам делается из того, что радар действительно знает:
охват события по кластерам источников, разрыв перспективы между сообществом
и медиа, тип сигнала. Вопросы, на которые данных нет, честно остаются пустыми
с пояснением, чего не хватает, — пустая рубрика полезнее выдуманной.

Прежняя версия строила дайджест из ``PostCard``: «73 поста, топ-5 по score».
Это лента популярного, а не разбор.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Выжимка, требующая прокрутки, перестаёт быть выжимкой. Обещание выпуска —
# 5–6 минут чтения, и заготовка не должна быть больше самого разбора.
MAX_PER_SECTION = 5

# Какие типы сигналов отвечают на какой вопрос. Сопоставление грубое и
# намеренно узкое: лучше показать три верных примера, чем двадцать «около».
HUMAN_SIGNALS = ("career_labor", "pain_point", "complaint", "question")
BUSINESS_SIGNALS = ("market_investing", "ai_tools", "product_request")
TRUST_SIGNALS = ("ai_risk", "policy_politics")


@dataclass(frozen=True)
class DigestItem:
    """Одна строка материала: заголовок, ссылка и одна поясняющая фраза."""

    title: str
    url: str = ""
    note: str = ""
    meta: str = ""


@dataclass(frozen=True)
class DigestSection:
    """Ответ на один вопрос книги — или честное «данных нет»."""

    key: str
    title: str
    question: str
    items: list[DigestItem] = field(default_factory=list)
    # Чего не хватает, если раздел пуст. Пустая рубрика без объяснения читается
    # как поломка; с объяснением — как знание о границах метода.
    gap_note: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.items


@dataclass
class Digest:
    """Заготовка выпуска за период."""

    date: str
    headline: str
    lede: str = ""
    preview: bool = False
    publication_id: str = ""
    item_count: int = 0
    source_count: int = 0
    considered_count: int = 0
    sections: list[DigestSection] = field(default_factory=list)
    next_move: str = ""

    @property
    def is_empty(self) -> bool:
        return all(section.is_empty for section in self.sections)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clip(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _signal_item(signal: dict[str, Any]) -> DigestItem:
    return DigestItem(
        title=_clip(signal.get("title"), 120),
        url=str(signal.get("discussion_url") or ""),
        meta=" · ".join(
            part
            for part in (
                f"r/{signal.get('subreddit')}" if signal.get("subreddit") else "",
                str(signal.get("signal_type_label") or ""),
            )
            if part
        ),
    )


def _by_types(
    signals: list[dict[str, Any]], types: tuple[str, ...], limit: int = MAX_PER_SECTION
) -> list[DigestItem]:
    return [
        _signal_item(signal) for signal in signals if str(signal.get("signal_type") or "") in types
    ][:limit]


def _headline(cross_source: int, gaps: int) -> str:
    """Заголовок называет сдвиг, а не пересчитывает корпус.

    «8 385 материалов» ничего не сообщает: обещание выпуска — один проверяемый
    сдвиг, поэтому и заготовка должна отвечать, есть ли он.
    """
    if cross_source and gaps:
        return (
            f"{cross_source} подтверждено разными источниками, {gaps} обсуждают без участия медиа"
        )
    if cross_source:
        return f"{cross_source} сдвигов подтверждены разными источниками"
    if gaps:
        return f"{gaps} тем живут только в сообществе"
    return "Проверяемых сдвигов не набралось"


def build_digest(
    *,
    date: str,
    dashboard: dict[str, Any],
    trends: list[dict[str, Any]],
    reading: list[dict[str, Any]],
    reddit_new: list[dict[str, Any]],
    gap_signals: list[dict[str, Any]] | None = None,
    preview: bool = False,
    publication_id: str = "",
    base_url: str = "",
) -> Digest:
    """Разложить опубликованный выпуск по четырём вопросам книги.

    Функция намеренно ничего не читает сама: куски передаёт вызывающий код,
    поэтому заготовка проверяется без базы, а страница и будущая рассылка
    не могут разойтись — источник у них один.
    """
    gap_signals = gap_signals or []

    def absolute(url: str) -> str:
        """В письме относительный путь мёртв, на странице — нормален."""
        if not url or not base_url:
            return url
        return f"{base_url.rstrip('/')}{url}" if url.startswith("/") else url

    changed = [
        DigestItem(
            title=_clip(trend.get("title"), 120),
            url=absolute(str(trend.get("url") or "")),
            note=_clip(trend.get("pattern"), 180),
            meta=" · ".join(
                part
                for part in (
                    str(trend.get("lifecycle_label") or ""),
                    str(trend.get("source_scope_label") or ""),
                )
                if part
            ),
        )
        for trend in trends[:MAX_PER_SECTION]
    ]

    evidence = [
        DigestItem(
            title=_clip(item.get("title"), 120),
            url=str(item.get("primary_url") or ""),
            note=_clip(item.get("summary_ru") or item.get("excerpt"), 180),
            meta=str(item.get("provider") or ""),
        )
        for item in reading[:MAX_PER_SECTION]
    ]

    trust_items = [_signal_item(signal) for signal in gap_signals[:MAX_PER_SECTION]]
    trust_items += _by_types(
        reddit_new, TRUST_SIGNALS, limit=max(0, MAX_PER_SECTION - len(trust_items))
    )

    cross_source = int(dashboard.get("cross_source_trend_count") or 0)

    sections = [
        DigestSection(
            key="changed",
            title="Что реально изменилось",
            question="Отделить событие и подтверждённые данные от анонса и шума.",
            items=changed,
            gap_note=(
                "Ни один паттерн ещё не прошёл проверку: кандидаты остаются в Трендах "
                "до review. Сдвиг без подтверждения — это анонс, и в выпуск он не идёт."
            ),
        ),
        DigestSection(
            key="price",
            title="Что стало дешевле, а что ценнее",
            question="Показать сдвиг цены, дефицита или полезности.",
            items=[],
            gap_note=(
                "Радар этого не измеряет: он видит охват и тон обсуждения, но не цену "
                "и не дефицит. Раздел заполняется вручную из первоисточников выпуска."
            ),
        ),
        DigestSection(
            key="human",
            title="Что это меняет для человека и работы",
            question="Роль, навык, решение, ответственность.",
            items=_by_types(reddit_new, HUMAN_SIGNALS),
            gap_note="Сигналов про работу, боли и вопросы в этом выпуске не нашлось.",
        ),
        DigestSection(
            key="business",
            title="Что это меняет для команды и бизнеса",
            question="Процесс, outcome, риск, экономика.",
            items=_by_types(reddit_new, BUSINESS_SIGNALS),
            gap_note="Сигналов про рынки, инструменты и запросы к продукту не нашлось.",
        ),
        DigestSection(
            key="trust",
            title="Доверие: где сообщество расходится с медиа",
            question="Кому верят, на каком основании и где освещение одностороннее.",
            items=trust_items,
            gap_note=(
                "Односторонних тем не набралось: всё заметное освещено и сообществом, "
                "и медиа. Для книги это тоже наблюдение."
            ),
        ),
        DigestSection(
            key="evidence",
            title="Первоисточники",
            question="Что прочитать целиком, прежде чем писать.",
            items=evidence,
            gap_note="Лента чтения за эту дату пуста.",
        ),
    ]

    return Digest(
        date=date,
        headline=_headline(cross_source, len(gap_signals)),
        lede=(
            "Заготовка недельного разбора: материал разложен по четырём вопросам книги. "
            "Выпуск из него собирается вручную — здесь нет ни интерпретации, ни вывода."
        ),
        preview=preview,
        publication_id=publication_id,
        item_count=int(dashboard.get("item_count") or 0),
        source_count=int(dashboard.get("source_count") or 0),
        considered_count=int(dashboard.get("trend_count") or 0),
        sections=sections,
        # Обещание выпуска — «один следующий разумный ход». Его формулирует
        # автор: радар может показать материал, но не может решить за него.
        next_move="",
    )
