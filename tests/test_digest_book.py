"""ЭКСПЕРИМЕНТ: разбор по оптике книги — второй дайджест рядом с основным.

Оптика взята из утверждённого контракта дайджеста в
``AiNativeBook_Draft_26/services/digest-service``: каждая тема проходит через
«что изменилось», «что подешевело / стало ценнее», «что это меняет для человека
и работы» и «что это меняет для команды, бизнеса и доверия», а выпуск
заканчивается одним следующим разумным ходом.

Прежняя версия строила дайджест из ``PostCard`` — «73 поста, топ-5 по score».
Это лента популярного, а не разбор.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from reddit_compass.api.app import create_app
from reddit_compass.intelligence.digest_book import MAX_PER_SECTION, build_book_digest


def _dashboard(**over: int) -> dict[str, int]:
    base = {
        "item_count": 8385,
        "source_count": 21,
        "source_cluster_count": 7,
        "trend_count": 42,
        "cross_source_trend_count": 17,
    }
    base.update(over)
    return base


def _signal(signal_type: str, index: int = 0) -> dict[str, object]:
    return {
        "title": f"сигнал {signal_type} {index}",
        "signal_type": signal_type,
        "signal_type_label": signal_type,
        "subreddit": "test",
        "discussion_url": f"https://example.invalid/{signal_type}/{index}",
    }


def _digest(**over: object):
    kwargs: dict[str, object] = {
        "date": "2026-08-01",
        "dashboard": _dashboard(),
        "trends": [],
        "reading": [],
        "reddit_new": [],
    }
    kwargs.update(over)
    return build_book_digest(**kwargs)  # type: ignore[arg-type]


def _section(digest, key: str):
    return next(section for section in digest.sections if section.key == key)


def test_sections_follow_the_four_questions_of_the_book() -> None:
    keys = [section.key for section in _digest().sections]

    assert keys == ["changed", "price", "human", "business", "trust", "evidence"]


def test_signals_land_in_the_question_they_answer() -> None:
    """Работа — в «человек», рынки — в «бизнес», риски — в «доверие»."""
    digest = _digest(
        reddit_new=[
            _signal("career_labor"),
            _signal("market_investing"),
            _signal("ai_risk"),
            _signal("meme_culture"),
        ]
    )

    assert "career_labor" in _section(digest, "human").items[0].title
    assert "market_investing" in _section(digest, "business").items[0].title
    assert "ai_risk" in _section(digest, "trust").items[0].title
    # Мемы не отвечают ни на один вопрос книги и никуда не попадают.
    assert all(
        "meme_culture" not in item.title for section in digest.sections for item in section.items
    )


def test_empty_section_explains_what_is_missing() -> None:
    """Пустая рубрика без объяснения читается как поломка страницы.

    «Что стало дешевле» радар не измеряет вовсе — он видит охват и тон,
    но не цену. Об этом надо сказать, а не молчать.
    """
    price = _section(_digest(), "price")

    assert price.is_empty
    assert "не измеряет" in price.gap_note


def test_next_move_is_left_to_the_author() -> None:
    """Обещание выпуска — один следующий разумный ход, и это суждение."""
    assert _digest().next_move == ""


def test_headline_names_the_shift_not_the_corpus() -> None:
    with_gaps = _digest(gap_signals=[_signal("ai_risk")])
    quiet = _digest(dashboard=_dashboard(cross_source_trend_count=0))

    assert "подтверждено разными источниками" in with_gaps.headline
    assert "без участия медиа" in with_gaps.headline
    assert "не набралось" in quiet.headline


def test_sections_are_capped_so_the_draft_stays_shorter_than_the_issue() -> None:
    digest = _digest(reddit_new=[_signal("career_labor", i) for i in range(50)])

    assert len(_section(digest, "human").items) == MAX_PER_SECTION


def test_long_titles_are_clipped() -> None:
    digest = _digest(reading=[{"title": "я" * 400, "primary_url": "https://example.invalid/1"}])

    item = _section(digest, "evidence").items[0]
    assert len(item.title) <= 120
    assert item.title.endswith("…")


def test_internal_links_become_absolute_for_delivery() -> None:
    """В письме относительный путь мёртв, на странице — нормален."""
    trend = {"title": "T", "url": "/trends/t1?channel=broad", "pattern": "p"}

    on_site = _digest(trends=[trend])
    for_email = _digest(trends=[trend], base_url="https://example.invalid")

    assert _section(on_site, "changed").items[0].url.startswith("/trends/")
    assert (
        _section(for_email, "changed").items[0].url
        == "https://example.invalid/trends/t1?channel=broad"
    )


def test_book_digest_lives_beside_the_main_one() -> None:
    client = TestClient(create_app())

    page = client.get("/digest/book")

    assert page.status_code == 200
    assert client.get("/digest").status_code == 200
    assert "Что реально изменилось" in page.text
    assert "Следующий разумный ход" in page.text
    # Эксперимент помечен экспериментом и уводит обратно на основной дайджест.
    assert "digest-experiment" in page.text
    assert 'href="/digest"' in page.text
    # Своего пункта в главном меню у него нет: он живёт в разделе «Дайджест».
    assert '<a href="/digest/book" class="nav-link' not in page.text


def test_digest_and_today_come_from_the_same_release() -> None:
    """Страница и будущая рассылка не должны расходиться с /today."""
    import re

    client = TestClient(create_app())

    def date_of(html: str) -> str:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", html)
        return match.group(1) if match else ""

    assert date_of(client.get("/digest/book").text) == date_of(client.get("/today").text)
