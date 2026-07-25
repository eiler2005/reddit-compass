"""Тесты trend_strength: история тем, сила тренда, новизна."""

from __future__ import annotations

from pathlib import Path

from reddit_compass.trend_strength import (
    ThemeSnapshot,
    TrendInfo,
    compute_trends,
    extract_themes_from_signals,
    load_theme_history,
    save_theme_history,
)


def _signal(
    themes: list[str],
    source: str = "reddit",
    subreddit: str = "test",
    score: int = 100,
) -> dict:
    return {
        "post_id": "p1",
        "title": "Test",
        "subreddit": subreddit,
        "source": source,
        "score": score,
        "themes": themes,
        "pain_points": [],
        "book_relevance": 5,
        "business_relevance": 5,
        "summary": "",
        "model": "test",
    }


class TestExtractThemes:
    def test_basic_extraction(self):
        signals = [
            _signal(["AI safety", "sandbox escape"]),
            _signal(["AI safety"], source="hackernews", subreddit="hackernews"),
        ]
        snaps = extract_themes_from_signals(signals)
        themes = {s.theme.lower(): s for s in snaps}

        assert "ai safety" in themes
        assert themes["ai safety"].count == 2
        assert "reddit" in themes["ai safety"].sources
        assert "hackernews" in themes["ai safety"].sources

        assert "sandbox escape" in themes
        assert themes["sandbox escape"].count == 1

    def test_empty_signals(self):
        assert extract_themes_from_signals([]) == []


class TestThemeHistory:
    def test_save_and_load(self, tmp_path: Path):
        snaps = [
            ThemeSnapshot(date="2026-07-25", theme="AI safety", count=10, sources=["reddit"]),
            ThemeSnapshot(date="2026-07-25", theme="Remote work", count=5, sources=["hn"]),
        ]
        save_theme_history(tmp_path, snaps)

        loaded = load_theme_history(tmp_path)
        assert len(loaded) == 2
        assert loaded[0].theme == "AI safety"
        assert loaded[1].count == 5

    def test_deduplication(self, tmp_path: Path):
        snaps = [ThemeSnapshot(date="2026-07-25", theme="AI safety", count=10)]
        save_theme_history(tmp_path, snaps)
        save_theme_history(tmp_path, snaps)  # duplicate

        loaded = load_theme_history(tmp_path)
        assert len(loaded) == 1

    def test_append_new(self, tmp_path: Path):
        save_theme_history(
            tmp_path, [ThemeSnapshot(date="2026-07-25", theme="AI safety", count=10)]
        )
        save_theme_history(
            tmp_path, [ThemeSnapshot(date="2026-07-26", theme="AI safety", count=15)]
        )

        loaded = load_theme_history(tmp_path)
        assert len(loaded) == 2

    def test_load_missing_file(self, tmp_path: Path):
        assert load_theme_history(tmp_path) == []


class TestComputeTrends:
    def test_new_theme(self):
        current = [
            ThemeSnapshot(date="2026-07-25", theme="New Theme", count=10, sources=["reddit", "hn"])
        ]
        trends = compute_trends(current, [], "2026-07-25")

        assert len(trends) == 1
        assert trends[0].is_new is True
        assert trends[0].direction == "new"
        assert trends[0].weeks_seen == 1
        assert trends[0].strength > 0

    def test_recurring_theme(self):
        history = [
            ThemeSnapshot(date="2026-07-18", theme="Old Theme", count=8, sources=["reddit"]),
            ThemeSnapshot(date="2026-07-20", theme="Old Theme", count=12, sources=["reddit"]),
        ]
        current = [
            ThemeSnapshot(date="2026-07-25", theme="Old Theme", count=15, sources=["reddit", "hn"])
        ]
        trends = compute_trends(current, history, "2026-07-25")

        assert len(trends) == 1
        assert trends[0].is_new is False
        assert trends[0].weeks_seen >= 2
        assert trends[0].direction == "stable"  # 15/12 = 1.25 < 1.3

    def test_growing_direction(self):
        history = [ThemeSnapshot(date="2026-07-20", theme="Trend", count=5, sources=["reddit"])]
        current = [ThemeSnapshot(date="2026-07-25", theme="Trend", count=10, sources=["reddit"])]
        trends = compute_trends(current, history, "2026-07-25")

        assert trends[0].direction == "growing"  # 10/5 = 2.0 > 1.3

    def test_fading_direction(self):
        history = [ThemeSnapshot(date="2026-07-20", theme="Trend", count=20, sources=["reddit"])]
        current = [ThemeSnapshot(date="2026-07-25", theme="Trend", count=5, sources=["reddit"])]
        trends = compute_trends(current, history, "2026-07-25")

        assert trends[0].direction == "fading"  # 5/20 = 0.25 < 0.7

    def test_cross_source_increases_strength(self):
        single = [ThemeSnapshot(date="2026-07-25", theme="T", count=10, sources=["reddit"])]
        multi = [
            ThemeSnapshot(date="2026-07-25", theme="T", count=10, sources=["reddit", "hn", "rss"])
        ]

        t_single = compute_trends(single, [], "2026-07-25")
        t_multi = compute_trends(multi, [], "2026-07-25")

        assert t_multi[0].strength > t_single[0].strength

    def test_sorted_by_strength(self):
        current = [
            ThemeSnapshot(date="2026-07-25", theme="Weak", count=1, sources=["reddit"]),
            ThemeSnapshot(
                date="2026-07-25", theme="Strong", count=50, sources=["reddit", "hn", "rss"]
            ),
        ]
        trends = compute_trends(current, [], "2026-07-25")

        assert trends[0].theme == "Strong"
        assert trends[1].theme == "Weak"


class TestTrendInfoLabels:
    def test_strength_labels(self):
        t = TrendInfo(
            theme="T",
            count=1,
            sources=["r"],
            strength=35,
            is_new=True,
            weeks_seen=1,
            direction="new",
        )
        assert t.strength_label == "🔥🔥🔥"

        t.strength = 20
        assert t.strength_label == "🔥🔥"

        t.strength = 8
        assert t.strength_label == "🔥"

        t.strength = 2
        assert t.strength_label == "·"

    def test_novelty_labels(self):
        t = TrendInfo(
            theme="T",
            count=1,
            sources=["r"],
            strength=10,
            is_new=True,
            weeks_seen=1,
            direction="new",
        )
        assert t.novelty_label == "🆕"

        t.is_new = False
        t.weeks_seen = 3
        assert t.novelty_label == "🔄 3 нед"
