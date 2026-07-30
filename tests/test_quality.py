"""Tests for the quality-gate module (Шаг 4): floors + regressions + compute_quality."""

from __future__ import annotations

import json

from reddit_compass.intelligence.engine import engine_db
from reddit_compass.intelligence.quality import (
    compute_quality,
    evaluate_floors,
    evaluate_regressions,
    is_bad_trend_name,
)


def test_is_bad_trend_name() -> None:
    assert is_bad_trend_name("Паттерн: rise") is True
    assert is_bad_trend_name("Паттерн: layoff") is True
    assert is_bad_trend_name("fall") is True
    assert is_bad_trend_name("ai agent") is True
    assert is_bad_trend_name("") is True
    # валидные многословные имена (v2 c-TF-IDF стиль)
    assert is_bad_trend_name("OpenAI quantum agent platform") is False
    assert is_bad_trend_name("climate policy shift") is False


def test_evaluate_floors_pass_and_fail() -> None:
    clean = {"stories_overmerge_ge5": 0, "taxonomy_ai_tech_share": 12.0, "trends_bad_name_count": 0}
    assert all(r.passed for r in evaluate_floors(clean))
    bad = {"stories_overmerge_ge5": 3, "taxonomy_ai_tech_share": 75.0, "trends_bad_name_count": 2}
    by_metric = {r.metric: r for r in evaluate_floors(bad)}
    assert by_metric["stories_overmerge_ge5"].passed is False
    assert by_metric["taxonomy_ai_tech_share"].passed is False
    assert by_metric["trends_bad_name_count"].passed is False


def test_evaluate_regressions_detects_worsening() -> None:
    baseline = {
        "stories_overmerge_ge5": 0,
        "stories_cross_source": 60,
        "taxonomy_ai_tech_share": 20,
    }
    worse = {"stories_overmerge_ge5": 5, "stories_cross_source": 30, "taxonomy_ai_tech_share": 22}
    reg = {r["metric"]: r for r in evaluate_regressions(worse, baseline)}
    assert reg["stories_overmerge_ge5"]["regressed"] is True  # 0 -> 5, tol 0
    assert reg["stories_cross_source"]["regressed"] is True  # 60 -> 30, tol 10
    assert reg["taxonomy_ai_tech_share"]["regressed"] is False  # +2 within tol 5
    # без изменений регрессий нет
    assert all(not r["regressed"] for r in evaluate_regressions(baseline, baseline))


def _insert_item(conn, release_id, item_id, title, provider="reuters", section="technology"):
    conn.execute(
        """INSERT INTO release_items
           (release_id, item_id, provider, source_cluster, external_id, canonical_url,
            title, excerpt, source_section, row_checksum)
           VALUES (?,?,?,?,?,?,?,?,?, 'x')""",
        (
            release_id,
            item_id,
            provider,
            "mainstream",
            item_id,
            f"https://x/{item_id}",
            title,
            "",
            section,
        ),
    )


def _insert_story(conn, sr, sid, source_count, item_count):
    conn.execute(
        """INSERT INTO engine_stories
           (story_release_id, story_id, canonical_key, title, source_count, item_count)
           VALUES (?,?,?,?,?,?)""",
        (sr, sid, sid, "t", source_count, item_count),
    )


def _insert_trend(conn, tr, tid, name):
    conn.execute(
        "INSERT INTO engine_trends (trend_release_id, trend_id, name_ru, pattern) VALUES (?,?,?,?)",
        (tr, tid, name, name),
    )


def _insert_signal(conn, sid, signal_id, signal_type, gap):
    conn.execute(
        """INSERT INTO community_signals
           (signal_release_id, signal_id, item_id, subreddit, signal_type, title, perspective_gap)
           VALUES (?,?,?,?,?,?,?)""",
        (sid, signal_id, signal_id, "technology", signal_type, "t", gap),
    )


def _build(conn, dr, sr, tr, sig, items, stories, trends, signals, gap_available=True):
    for it in items:
        _insert_item(conn, dr, *it)
    for st in stories:
        _insert_story(conn, sr, *st)
    for td in trends:
        _insert_trend(conn, tr, *td)
    conn.execute(
        """INSERT INTO signal_releases
           (signal_release_id, data_release_id, facet_release_id, date, status,
            created_at, metrics_json)
           VALUES (?,?,?,?, 'finalized', '2026-07-30T00:00:00Z', ?)""",
        (sig, dr, "fac", "2026-07-30", json.dumps({"perspective_gap_available": gap_available})),
    )
    for sg in signals:
        _insert_signal(conn, sig, *sg)
    conn.commit()


def test_compute_quality_dirty_release_fails_floors(tmp_path) -> None:
    conn = engine_db(tmp_path / "trend_engine.db")
    _build(
        conn,
        "DR",
        "SR",
        "TR",
        "SIG",
        items=[
            ("i1", "OpenAI GPT LLM release"),
            ("i2", "Claude LLM agent update"),
            ("i3", "Anthropic GPT model news"),
            ("i4", "a quiet local note xyz"),
        ],
        stories=[("s1", 1, 6), ("s2", 2, 2), ("s3", 1, 1)],
        trends=[
            ("t1", "Паттерн: rise"),
            ("t2", "Паттерн: rise"),
            ("t3", "OpenAI quantum agent platform"),
        ],
        signals=[("g1", "other", 0.5), ("g2", "question", 0.4)],
    )
    m = compute_quality(
        conn,
        data_release_id="DR",
        story_release_id="SR",
        trend_release_id="TR",
        signal_release_id="SIG",
    )
    assert m["stories_overmerge_ge5"] == 1
    assert m["taxonomy_ai_tech_share"] > 50
    assert m["taxonomy_empty_rubrics"] > 0
    assert m["trends_bad_name_count"] == 2
    assert m["trends_duplicate_name_count"] == 1
    assert m["pulse_other_share"] == 50.0
    failed = [r.metric for r in evaluate_floors(m) if not r.passed]
    assert "stories_overmerge_ge5" in failed
    assert "trends_bad_name_count" in failed
    assert "trends_duplicate_name_count" in failed
    conn.close()


def test_compute_quality_clean_release_passes_floors(tmp_path) -> None:
    conn = engine_db(tmp_path / "trend_engine.db")
    _build(
        conn,
        "DR",
        "SR",
        "TR",
        "SIG",
        items=[
            ("i1", "OpenAI GPT LLM release"),
            ("i2", "surveillance camera facial recognition tracking privacy"),
            ("i3", "layoff hiring salary career jobs"),
            ("i4", "company earnings revenue merger acquisition"),
            ("i5", "election senate congress government policy"),
            ("i6", "war ukraine nato sanctions geopolitics"),
            ("i7", "hollywood film music netflix celebrity"),
            ("i8", "climate energy solar nuclear science research"),
        ],
        stories=[("s1", 2, 2), ("s2", 1, 1), ("s3", 2, 3)],
        trends=[
            ("t1", "OpenAI quantum agent platform"),
            ("t2", "climate policy shift"),
            ("t3", "labor market cooling"),
        ],
        signals=[("g1", "question", 0.6), ("g2", "pain_point", 0.5)],
    )
    m = compute_quality(
        conn,
        data_release_id="DR",
        story_release_id="SR",
        trend_release_id="TR",
        signal_release_id="SIG",
    )
    assert m["stories_overmerge_ge5"] == 0
    assert m["taxonomy_empty_rubrics"] == 0
    assert m["taxonomy_max_rubric_share"] <= 50
    assert m["trends_bad_name_count"] == 0
    assert m["trends_duplicate_name_count"] == 0
    assert m["pulse_other_share"] == 0.0
    assert all(r.passed for r in evaluate_floors(m))
    conn.close()
