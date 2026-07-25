"""Тесты Trend Radar: парсинг signals-report, рендер radar-страницы, trend_radar с Ladder/PH."""

from __future__ import annotations

import json
from pathlib import Path

from reddit_compass.api.dashboard import (
    _parse_signals_report_sections,
    render_radar_page,
)
from reddit_compass.signals import render_trend_radar

SAMPLE_REPORT = """\
## 🤖 LLM-анализ сигналов (2026-07-25)

Проанализировано: 100 постов

### Топ-темы
1. **AI-агенты выходят из-под контроля**
   Продвинутые модели самостоятельно находят уязвимости. Критично для тома «Общество».
2. **Физическое сопротивление ИИ-дата-центрам**
   Активисты мобилизуют общественность против экологического ущерба.

### Идеи для колонок
- «ИИ-колониализм» и бунт на местах
- Конец эпохи AI-washing

### Сдвиги нарратива
- От «AI заменит всех» к «AI нужен начальник»

### Топ-10 по релевантности для книги

| # | Subreddit | Title | Book | Biz | Themes |
|---|---|---|---|---|---|
| 1 | r/artificial | AI agents escape | 9 | 8 | AI safety |

### Pain points (все)
- AI safety failures
- Sandbox escape
- AI feature bloat
"""


def _make_post(
    post_id: str = "p1",
    title: str = "Test Post",
    subreddit: str = "test",
    score: int = 100,
    source: str = "reddit",
) -> dict:
    return {
        "post_id": post_id,
        "title": title,
        "subreddit": subreddit,
        "score": score,
        "author": "tester",
        "created_utc": "2026-07-25T12:00:00Z",
        "upvote_ratio": 0.9,
        "num_comments": 5,
        "url": f"https://example.com/{post_id}",
        "selftext": "",
        "link_flair_text": None,
        "is_self": True,
        "permalink": f"/r/{subreddit}/comments/{post_id}",
        "monitoring_type": "hot",
        "snapshot_date": "2026-07-25",
        "source": source,
    }


def _make_signal(
    post_id: str = "p1",
    title: str = "Test Post",
    themes: list[str] | None = None,
    pain_points: list[str] | None = None,
    book_relevance: int = 7,
) -> dict:
    return {
        "post_id": post_id,
        "title": title,
        "subreddit": "test",
        "score": 100,
        "pain_points": pain_points or ["sandbox escape"],
        "buying_intent": False,
        "business_relevance": 6,
        "book_relevance": book_relevance,
        "themes": themes or ["AI safety"],
        "summary": "Test summary",
        "model": "qwen3.7-plus",
    }


class TestParseSignalsReport:
    def test_parses_top_themes(self):
        sections = _parse_signals_report_sections(SAMPLE_REPORT)
        assert len(sections["top_themes"]) == 2
        assert sections["top_themes"][0]["theme"] == "AI-агенты выходят из-под контроля"
        assert "уязвимости" in sections["top_themes"][0]["explanation"]
        assert sections["top_themes"][1]["theme"] == "Физическое сопротивление ИИ-дата-центрам"

    def test_parses_column_ideas(self):
        sections = _parse_signals_report_sections(SAMPLE_REPORT)
        assert len(sections["column_ideas"]) == 2
        assert "ИИ-колониализм" in sections["column_ideas"][0]

    def test_parses_narrative_shifts(self):
        sections = _parse_signals_report_sections(SAMPLE_REPORT)
        assert len(sections["narrative_shifts"]) == 1
        assert "AI заменит всех" in sections["narrative_shifts"][0]

    def test_parses_pain_points(self):
        sections = _parse_signals_report_sections(SAMPLE_REPORT)
        assert len(sections["pain_points"]) == 3
        assert "AI safety failures" in sections["pain_points"]

    def test_empty_report(self):
        sections = _parse_signals_report_sections("")
        assert sections["top_themes"] == []
        assert sections["column_ideas"] == []


class TestRenderRadarPage:
    def test_radar_with_signals(self, tmp_path: Path):
        snap = tmp_path / "2026-07-25"
        snap.mkdir()

        # posts.jsonl
        posts = [_make_post("p1", "AI Post", "artificial", 500)]
        (snap / "posts.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in posts), encoding="utf-8"
        )

        # signals.jsonl
        signals = [_make_signal("p1", "AI Post", themes=["AI safety"], book_relevance=9)]
        (snap / "signals.jsonl").write_text(
            "\n".join(json.dumps(s, ensure_ascii=False) for s in signals), encoding="utf-8"
        )

        # signals-report.md
        (snap / "signals-report.md").write_text(SAMPLE_REPORT, encoding="utf-8")

        html = render_radar_page(snap, "2026-07-25")

        assert "Trend Radar" in html
        assert "AI-агенты выходят из-под контроля" in html
        assert "ИИ-колониализм" in html
        assert "sandbox escape" in html  # pain point из signals.jsonl
        assert "LLM-анализ" in html
        assert "AI Post" in html

    def test_radar_without_signals(self, tmp_path: Path):
        snap = tmp_path / "2026-07-25"
        snap.mkdir()

        posts = [_make_post("p1", "No Signals Post")]
        (snap / "posts.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in posts), encoding="utf-8"
        )

        html = render_radar_page(snap, "2026-07-25")

        assert "Trend Radar" in html
        assert "LLM-анализ не выполнен" in html
        assert "No Signals Post" in html

    def test_radar_empty_snapshot(self, tmp_path: Path):
        snap = tmp_path / "2026-07-25"
        snap.mkdir()

        html = render_radar_page(snap, "2026-07-25")

        assert "Trend Radar" in html
        assert "LLM-анализ не выполнен" in html


class TestRenderTrendRadar:
    def test_includes_ladder_and_ph(self, tmp_path: Path):
        snap = tmp_path / "2026-07-25"
        snap.mkdir()

        # ladder.jsonl
        ladder = [_make_post("l1", "NYT AI Article", "nytimes", 0, source="ladder")]
        (snap / "ladder.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in ladder), encoding="utf-8"
        )

        # producthunt.jsonl
        ph = [_make_post("ph1", "Cool AI Tool", "producthunt", 50, source="producthunt")]
        (snap / "producthunt.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in ph), encoding="utf-8"
        )

        radar = render_trend_radar(snap, "2026-07-25")

        assert "Ladder: 1" in radar
        assert "PH: 1" in radar
        assert "NYT AI Article" in radar
        assert "Cool AI Tool" in radar
        assert "ProductHunt" in radar

    def test_mega_trends_include_all_sources(self, tmp_path: Path):
        snap = tmp_path / "2026-07-25"
        snap.mkdir()

        reddit = [_make_post("r1", "Reddit Post", "artificial", 1000)]
        hn = [_make_post("h1", "HN Post", "hackernews", 500, source="hackernews")]
        ladder = [_make_post("l1", "Ladder Post", "nytimes", 0, source="ladder")]

        (snap / "posts.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in reddit), encoding="utf-8"
        )
        (snap / "hackernews.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in hn), encoding="utf-8"
        )
        (snap / "ladder.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in ladder), encoding="utf-8"
        )

        radar = render_trend_radar(snap, "2026-07-25")

        assert "Reddit Post" in radar
        assert "HN Post" in radar
        assert "Ladder Post" in radar
        assert "3 единиц" in radar
