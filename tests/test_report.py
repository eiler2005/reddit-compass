"""Тесты рендера Markdown-отчёта."""

from __future__ import annotations

from collections.abc import Callable

from reddit_compass.export import render_trends_report
from reddit_compass.models import PostCard


def test_report_empty_inputs() -> None:
    md = render_trends_report([], [], [], "2026-07-22", {})
    assert "# Reddit Trends Report — 2026-07-22" in md
    assert "reddit-compass" in md  # футер обновлён (не старое имя)


def test_report_has_sections(make_card: Callable[..., PostCard]) -> None:
    cards = [
        make_card(
            post_id="a",
            title="A very long headline for testing the trends report layout",
            score=2000,
            num_comments=300,
            monitoring_type="hot",
        )
    ]
    md = render_trends_report(cards, [], [], "2026-07-22", {"ai": ["artificial"]})
    assert "Hot & Rising" in md
    assert "Trend Summary" in md
    assert "r/artificial" in md
    assert "2.0k" in md  # _format_score для 2000


def test_report_search_and_summary(make_card: Callable[..., PostCard]) -> None:
    cards = [
        make_card(post_id="s1", monitoring_type="search", keyword="vibe coding", score=50),
    ]
    md = render_trends_report(cards, [], [], "2026-07-22", {})
    assert "Keyword Search" in md
    assert "vibe coding" in md
