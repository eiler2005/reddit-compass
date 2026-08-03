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


def _percentile(values: list[int], fraction: float) -> int:
    """Ранговый перцентиль без numpy: рубрика большая, key event маленький."""
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * (len(ordered) - 1) + 0.5))
    return ordered[index]


def _measure_schema(
    stories: list[dict[str, Any]],
    *,
    depth: int = 2,
    actor_types: dict[str, tuple[str, str]] | None = None,
    baseline_roots: set[str] | None = None,
) -> dict[str, Any]:
    trends = discover_schema_trends(stories, depth=depth, actor_types=actor_types)
    total = len(stories)
    grouped = {story_id for trend in trends for story_id in trend["story_ids"]}
    sizes = [int(trend["story_count"]) for trend in trends]
    single_actor = sum(1 for trend in trends if len(trend["distinct_actors"]) < 2)
    names = [str(trend["name_ru"]) for trend in trends]
    roots = {str(t["schema_key"]) for t in trends if int(t["depth"]) == 2}
    children = [trend for trend in trends if int(trend["depth"]) == 3]
    parents = {str(child["parent_schema_key"]) for child in children}
    # Доля сгруппированных сюжетов, у которых тип вообще нашёлся. Если она низкая,
    # глубина 3 — бутафория: ключ формально трёхкомпонентный, а делить нечем.
    typed = 0
    if actor_types:
        from reddit_compass.intelligence.actor_types import normalize_title_key

        by_id = {str(story["story_id"]): story for story in stories}
        typed = sum(
            1
            for story_id in grouped
            if normalize_title_key(str(by_id.get(story_id, {}).get("title") or "")) in actor_types
        )
    return {
        "variant": f"schema depth={depth}",
        "trends": len(trends),
        "roots": len(roots),
        "parents": len(parents),
        "children": len(children),
        "leaves": len(roots) - len(parents),
        # Обязан быть 0: глубина 3 только дробит и никогда не удаляет тренд.
        "parents_lost_vs_depth2": len(baseline_roots - roots) if baseline_roots else 0,
        "max_share": round(100 * max(sizes, default=0) / max(total, 1), 1),
        "median_size": _percentile(sizes, 0.5),
        "p90_size": _percentile(sizes, 0.9),
        "single_actor": single_actor,
        "dup_names": len(names) - len(set(names)),
        "typed_share": round(100 * typed / max(len(grouped), 1), 1),
        "named_by_pattern": 100.0 if trends else 0.0,
        "coverage": round(100 * len(grouped) / max(total, 1), 1),
        "top": _render_tree(trends),
        "_roots": roots,
    }


def _render_tree(trends: list[dict[str, Any]]) -> list[str]:
    """Крупнейшие тренды деревом: дети с отступом под своим родителем."""
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for trend in trends:
        if int(trend["depth"]) == 3:
            children_by_parent.setdefault(str(trend["parent_schema_key"]), []).append(trend)
    roots = sorted(
        (trend for trend in trends if int(trend["depth"]) == 2),
        key=lambda trend: -int(trend["story_count"]),
    )
    lines: list[str] = []
    for root in roots[:5]:
        lines.append(_describe(root))
        for child in children_by_parent.get(str(root["schema_key"]), []):
            lines.append(f"  ↳ {_describe(child)}")
    return lines


def _describe(trend: dict[str, Any]) -> str:
    actors = trend["distinct_actors"]
    sample = ", ".join(str(actor) for actor in actors[:4])
    return f"{trend['story_count']}× {trend['name_ru']} ({len(actors)} акторов: {sample})"


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


_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("trends", "трендов", ""),
    ("parents", "родителей", ""),
    ("children", "детей", ""),
    ("leaves", "листьев", ""),
    ("parents_lost_vs_depth2", "потеряно", ""),
    ("max_share", "макс. доля", "%"),
    ("median_size", "медиана", ""),
    ("p90_size", "p90", ""),
    ("single_actor", "одноакторных", ""),
    ("dup_names", "дублей имён", ""),
    ("typed_share", "с типом", "%"),
    ("coverage", "покрытие", "%"),
)


def _render_markdown(results: list[dict[str, Any]]) -> str:
    header = " | ".join(title for _, title, _ in _COLUMNS)
    lines = [
        f"| вариант | {header} |",
        "|---" * (len(_COLUMNS) + 1) + "|",
    ]

    def cell(row: dict[str, Any], key: str, suffix: str = "") -> str:
        value = row.get(key)
        return "—" if value is None else f"{value}{suffix}"

    for row in results:
        cells = " | ".join(cell(row, key, suffix) for key, _, suffix in _COLUMNS)
        lines.append(f"| `{row['variant']}` | {cells} |")
    lines.append("")
    for row in results:
        lines.append(f"**{row['variant']}** — крупнейшие:")
        lines.extend(f"- {entry}" for entry in row["top"])
        lines.append("")
    return "\n".join(lines)


def _resolve_actor_types(args: argparse.Namespace, stories: list[dict[str, Any]]) -> dict[str, Any]:
    """Таблица типов: из файла либо посчитанная на лету. Скрипт остаётся read-only."""
    from reddit_compass.intelligence.actor_types import load_actor_types, type_titles

    if args.actor_types:
        return load_actor_types(Path(args.actor_types))
    if args.type_now:
        return type_titles([str(story.get("title") or "") for story in stories])
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Копия trend_engine.db")
    parser.add_argument("--story-release", required=True)
    parser.add_argument("--trend-release", help="Существующий трендовый релиз для сравнения")
    parser.add_argument(
        "--depth",
        type=int,
        nargs="+",
        default=[2, 3],
        choices=[2, 3],
        help="Глубины схемного ключа для сравнения бок о бок",
    )
    parser.add_argument("--actor-types", help="Готовая таблица типов акторов (actor_types.json)")
    parser.add_argument(
        "--type-now",
        action="store_true",
        help="Посчитать типы на лету (нужен reddit-compass[actors]); в файл ничего не пишется",
    )
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args()

    stories = _load_stories(args.db, args.story_release)
    actor_types = _resolve_actor_types(args, stories)

    results: list[dict[str, Any]] = []
    baseline_roots: set[str] | None = None
    for depth in sorted(set(args.depth)):
        measured = _measure_schema(
            stories, depth=depth, actor_types=actor_types, baseline_roots=baseline_roots
        )
        if baseline_roots is None:
            baseline_roots = measured["_roots"]
        results.append(measured)
    for measured in results:
        measured.pop("_roots", None)
    if args.trend_release:
        results.insert(0, _measure_published(args.db, args.trend_release, len(stories)))

    if args.format == "json":
        print(
            json.dumps({"stories": len(stories), "results": results}, ensure_ascii=False, indent=2)
        )
    else:
        print(f"Сюжетов в релизе: {len(stories)}")
        print(f"Заголовков с типом актора в таблице: {len(actor_types)}\n")
        print(_render_markdown(results))


if __name__ == "__main__":
    main()
