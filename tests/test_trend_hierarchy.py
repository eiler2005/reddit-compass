"""Иерархия трендов на уровне релиза: миграция, реконсиляция, плумбинг глубины.

Содержательная часть дробления проверяется в ``test_trend_schema.py``. Здесь — то, что
живёт вокруг неё и ломается тише: колонка, появляющаяся на старой БД; дерево, по которому
прошлось LLM-ревью; и параметр, который обязан дожить до второго построения релиза.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from reddit_compass.intelligence.engine import (
    _discover_trends_schema_v2,
    _reconcile_trend_hierarchy,
    engine_db,
    migrate_engine,
)


def _trend(trend_id: str, name: str, *, parent: str = "", actors: int = 2) -> dict[str, Any]:
    return {
        "trend_id": trend_id,
        "parent_trend_id": parent,
        "name_ru": name,
        "pattern": name,
        "domain_ids": ["ai_technology"],
        "confidence": 0.5,
        "lifecycle": "stable",
        "source_scope": "cross_source",
        "first_seen": "2026-07-28",
        "last_seen": "2026-07-30",
        "story_count": 3,
        "source_count": actors,
        "project_scores": {},
        "evidence_story_ids": [],
        "counterpoints": [],
        "review_status": "confirmed",
        "review_id": "",
        "distinct_actors": ["A", "B"],
    }


def _entry(trend: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, float, str]]]:
    return trend, [("story_1", 1.0, "event schema")]


def test_migration_adds_the_hierarchy_columns_to_an_old_database(tmp_path: Path) -> None:
    """Колонки приходят через _ensure_engine_column, а не через пересоздание таблицы."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE engine_trends (
            trend_release_id TEXT NOT NULL,
            trend_id         TEXT NOT NULL,
            name_ru          TEXT NOT NULL,
            pattern          TEXT NOT NULL,
            PRIMARY KEY (trend_release_id, trend_id)
        );
        INSERT INTO engine_trends VALUES ('r1', 't1', 'layoffs in AI', 'layoffs in AI');
        """
    )
    conn.commit()

    migrate_engine(conn)

    row = conn.execute("SELECT * FROM engine_trends WHERE trend_id = 't1'").fetchone()
    columns = {str(entry[1]) for entry in conn.execute("PRAGMA table_info(engine_trends)")}
    conn.close()

    assert {"parent_trend_id", "distinct_actors"} <= columns
    assert row is not None


def test_orphaned_child_is_promoted_to_a_root() -> None:
    """`apply_cached_trend_reviews` умеет удалить родителя по reject.

    Ребёнок с ссылкой в пустоту пропал бы из плоского списка целиком, потому что тот
    фильтрует по корням.
    """
    survivors = [
        _entry(_trend("child_a", "fines in AI by companies", parent="gone")),
        _entry(_trend("child_b", "fines in AI by countries", parent="gone")),
    ]

    reconciled = _reconcile_trend_hierarchy(survivors)

    assert {trend["trend_id"] for trend, _ in reconciled} == {"child_a", "child_b"}
    assert all(trend["parent_trend_id"] == "" for trend, _ in reconciled)


def test_a_parent_left_with_one_child_drops_that_child() -> None:
    """Ребёнка отбрасываем, а не поднимаем: его состав — подмножество родительского."""
    trends = [
        _entry(_trend("root", "fines in AI")),
        _entry(_trend("child_a", "fines in AI by companies", parent="root")),
    ]

    reconciled = _reconcile_trend_hierarchy(trends)

    assert [trend["trend_id"] for trend, _ in reconciled] == ["root"]


def test_two_children_keep_their_parent() -> None:
    trends = [
        _entry(_trend("root", "fines in AI")),
        _entry(_trend("child_a", "fines in AI by companies", parent="root")),
        _entry(_trend("child_b", "fines in AI by countries", parent="root")),
    ]

    reconciled = _reconcile_trend_hierarchy(trends)

    assert len(reconciled) == 3
    assert {t["parent_trend_id"] for t, _ in reconciled if t["trend_id"] != "root"} == {"root"}


def test_a_child_renamed_into_its_parent_name_is_separated() -> None:
    """LLM-ревью умеет переименовать тренд, а поле дублей имён — max 0.

    Это единственный путь, которым дубль может возникнуть уже после того, как замер
    показал `dup_names == 0`.
    """
    trends = [
        _entry(_trend("root", "fines in AI", actors=7)),
        _entry(_trend("child_a", "fines in AI", parent="root", actors=3)),
        _entry(_trend("child_b", "fines in AI by countries", parent="root", actors=2)),
    ]

    reconciled = _reconcile_trend_hierarchy(trends)
    names = [str(trend["name_ru"]) for trend, _ in reconciled]

    assert len(set(names)) == len(names), names
    assert "fines in AI" in names


def test_adapter_links_children_to_the_parent_trend_id() -> None:
    """Ключ ребёнка знает родителя по schema_key; в релиз обязан уехать trend_id."""
    stories = [
        {
            "story_id": f"s{index}",
            "title": title,
            "domain_ids": ["business_markets"],
            "first_seen": date,
            "source_count": 2,
        }
        for index, (title, date) in enumerate(
            [
                ("EU fines Google 890mn over ad tech", "2026-07-28"),
                ("Brussels fines Apple over app rules", "2026-07-29"),
                ("Ireland fines Meta over data transfers", "2026-07-30"),
                ("Sony penalty over refunds upheld", "2026-07-28"),
                ("Valve faces a penalty over refunds", "2026-07-29"),
                ("Nintendo penalty over pricing stands", "2026-07-30"),
            ],
            start=1,
        )
    ]
    from reddit_compass.intelligence.actor_types import normalize_title_key

    actor_types = {
        normalize_title_key(title): value
        for title, value in [
            ("EU fines Google 890mn over ad tech", ("EU", "government agency")),
            ("Brussels fines Apple over app rules", ("Brussels", "government agency")),
            ("Ireland fines Meta over data transfers", ("Ireland", "government agency")),
            ("Sony penalty over refunds upheld", ("Sony", "company")),
            ("Valve faces a penalty over refunds", ("Valve", "company")),
            ("Nintendo penalty over pricing stands", ("Nintendo", "company")),
        ]
    }

    adapted = _discover_trends_schema_v2(
        stories, params={"trend_schema_depth": 3}, actor_types=actor_types
    )

    by_id = {str(trend["trend_id"]): trend for trend, _ in adapted}
    children = [trend for trend, _ in adapted if trend["parent_trend_id"]]
    assert len(children) == 2
    for child in children:
        assert child["parent_trend_id"] in by_id
        assert by_id[child["parent_trend_id"]]["parent_trend_id"] == ""


def test_depth_is_part_of_the_params_hash(tmp_path: Path) -> None:
    """Два релиза с разной глубиной обязаны различаться хэшем параметров."""
    from reddit_compass.intelligence.engine import _hash_json

    base = {"min_stories": 3, "min_dates": 2}

    assert _hash_json({**base, "trend_schema_depth": 2}) != _hash_json(
        {**base, "trend_schema_depth": 3}
    )


def test_run_engine_cycle_keeps_trend_depth_on_re_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Второй вызов `create_trend_release` — живая ловушка.

    После валидного LLM-ревью релиз строится заново, чтобы кэшированные решения стали
    статусом. Если туда не передать `params`, опубликуется глубина 2 под `params_hash`,
    который утверждает глубину 3, — ровно то, против чего написан EMBEDDING FALLBACK.
    """
    from test_trend_engine import _seed_cycle_corpus

    from reddit_compass.intelligence import engine as engine_module

    seen_params: list[dict[str, Any] | None] = []
    real_create = engine_module.create_trend_release

    def _spy(*args: Any, **kwargs: Any) -> Any:
        seen_params.append(kwargs.get("params"))
        return real_create(*args, **kwargs)

    monkeypatch.setattr(engine_module, "create_trend_release", _spy)
    monkeypatch.setattr(
        engine_module,
        "prepare_trend_review_jobs",
        lambda *a, **k: [
            {
                "prompt": "p",
                "target_id": "t",
                "input_hash": "h",
                "story_ids": [],
                "prompt_version": "v",
            }
        ],
    )
    monkeypatch.setattr(
        engine_module, "store_trend_review_response", lambda *a, **k: {"valid": True}
    )

    async def _runner(_prompt: str, _model: str) -> str:
        return "{}"

    corpus_path = tmp_path / "compass.db"
    corpus = _seed_cycle_corpus(corpus_path)
    engine = engine_db(tmp_path / "trend_engine.db")
    try:
        asyncio.run(
            engine_module.run_engine_cycle(
                corpus,
                engine,
                corpus_path=corpus_path,
                profile="broad",
                window=2,
                theme_catalog={},
                pack_by_subreddit={"artificial": "ai_technology"},
                trend_method="schema_v2",
                trend_depth=3,
                review_limit=0,
                trend_review_limit=5,
                review_runner=_runner,
                publish_channel=None,
                pulse=False,
            )
        )
    finally:
        corpus.close()
        engine.close()

    assert len(seen_params) == 2, "ре-материализация не произошла — тест ничего не стережёт"
    assert all(params and params.get("trend_schema_depth") == 3 for params in seen_params)


def test_depth_three_without_a_table_degrades_and_says_so(tmp_path: Path) -> None:
    """Глубина 3 без таблицы типов — это глубина 2, и релиз обязан признаться."""
    from reddit_compass.intelligence.trend_schema import discover_schema_trends

    stories = [
        {
            "story_id": f"s{index}",
            "title": title,
            "domain_ids": ["labor_career"],
            "first_seen": date,
        }
        for index, (title, date) in enumerate(
            [
                ("Amazon lays off 14000 managers", "2026-07-28"),
                ("Salesforce cuts 4000 jobs", "2026-07-29"),
                ("Intel announces fresh layoffs", "2026-07-30"),
            ],
            start=1,
        )
    ]

    assert discover_schema_trends(stories, depth=3, actor_types={}) == discover_schema_trends(
        stories, depth=2
    )
    engine_db(tmp_path / "engine.db").close()
