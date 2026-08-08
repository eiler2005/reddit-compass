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


def test_collapsed_trend_layer_fails_a_floor() -> None:
    """Релиз без трендов обязан упираться в пол.

    Пока отказы трендового ревью терялись, пустой слой был недостижим и пола не
    существовало: пятнадцать полов пропускали релиз с нулём трендов. Теперь отказ
    выбрасывает тренд, поэтому обвал слоя стал возможным исходом сбоя промпта.
    """
    by_metric = {r.metric: r for r in evaluate_floors({"trends_count": 0})}
    assert by_metric["trends_count"].passed is False
    # Порог не подогнан под боевые 98–104: законно тихое окно проходит.
    assert all(r.passed for r in evaluate_floors({"trends_count": 3}))
    assert all(r.passed for r in evaluate_floors({"trends_count": 98}))


def test_partial_trend_collapse_is_caught_by_regression_not_by_the_floor() -> None:
    """Пол ловит только ноль; половину слоя обязана поймать регрессия к baseline.

    Абсолютным порогом это не чинится: значение под боевые 87–104 блокировало бы законно
    тихое окно. Сравнение с эталонным релизом от подгонки свободно.
    """
    baseline = {"trends_count": 93}
    normal = {r["metric"]: r for r in evaluate_regressions({"trends_count": 87}, baseline)}
    collapsed = {r["metric"]: r for r in evaluate_regressions({"trends_count": 5}, baseline)}

    assert normal["trends_count"]["regressed"] is False
    assert collapsed["trends_count"]["regressed"] is True
    # Рост слоя регрессией не считается — его ограничивают полы на качество имён.
    grown = {r["metric"]: r for r in evaluate_regressions({"trends_count": 140}, baseline)}
    assert grown["trends_count"]["regressed"] is False


def test_completeness_floors_separate_collapsed_from_working_releases() -> None:
    """Полы полноты обязаны лежать в зазоре между схлопыванием и рабочей полосой.

    Числа — фактические замеры на 2026-07-26_2026-08-01-broad-r2 и на замороженном
    2026-07-23_2026-07-29-broad-r1. Прежние значения (90 / 35 / 0.85) стояли на потолке
    достижимого: их брала ровно одна точка порога CrossEncoder, и +0.03 к нему давало
    провал. Тест фиксирует, что полы снова не съедут на край фронтира.
    """
    stable = {
        "stories_overmerge_ge5": 0,
        "stories_overmerge_ge8": 0,
        "trends_bad_name_count": 0,
        "trends_duplicate_name_count": 0,
    }

    def failed(multi: float, cross: float, compression: float) -> set[str]:
        metrics = {
            **stable,
            "stories_multi_per_1k": multi,
            "stories_cross_source_per_1k": cross,
            "stories_compression": compression,
        }
        return {r.metric for r in evaluate_floors(metrics) if not r.passed}

    completeness = {
        "stories_multi_per_1k",
        "stories_cross_source_per_1k",
        "stories_compression",
    }
    # Схлопнутый слой Stories — то, ради чего полы и вводились.
    assert failed(51.9, 19.8, 0.9306) == completeness
    assert failed(42.2, 12.1, 0.9496) == completeness
    # Вся измеренная рабочая полоса, от самого консервативного порога до самого мягкого.
    assert failed(77.1, 35.1, 0.8681) == set()
    assert failed(80.8, 36.8, 0.8567) == set()
    assert failed(90.1, 41.2, 0.8353) == set()


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


def _items_across_publishers(titles: list[str]) -> list[tuple[str, str, str]]:
    """Материалы, размазанные по изданиям так, как выглядит настоящий broad-релиз.

    Пол `collection_provider_share` требует 70% от двадцати одного издания, то есть
    пятнадцать. Берём шестнадцать, обязательно включая все критические: релиз без
    reddit или без крупного новостного издания публиковать в broad нельзя, и фикстура
    «чистого» релиза не должна это правило обходить.
    """
    from reddit_compass.collector import expected_providers
    from reddit_compass.intelligence.quality import CRITICAL_PROVIDERS

    rest = sorted(expected_providers() - CRITICAL_PROVIDERS)
    publishers = sorted(CRITICAL_PROVIDERS) + rest[: 16 - len(CRITICAL_PROVIDERS)]
    return [
        (f"i{index}", titles[index % len(titles)], publisher)
        for index, publisher in enumerate(publishers)
    ]


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
        # Чистый релиз обязан и выглядеть чистым: раньше все восемь материалов шли от
        # `reuters`, то есть от одного издания из двадцати одного. Пол покрытия такой
        # релиз публиковать в broad не даст — и это правильно, поэтому фикстура
        # приведена к виду настоящего broad-релиза: шестнадцать изданий, включая все
        # шесть критических, по два материала на тему для баланса рубрик.
        items=_items_across_publishers(
            [
                "OpenAI GPT LLM release",
                "surveillance camera facial recognition tracking privacy",
                "layoff hiring salary career jobs",
                "company earnings revenue merger acquisition",
                "election senate congress government policy",
                "war ukraine nato sanctions geopolitics",
                "hollywood film music netflix celebrity",
                "climate energy solar nuclear science research",
            ]
        ),
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


def test_release_with_missing_critical_publisher_fails_the_floor(tmp_path) -> None:
    """Отсутствие reddit или крупного новостного издания блокирует broad.

    Правило владельца: блокируют «условный reddit и 5-6 новостных сайтов», остальное —
    предупреждение. Пятёрка выбрана по вкладу в боевой релиз
    `2026-07-30_2026-08-05-broad-r2` (10 927 материалов): reddit 6875, nytimes 685,
    guardian 392, reuters 358, washingtonpost 321, bbc 247 — дальше резкий обрыв
    (ft 187), поэтому граница проведена по данным, а не на глаз.
    """
    from reddit_compass.intelligence.quality import CRITICAL_PROVIDERS, QUALITY_FLOORS

    assert QUALITY_FLOORS["collection_critical_missing"]["value"] == 0
    assert QUALITY_FLOORS["collection_provider_share"]["op"] == "min"
    assert QUALITY_FLOORS["collection_provider_share"]["value"] == 70.0
    # Reddit — крупнейший вклад, без него корпус теряет больше половины материалов.
    assert "reddit" in CRITICAL_PROVIDERS
    assert len(CRITICAL_PROVIDERS) == 6


def test_provider_share_floor_matches_the_expected_publisher_set() -> None:
    """70% считаются от 21 издания, а не от 5 адаптеров — иначе порог ничего не значит."""
    from reddit_compass.collector import expected_providers
    from reddit_compass.intelligence.quality import MIN_PROVIDER_SHARE

    expected = expected_providers()
    assert len(expected) == 21
    # 70% от 21 — пятнадцать изданий: ниже этого кросс-source подтверждение,
    # ради которого существует слой Stories, держится на нескольких источниках.
    assert round(len(expected) * MIN_PROVIDER_SHARE / 100) == 15
    assert CRITICAL_SUBSET_IS_EXPECTED(expected)


def CRITICAL_SUBSET_IS_EXPECTED(expected: frozenset[str]) -> bool:
    from reddit_compass.intelligence.quality import CRITICAL_PROVIDERS

    return expected >= CRITICAL_PROVIDERS
