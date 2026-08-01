"""Дайджест: выжимка за день из опубликованного выпуска.

Заготовки в ``notify.py`` строили дайджест из ``PostCard`` — старой модели,
где есть только Reddit: «73 поста, топ-5 по score». Это лента популярного,
а не компас. Здесь проверяется, что дайджест говорит о том, ради чего продукт
существует: где сообщество расходится с медиа.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from reddit_compass.api.app import create_app
from reddit_compass.intelligence.digest import MAX_GAPS, build_digest


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


def test_headline_names_the_gap_not_the_counters() -> None:
    """«73 поста» ничего не сообщает; расхождение с медиа — сообщает."""
    digest = build_digest(
        date="2026-08-01",
        dashboard=_dashboard(),
        trends=[],
        reading=[],
        reddit_new=[],
        gap_signals=[{"title": "A", "subreddit": "x", "discussion_url": "https://e/1"}],
    )

    assert "подтверждено разными источниками" in digest.headline
    assert "только на Reddit" in digest.headline


def test_quiet_day_says_so_instead_of_faking_urgency() -> None:
    digest = build_digest(
        date="2026-08-01",
        dashboard=_dashboard(cross_source_trend_count=0),
        trends=[],
        reading=[],
        reddit_new=[],
        gap_signals=[],
    )

    assert "спокойный день" in digest.headline
    assert digest.is_empty


def test_sections_are_capped_so_the_digest_stays_readable() -> None:
    """Выжимка не должна требовать прокрутки — иначе это не выжимка."""
    digest = build_digest(
        date="2026-08-01",
        dashboard=_dashboard(),
        trends=[],
        reading=[],
        reddit_new=[],
        gap_signals=[
            {"title": f"сигнал {i}", "subreddit": "x", "discussion_url": f"https://e/{i}"}
            for i in range(50)
        ],
    )

    assert len(digest.gaps) == MAX_GAPS


def test_long_titles_are_clipped_not_wrapped() -> None:
    digest = build_digest(
        date="2026-08-01",
        dashboard=_dashboard(),
        trends=[],
        reading=[{"title": "я" * 400, "primary_url": "https://e/1"}],
        reddit_new=[],
    )

    assert len(digest.reading[0].title) <= 120
    assert digest.reading[0].title.endswith("…")


def test_internal_links_become_absolute_for_delivery() -> None:
    """В письме относительный путь мёртв, а на странице — нормален."""
    trend = {"title": "T", "url": "/trends/t1?channel=broad", "pattern": "p"}

    on_site = build_digest(
        date="2026-08-01", dashboard=_dashboard(), trends=[trend], reading=[], reddit_new=[]
    )
    for_email = build_digest(
        date="2026-08-01",
        dashboard=_dashboard(),
        trends=[trend],
        reading=[],
        reddit_new=[],
        base_url="https://example.invalid",
    )

    assert on_site.trends[0].url.startswith("/trends/")
    assert for_email.trends[0].url == "https://example.invalid/trends/t1?channel=broad"


def test_digest_page_renders_and_is_reachable_from_the_nav() -> None:
    client = TestClient(create_app())

    page = client.get("/digest")
    dated = client.get("/digest/2026-07-27")

    assert page.status_code == 200
    assert dated.status_code == 200
    assert "digest-headline" in page.text
    # Раздел помечен активным на сервере, как и остальные.
    assert '<a href="/digest" class="nav-link active"' in page.text


def test_digest_and_today_come_from_the_same_release() -> None:
    """Страница и будущая рассылка не должны расходиться с /today."""
    client = TestClient(create_app())

    digest = client.get("/digest").text
    today = client.get("/today").text

    import re

    def date_of(html: str) -> str:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", html)
        return match.group(1) if match else ""

    assert date_of(digest) == date_of(today)
