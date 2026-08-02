#!/usr/bin/env python
"""Машинка замеров слоя Trends: одинаковые числа для разных поколений движка.

Зачем: до сих пор каждое поколение мерилось разовым скриптом в scratch, поэтому числа
поколений нельзя было положить рядом. Здесь один вход, один набор метрик и один формат
вывода, пригодный для коммита в git.

Запуск на замороженном релизе (production DB не трогается — работать на копии):

    python scripts/measure_trend_variants.py \
        --db /path/to/copy.db --story-release stories_... --format markdown

Метрики намеренно те, что отвечают на вопрос «это тренды или корзина слов»:

* ``trends``            — сколько трендов получилось;
* ``max_share``         — доля сюжетов в крупнейшем тренде (пол гейта ≤ 10%);
* ``single_actor``      — трендов с одним актором; для схемного слоя обязан быть 0,
                          потому что один актор — это сюжетная линия, а не паттерн;
* ``named_by_pattern``  — доля имён, собранных из схемы, а не из частотных слов;
* ``coverage``          — доля сюжетов, попавших хоть в один тренд.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reddit_compass.intelligence.trend_schema import (
    discover_schema_trends,
)


def _load_stories(db: Path, story_release: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM engine_stories WHERE story_release_id = ? ORDER BY story_id",
            (story_release,),
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise SystemExit(f"Нет сюжетов для {story_release} в {db}")
    return [dict(row) for row in rows]


def _measure_schema(stories: list[dict[str, Any]]) -> dict[str, Any]:
    trends = discover_schema_trends(stories)
    total = len(stories)
    grouped = {story_id for trend in trends for story_id in trend["story_ids"]}
    sizes = [int(trend["story_count"]) for trend in trends]
    single_actor = sum(1 for trend in trends if len(trend["distinct_actors"]) < 2)
    return {
        "variant": "schema_v1",
        "trends": len(trends),
        "max_share": round(100 * max(sizes, default=0) / max(total, 1), 1),
        "single_actor": single_actor,
        "named_by_pattern": 100.0 if trends else 0.0,
        "coverage": round(100 * len(grouped) / max(total, 1), 1),
        "top": [
            f"{trend['story_count']}× {trend['name_ru']} ({len(trend['distinct_actors'])} акторов)"
            for trend in trends[:5]
        ],
    }


def _measure_published(db: Path, trend_release: str, story_total: int) -> dict[str, Any]:
    """Замер уже существующего трендового релиза — для сравнения с прежним поколением."""
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT name_ru, story_count FROM engine_trends WHERE trend_release_id = ?",
            (trend_release,),
        ).fetchall()
    finally:
        connection.close()
    sizes = [int(row["story_count"] or 0) for row in rows]
    return {
        "variant": f"published:{trend_release[:22]}",
        "trends": len(rows),
        "max_share": round(100 * max(sizes, default=0) / max(story_total, 1), 1),
        "single_actor": None,
        "named_by_pattern": 0.0,
        "coverage": None,
        "top": [f"{int(r['story_count'] or 0)}× {r['name_ru'][:44]}" for r in rows[:5]],
    }


def _render_markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "| вариант | трендов | макс. доля | одноакторных | имя из схемы | покрытие |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    def cell(row: dict[str, Any], key: str, suffix: str = "") -> str:
        value = row[key]
        return "—" if value is None else f"{value}{suffix}"

    for row in results:
        lines.append(
            f"| `{row['variant']}` | {row['trends']} | {cell(row, 'max_share', '%')} | "
            f"{cell(row, 'single_actor')} | {cell(row, 'named_by_pattern', '%')} | "
            f"{cell(row, 'coverage', '%')} |"
        )
    lines.append("")
    for row in results:
        lines.append(f"**{row['variant']}** — крупнейшие:")
        lines.extend(f"- {entry}" for entry in row["top"])
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Копия trend_engine.db")
    parser.add_argument("--story-release", required=True)
    parser.add_argument("--trend-release", help="Существующий трендовый релиз для сравнения")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args()

    stories = _load_stories(args.db, args.story_release)
    results = [_measure_schema(stories)]
    if args.trend_release:
        results.insert(0, _measure_published(args.db, args.trend_release, len(stories)))

    if args.format == "json":
        print(
            json.dumps({"stories": len(stories), "results": results}, ensure_ascii=False, indent=2)
        )
    else:
        print(f"Сюжетов в релизе: {len(stories)}\n")
        print(_render_markdown(results))


if __name__ == "__main__":
    main()
