"""Query service: единый источник правды для UI.

Собирает RunSummary, SourceCoverageRow из SQLite projection.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from urllib.parse import urlencode

from ..config import DEFAULT_PROFILE
from ..intelligence.taxonomy import BROAD_DOMAINS, DOMAIN_ORDER, stable_hash_id
from ..sources.registry import SOURCES
from .view_models import (
    CloudNode,
    DomainSummaryView,
    RawItemView,
    RunSummary,
    SourceCoverageRow,
    StoryCardView,
    TrendStrengthView,
    cluster_label,
    direction_label,
    domain_label,
    provider_label,
)


def resolve_latest_run(conn: sqlite3.Connection, profile: str | None = None) -> str | None:
    """Возвращает дату последнего доступного run."""
    if profile:
        row = conn.execute(
            "SELECT snapshot_date FROM runs WHERE profile = ? ORDER BY snapshot_date DESC LIMIT 1",
            (profile,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT snapshot_date FROM runs ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
    return row[0] if row else None


def build_run_summary(
    conn: sqlite3.Connection,
    date: str,
    profile: str = DEFAULT_PROFILE,
) -> RunSummary | None:
    """Строит RunSummary из SQLite."""
    run_row = conn.execute(
        "SELECT * FROM runs WHERE snapshot_date = ? AND profile = ?",
        (date, profile),
    ).fetchone()

    if not run_row:
        return None

    run_id = run_row["run_id"]

    # Unique items for this run. Do not use items.snapshot_date: rebuild/projection
    # may keep first-seen dates that differ from the run observation date.
    item_count = conn.execute(
        "SELECT COUNT(DISTINCT item_id) FROM observations WHERE run_id = ?",
        (run_id,),
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
    # Получаем фактические items по provider/section for this run.
    section_counts: dict[str, int] = {}
    rows = conn.execute(
        """SELECT i.provider, i.source_section, COUNT(*) as cnt
           FROM observations o
           JOIN items i ON i.item_id = o.item_id
           WHERE o.run_id = ?
           GROUP BY i.provider, i.source_section""",
        (run_id,),
    ).fetchall()

    for row in rows:
        section = row["source_section"] or row["provider"]
        section_counts[f"{row['provider']}:{section}"] = row["cnt"]

    # Получаем source health из run
    health_rows = conn.execute(
        "SELECT * FROM source_health WHERE run_id = ?",
        (run_id,),
    ).fetchall()

    health_by_source: dict[str, dict[str, object]] = {}
    for row in health_rows:
        health_by_source[row["source_id"]] = dict(row)

    coverage: list[SourceCoverageRow] = []

    if health_by_source:
        for source_id, health in health_by_source.items():
            provider = str(health.get("provider", source_id))
            section = str(health.get("message", "")) or source_id.split(":", 1)[-1]
            source_def = SOURCES.get(provider) or SOURCES.get(source_id)
            item_count = _int_value(health.get("count") or section_counts.get(source_id, 0))
            status = str(health.get("status", "skipped"))
            cluster = health.get("cluster", source_def.cluster if source_def else "")
            coverage.append(
                SourceCoverageRow(
                    source_id=source_id,
                    label=_coverage_label(provider, section),
                    adapter=source_def.access if source_def else "mixed",
                    source_cluster=str(cluster),
                    configured=True,
                    expected=True,
                    attempted=status not in {"skipped", "not_configured"},
                    status=status,  # type: ignore[arg-type]
                    item_count=item_count,
                    content_scope=source_def.default_scope if source_def else "headline",
                    duration_sec=_float_value(health.get("duration_sec") or 0.0),
                    message=section,
                )
            )

    for source_id, source_def in SOURCES.items():
        if not source_def.enabled_by_default and not source_def.requires_env:
            continue

        provider_has_section_health = any(
            key.startswith(f"{source_def.provider}:") for key in health_by_source
        )
        if source_id in health_by_source or provider_has_section_health:
            continue
        health = health_by_source.get(source_id, {})
        configured = all(os.environ.get(var) for var in source_def.requires_env)
        provider_total = sum(
            count
            for provider_section, count in section_counts.items()
            if provider_section.startswith(f"{source_def.provider}:")
        )

        # Определяем статус
        if source_def.requires_env and not configured:
            status = "not_configured"
        else:
            status = "ok" if provider_total > 0 else "skipped"

        duration_val = health.get("duration_sec")
        coverage.append(
            SourceCoverageRow(
                source_id=source_id,
                label=source_def.label,
                adapter=source_def.access,
                source_cluster=source_def.cluster,
                configured=configured,
                expected=source_def.enabled_by_default or bool(source_def.requires_env),
                attempted=configured and (source_id in health_by_source or provider_total > 0),
                status=status,  # type: ignore[arg-type]
                item_count=provider_total,
                content_scope=source_def.default_scope,
                duration_sec=float(duration_val) if duration_val is not None else None,  # type: ignore[arg-type]
                message=str(health.get("message", "")),
            )
        )

    # Сортируем: успешные первыми, потом по количеству
    coverage.sort(key=lambda c: (-c.item_count, c.source_id))

    return coverage


def _coverage_label(provider: str, section: str) -> str:
    if section and section != provider:
        clean_section = section.split(":", 1)[-1].replace("_", " ").title()
        return f"{provider_label(provider)} / {clean_section}"
    return provider_label(provider)


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

    def explore_url(**filters: str) -> str:
        params: dict[str, str] = {}
        if ":" in run_id:
            date, profile = run_id.split(":", 1)
            params["date"] = date
            params["profile"] = profile
        params.update({key: value for key, value in filters.items() if value})
        return f"/explore?{urlencode(params)}"

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
                    url=explore_url(theme=theme_id),
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
                    node_id=stable_hash_id("candidate", candidate, length=10),
                    label_ru=candidate,
                    label_original=candidate,
                    item_count=count,
                    url=explore_url(candidate_theme=candidate),
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
                node_id=stable_hash_id("pain", pain, length=10),
                label_ru=pain,
                item_count=count,
                url=explore_url(pain=pain),
            )
        )

    return stable_themes, emerging_candidates, pain_points


def build_domain_summaries(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[DomainSummaryView]:
    """Build broad domain cards for Radar navigation."""
    rows = conn.execute(
        """SELECT s.story_id, s.domain_ids, sm.trend_score
           FROM story_metrics sm
           JOIN stories s ON sm.story_id = s.story_id
           WHERE sm.run_id = ?""",
        (run_id,),
    ).fetchall()
    item_rows = conn.execute(
        """SELECT i.item_id, i.domain_ids, i.provider
           FROM observations o
           JOIN items i ON i.item_id = o.item_id
           WHERE o.run_id = ?""",
        (run_id,),
    ).fetchall()

    story_counts = {domain_id: 0 for domain_id in DOMAIN_ORDER}
    item_counts = {domain_id: 0 for domain_id in DOMAIN_ORDER}
    source_sets: dict[str, set[str]] = {domain_id: set() for domain_id in DOMAIN_ORDER}
    top_scores = {domain_id: 0.0 for domain_id in DOMAIN_ORDER}

    for row in rows:
        domains = _json_list(row["domain_ids"], fallback=["other"])
        for domain_id in domains:
            if domain_id not in BROAD_DOMAINS:
                continue
            story_counts[domain_id] += 1
            top_scores[domain_id] = max(top_scores[domain_id], float(row["trend_score"] or 0.0))

    for row in item_rows:
        domains = _json_list(row["domain_ids"], fallback=["other"])
        for domain_id in domains:
            if domain_id not in BROAD_DOMAINS:
                continue
            item_counts[domain_id] += 1
            source_sets[domain_id].add(row["provider"])

    return [
        DomainSummaryView(
            domain_id=domain_id,
            label_ru=domain.label_ru,
            item_count=item_counts[domain_id],
            story_count=story_counts[domain_id],
            source_count=len(source_sets[domain_id]),
            top_score=top_scores[domain_id],
            url=f"?mode=broad&domain={domain_id}",
        )
        for domain_id, domain in BROAD_DOMAINS.items()
        if domain_id != "other" or item_counts[domain_id] > 0
    ]


def build_domain_matrix(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[dict[str, object]]:
    """Build category x source-cluster matrix."""
    rows = conn.execute(
        """SELECT i.domain_ids, i.source_cluster, COUNT(*) AS cnt
           FROM observations o
           JOIN items i ON i.item_id = o.item_id
           WHERE o.run_id = ?
           GROUP BY i.domain_ids, i.source_cluster""",
        (run_id,),
    ).fetchall()
    clusters = ("voices", "developers", "mainstream", "business", "tech_culture", "product_pulse")
    matrix = {
        domain_id: {"domain_id": domain_id, "label_ru": domain.label_ru, **{c: 0 for c in clusters}}
        for domain_id, domain in BROAD_DOMAINS.items()
        if domain_id != "other"
    }
    for row in rows:
        for domain_id in _json_list(row["domain_ids"], fallback=["other"]):
            if domain_id in matrix and row["source_cluster"] in clusters:
                cluster = str(row["source_cluster"])
                current = _int_value(matrix[domain_id].get(cluster))
                matrix[domain_id][cluster] = current + _int_value(row["cnt"])
    return [matrix[domain_id] for domain_id in DOMAIN_ORDER if domain_id in matrix]


def build_trend_shelves(
    conn: sqlite3.Connection,
    run_id: str,
    domain: str | None = None,
    limit_per_shelf: int = 6,
) -> dict[str, list[StoryCardView]]:
    """Build Radar shelves with cross-section dedupe."""
    rows = _story_rows(conn, run_id, domain=domain, limit=300)
    clusters_by_story = _clusters_by_story(conn, run_id)

    shelves: dict[str, list[StoryCardView]] = {
        "top_growing": [],
        "new": [],
        "resurfacing": [],
        "cross_source_confirmed": [],
        "undercovered": [],
        "mainstream_only": [],
        "people_only": [],
    }
    used: set[str] = set()

    def add(shelf: str, row: sqlite3.Row) -> None:
        if row["story_id"] in used or len(shelves[shelf]) >= limit_per_shelf:
            return
        shelves[shelf].append(_story_card_from_row(row, clusters_by_story))
        used.add(row["story_id"])

    for row in rows:
        if row["direction"] == "growing":
            add("top_growing", row)
    for row in rows:
        if row["direction"] == "new":
            add("new", row)
    for row in rows:
        if row["direction"] == "resurfacing":
            add("resurfacing", row)
    for row in rows:
        if row["source_count"] >= 2:
            add("cross_source_confirmed", row)
    for row in rows:
        if row["source_count"] == 1 and float(row["trend_score"] or 0) >= 45:
            add("undercovered", row)
    for row in rows:
        clusters = clusters_by_story.get(row["story_id"], set())
        if clusters and clusters <= {"mainstream", "business", "tech_culture"}:
            add("mainstream_only", row)
    for row in rows:
        clusters = clusters_by_story.get(row["story_id"], set())
        if clusters and clusters <= {"voices", "developers"}:
            add("people_only", row)

    return shelves


def build_trend_strength(
    conn: sqlite3.Connection,
    run_id: str,
    limit: int = 20,
) -> list[TrendStrengthView]:
    """Строит список силы трендов."""
    rows = conn.execute(
        """SELECT sm.story_id, s.title, sm.trend_score, sm.novelty,
                  sm.cross_source_coverage, sm.direction, sm.source_count, sm.item_count
           FROM story_metrics sm
           JOIN stories s ON sm.story_id = s.story_id
           WHERE sm.run_id = ?
           ORDER BY sm.trend_score DESC
           LIMIT ?""",
        (run_id, limit),
    ).fetchall()

    return [
        TrendStrengthView(
            story_id=row["story_id"],
            title=row["title"],
            trend_score=row["trend_score"],
            novelty=row["novelty"],
            coverage=row["cross_source_coverage"],
            direction=row["direction"],
            direction_label=direction_label(row["direction"]),
            provider_count=row["source_count"],
            item_count=row["item_count"],
        )
        for row in rows
    ]


def build_raw_popular_items(
    conn: sqlite3.Connection,
    date: str,
    profile: str = DEFAULT_PROFILE,
    limit: int = 20,
) -> list[RawItemView]:
    """Строит список популярных items (raw engagement)."""
    run_row = conn.execute(
        "SELECT run_id FROM runs WHERE snapshot_date = ? AND profile = ?",
        (date, profile),
    ).fetchone()
    if not run_row:
        return []
    rows = conn.execute(
        """SELECT item_id, title, provider, source_cluster, canonical_url, raw_engagement
           FROM items
           WHERE item_id IN (SELECT item_id FROM observations WHERE run_id = ?)
           ORDER BY json_extract(raw_engagement, '$.score') DESC
           LIMIT ?""",
        (run_row["run_id"], limit),
    ).fetchall()

    items = []
    for row in rows:
        engagement = json.loads(row["raw_engagement"]) if row["raw_engagement"] else {}
        items.append(
            RawItemView(
                item_id=row["item_id"],
                title=row["title"],
                provider=row["provider"],
                source_cluster=row["source_cluster"],
                url=row["canonical_url"],
                score=int(engagement.get("score", 0)),
                comments=int(engagement.get("comments", 0)),
            )
        )
    return items


def build_goal_relevance_rankings(
    conn: sqlite3.Connection,
    run_id: str,
    goals: list[str],
    limit: int = 10,
) -> dict[str, list[StoryCardView]]:
    """Строит rankings по goal relevance."""
    rankings: dict[str, list[StoryCardView]] = {}
    clusters_by_story = _clusters_by_story(conn, run_id)
    rows = _story_rows(conn, run_id, limit=300)

    for goal in goals:
        ranked_rows = sorted(
            rows,
            key=lambda row: (
                _project_score(row["metric_project_scores"], goal),
                float(row["trend_score"] or 0),
            ),
            reverse=True,
        )[:limit]
        rankings[goal] = [_story_card_from_row(row, clusters_by_story) for row in ranked_rows]

    return rankings


def _story_rows(
    conn: sqlite3.Connection,
    run_id: str,
    domain: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    where = ["sm.run_id = ?"]
    params: list[object] = [run_id]
    if domain:
        where.append("s.domain_ids LIKE ?")
        params.append(f'%"{domain}"%')
    return conn.execute(
        f"""SELECT sm.story_id, s.title, s.summary_ru, s.domain_ids,
                   sm.trend_score, sm.direction, sm.confidence,
                   sm.source_count, sm.item_count, sm.project_scores AS metric_project_scores
            FROM story_metrics sm
            JOIN stories s ON sm.story_id = s.story_id
            WHERE {" AND ".join(where)}
            ORDER BY sm.trend_score DESC
            LIMIT ?""",
        [*params, limit],
    ).fetchall()


def _clusters_by_story(conn: sqlite3.Connection, run_id: str) -> dict[str, set[str]]:
    rows = conn.execute(
        """SELECT si.story_id, i.source_cluster
           FROM story_items si
           JOIN items i ON i.item_id = si.item_id
           WHERE si.run_id = ?""",
        (run_id,),
    ).fetchall()
    clusters: dict[str, set[str]] = {}
    for row in rows:
        clusters.setdefault(row["story_id"], set()).add(row["source_cluster"])
    return clusters


def _story_card_from_row(
    row: sqlite3.Row,
    clusters_by_story: dict[str, set[str]],
) -> StoryCardView:
    domain_ids = _json_list(row["domain_ids"], fallback=["other"])
    clusters = sorted(clusters_by_story.get(row["story_id"], set()))
    return StoryCardView(
        story_id=row["story_id"],
        title=row["title"],
        summary_ru=row["summary_ru"],
        direction=row["direction"],
        direction_label=direction_label(row["direction"]),
        trend_score=float(row["trend_score"] or 0.0),
        confidence=row["confidence"],
        why_it_matters="",
        source_count=int(row["source_count"] or 0),
        item_count=int(row["item_count"] or 0),
        domain_ids=domain_ids,
        domain_labels=[domain_label(domain_id) for domain_id in domain_ids],
        clusters=clusters,
        clusters_display=[cluster_label(cluster) for cluster in clusters],
    )


def _json_list(raw: object, fallback: list[str] | None = None) -> list[str]:
    if isinstance(raw, list):
        return [str(v) for v in raw]
    if isinstance(raw, str) and raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(v) for v in data]
        except json.JSONDecodeError:
            pass
    return list(fallback or [])


def _project_score(raw: object, goal: str) -> int:
    if isinstance(raw, str) and raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
    elif isinstance(raw, dict):
        data = raw
    else:
        data = {}
    return int(data.get(goal, 0) or 0)


def _int_value(raw: object, default: int = 0) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float | str | bytes | bytearray):
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def _float_value(raw: object, default: float = 0.0) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool | int | float | str | bytes | bytearray):
        try:
            return float(raw)
        except ValueError:
            return default
    return default
