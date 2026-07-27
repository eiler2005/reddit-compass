"""Query service: единый источник правды для UI.

Собирает RunSummary, SourceCoverageRow из SQLite projection.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..sources.registry import SOURCES
from .view_models import CloudNode, RunSummary, SourceCoverageRow


def resolve_latest_run(conn: sqlite3.Connection, profile: str = "ai-native") -> str | None:
    """Возвращает дату последнего доступного run."""
    row = conn.execute(
        "SELECT snapshot_date FROM runs WHERE profile = ? ORDER BY snapshot_date DESC LIMIT 1",
        (profile,),
    ).fetchone()
    return row[0] if row else None


def build_run_summary(
    conn: sqlite3.Connection,
    date: str,
    profile: str = "ai-native",
) -> RunSummary | None:
    """Строит RunSummary из SQLite."""
    run_row = conn.execute(
        "SELECT * FROM runs WHERE snapshot_date = ? AND profile = ?",
        (date, profile),
    ).fetchone()

    if not run_row:
        return None

    run_id = run_row["run_id"]

    # Unique items
    item_count = conn.execute(
        "SELECT COUNT(DISTINCT item_id) FROM items WHERE snapshot_date = ?",
        (date,),
    ).fetchone()[0]

    # Stories
    story_count = conn.execute(
        "SELECT COUNT(*) FROM story_metrics WHERE run_id = ?",
        (run_id,),
    ).fetchone()[0]

    # Analyzed items (with signals)
    analyzed_count = conn.execute(
        "SELECT COUNT(*) FROM item_signals WHERE run_id = ?",
        (run_id,),
    ).fetchone()[0]

    # Source coverage
    coverage = build_source_coverage(conn, run_id, date)
    successful = sum(1 for c in coverage if c.status == "ok")
    expected = sum(1 for c in coverage if c.expected)

    # Adapter families
    adapters = {c.adapter for c in coverage if c.attempted}

    return RunSummary(
        run_id=run_id,
        date=date,
        profile=profile,
        status=run_row["status"],
        started_at=run_row["started_at"],
        finished_at=run_row["finished_at"],
        unique_item_count=item_count,
        analyzed_item_count=analyzed_count,
        story_count=story_count,
        expected_provider_count=expected,
        successful_provider_count=successful,
        adapter_family_count=len(adapters),
    )


def build_source_coverage(
    conn: sqlite3.Connection,
    run_id: str,
    date: str,
) -> list[SourceCoverageRow]:
    """Строит список покрытия источников."""
    # Получаем фактические items по providers
    provider_counts: dict[str, int] = {}
    rows = conn.execute(
        """SELECT provider, COUNT(*) as cnt
           FROM items WHERE snapshot_date = ?
           GROUP BY provider""",
        (date,),
    ).fetchall()

    for row in rows:
        provider_counts[row["provider"]] = row["cnt"]

    # Получаем source health из run
    health_rows = conn.execute(
        "SELECT * FROM source_health WHERE run_id = ?",
        (run_id,),
    ).fetchall()

    health_by_source: dict[str, dict[str, object]] = {}
    for row in health_rows:
        health_by_source[row["source_id"]] = dict(row)

    # Строим coverage для всех известных источников
    coverage: list[SourceCoverageRow] = []

    for source_id, source_def in SOURCES.items():
        # Пропускаем источники, которые не enabled по умолчанию
        if not source_def.enabled_by_default:
            continue

        health = health_by_source.get(source_id, {})
        item_count = provider_counts.get(source_def.provider, 0)

        # Определяем статус
        if source_id in health_by_source:
            status = health.get("status", "skipped")
            if status == "ok" and item_count == 0:
                status = "empty"
        elif item_count > 0:
            status = "ok"
        else:
            status = "skipped"

        duration_val = health.get("duration_sec")
        coverage.append(
            SourceCoverageRow(
                source_id=source_id,
                label=source_def.label,
                adapter=source_def.access,
                source_cluster=source_def.cluster,
                configured=True,
                expected=source_def.enabled_by_default,
                attempted=source_id in health_by_source or item_count > 0,
                status=status,  # type: ignore[arg-type]
                item_count=item_count,
                content_scope=source_def.default_scope,
                duration_sec=float(duration_val) if duration_val is not None else None,  # type: ignore[arg-type]
                message=str(health.get("message", "")),
            )
        )

    # Сортируем: успешные первыми, потом по количеству
    coverage.sort(key=lambda c: (-c.item_count, c.source_id))

    return coverage


def build_freshness_line(summary: RunSummary) -> str:
    """Строит строку freshness для header."""
    status_text = {
        "complete": "Полный",
        "partial": "Частичный",
        "running": "Выполняется",
        "failed": "Ошибка",
    }.get(summary.status, summary.status)

    parts = [status_text]

    if summary.finished_at:
        try:
            dt = datetime.fromisoformat(summary.finished_at.replace("Z", "+00:00"))
            parts.append(f"обновлено {dt.strftime('%H:%M')}")
        except ValueError:
            pass

    parts.append(
        f"{summary.successful_provider_count}/{summary.expected_provider_count} источников"
    )
    parts.append(f"{summary.unique_item_count} материалов")

    return " · ".join(parts)


def build_theme_clouds(
    conn: sqlite3.Connection,
    run_id: str,
    theme_catalog: list[dict[str, str]] | None = None,
) -> tuple[list[CloudNode], list[CloudNode], list[CloudNode]]:
    """Строит три облака: stable themes, emerging candidates, pain points.

    Returns:
        Tuple of (stable_themes, emerging_candidates, pain_points).
    """
    # Получаем item signals для run
    signals = conn.execute(
        "SELECT * FROM item_signals WHERE run_id = ?",
        (run_id,),
    ).fetchall()

    if not signals:
        return [], [], []

    # Stable themes: из theme_catalog (profile taxonomy)
    stable_themes: list[CloudNode] = []
    if theme_catalog:
        theme_ids = {t["id"] for t in theme_catalog}
        theme_labels = {t["id"]: t.get("label", t["id"]) for t in theme_catalog}

        # Считаем items по theme_ids
        theme_counts: dict[str, int] = {}
        for sig in signals:
            import json

            sig_themes = json.loads(sig["theme_ids"])
            for theme_id in sig_themes:
                if theme_id in theme_ids:
                    theme_counts[theme_id] = theme_counts.get(theme_id, 0) + 1

        for theme_id, count in sorted(theme_counts.items(), key=lambda x: -x[1]):
            stable_themes.append(
                CloudNode(
                    node_id=theme_id,
                    label_ru=theme_labels.get(theme_id, theme_id),
                    item_count=count,
                    url=f"/explore?theme={theme_id}",
                )
            )

    # Emerging candidates: из candidate_themes
    candidate_counts: dict[str, int] = {}
    for sig in signals:
        import json

        candidates = json.loads(sig["candidate_themes"])
        for candidate in candidates:
            candidate_counts[candidate] = candidate_counts.get(candidate, 0) + 1

    emerging_candidates: list[CloudNode] = []
    for candidate, count in sorted(candidate_counts.items(), key=lambda x: -x[1])[:20]:
        if count >= 2:  # Только кандидаты с 2+ упоминаниями
            emerging_candidates.append(
                CloudNode(
                    node_id=f"candidate_{hash(candidate) % 10000}",
                    label_ru=candidate,
                    label_original=candidate,
                    item_count=count,
                )
            )

    # Pain points: нормализованные
    pain_counts: dict[str, int] = {}
    for sig in signals:
        import json

        pains = json.loads(sig["pain_points"])
        for pain in pains:
            pain_counts[pain] = pain_counts.get(pain, 0) + 1

    pain_points: list[CloudNode] = []
    for pain, count in sorted(pain_counts.items(), key=lambda x: -x[1])[:15]:
        pain_points.append(
            CloudNode(
                node_id=f"pain_{hash(pain) % 10000}",
                label_ru=pain,
                item_count=count,
            )
        )

    return stable_themes, emerging_candidates, pain_points
