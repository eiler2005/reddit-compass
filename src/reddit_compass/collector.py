"""Collection-only runtime.

This module owns network adapters and raw corpus persistence. It intentionally
does not import clustering, ranking, briefing, item-signal or LLM modules.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from .config import DEFAULT_PROFILE, MonitorConfig
from .intelligence.compat import load_legacy_jsonl
from .intelligence.migrations import migrate
from .intelligence.models import (
    ContentItem,
    Observation,
    SourceHealth,
    SourceStatus,
)
from .intelligence.repository import upsert_items, upsert_observations, upsert_run
from .models import PostCard
from .sources.registry import PRODUCTION_PROFILES

logger = logging.getLogger("reddit_compass")

DEFAULT_SOURCES = ["reddit", "hackernews", "rss", "ladder", "producthunt"]
_ALIASES = {"hn": "hackernews", "ph": "producthunt"}
_FILE_MAP = {
    "reddit": "posts.jsonl",
    "hackernews": "hackernews.jsonl",
    "rss": "rss.jsonl",
    "ladder": "ladder.jsonl",
    "producthunt": "producthunt.jsonl",
}


@dataclass(frozen=True)
class SourceResult:
    source_id: str
    status: str
    count: int = 0
    duration_sec: float = 0.0
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True)
class CollectionResult:
    run_id: str
    date: str
    profile: str
    status: str
    source_results: list[SourceResult] = field(default_factory=list)
    items: list[ContentItem] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


def _parse_snapshot_date(snapshot_date: str) -> datetime:
    """Validate a calendar date used as an immutable snapshot identity."""
    try:
        return datetime.strptime(snapshot_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("snapshot_date must use YYYY-MM-DD") from exc


def _canonical_snapshot_date(snapshot_date: str) -> str:
    """Validate and normalize a snapshot date to its canonical ``YYYY-MM-DD`` spelling.

    ``strptime`` accepts ``2026-8-3``, but stored dates are always zero-padded, so an
    un-normalized value silently misses every comparison: the coverage query compares
    ``snapshot_date BETWEEN ? AND ?`` as text, and ``'2026-08-04' >= '2026-8-3'`` is
    false.  A whole collected week then reports as missing, and ``--from-snapshots``
    creates a phantom ``snapshots/2026-8-3/`` no canonical-date query can ever find.
    Normalizing at the boundary keeps one spelling everywhere below it.
    """
    return _parse_snapshot_date(snapshot_date).date().isoformat()


def _utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


def _artifact_is_readable(path: Path) -> bool:
    """Артефакт пригоден к финализации: существует и целиком разбирается как JSONL.

    Проверки `is_file()` было мало. День с обрезанным или испорченным артефактом
    вечно числился `recoverable_from_snapshots`, каждый прогон пытался его
    финализировать и каждый раз падал на той же строке — а оператор видел
    «восстановимо» и ждал, что recovery сработает. Пустой файл читаем и остаётся
    валидным: это честно пустой источник за день, а не поломка.
    """
    if not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    json.loads(stripped)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return True


def collection_coverage(
    snapshots_dir: Path,
    db_path: Path,
    *,
    profile: str = DEFAULT_PROFILE,
    since: str,
    until: str,
    sources: list[str] | None = None,
) -> list[dict[str, object]]:
    """Return an honest per-day view of raw-run and snapshot-artifact coverage.

    This is intentionally read-only.  A direct, reddit-only ``collect`` may have
    created a row called ``complete`` before the other adapters arrive, therefore
    the report also requires a healthy adapter-level row for every requested
    source.  Artifact presence is reported separately so operators can safely
    recover a missed finalizer without re-fetching or relabelling fresh data.
    """
    # Normalize before the SQL below compares these as text, not after.
    since = _canonical_snapshot_date(since)
    until = _canonical_snapshot_date(until)
    start = _parse_snapshot_date(since).date()
    end = _parse_snapshot_date(until).date()
    if end < start:
        raise ValueError("until must be on or after since")

    selected = [
        _ALIASES.get(source.strip(), source.strip()) for source in (sources or DEFAULT_SOURCES)
    ]
    if len(set(selected)) != len(selected):
        raise ValueError("sources must not contain duplicate aliases")

    runs_by_date: dict[str, str] = {}
    health_by_date: dict[str, dict[str, str]] = {}
    if db_path.exists():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            run_rows = conn.execute(
                """SELECT snapshot_date, run_id, status
                   FROM runs
                   WHERE profile = ? AND snapshot_date BETWEEN ? AND ?""",
                (profile, since, until),
            ).fetchall()
            for snapshot_date, run_id, status in run_rows:
                runs_by_date[str(snapshot_date)] = f"{run_id}\t{status}"
            health_rows = conn.execute(
                """SELECT r.snapshot_date, sh.source_id, sh.status
                   FROM source_health sh
                   JOIN runs r ON r.run_id = sh.run_id
                   WHERE r.profile = ? AND r.snapshot_date BETWEEN ? AND ?""",
                (profile, since, until),
            ).fetchall()
            for snapshot_date, source_id, status in health_rows:
                health_by_date.setdefault(str(snapshot_date), {})[str(source_id)] = str(status)
        except sqlite3.OperationalError:
            # A new corpus file has no intelligence schema yet.  It is simply
            # equivalent to no finalized raw runs, not an error in a coverage check.
            pass
        finally:
            conn.close()

    result: list[dict[str, object]] = []
    current = start
    healthy_statuses = {"ok", "empty"}
    while current <= end:
        snapshot_date = current.isoformat()
        run_value = runs_by_date.get(snapshot_date, "")
        run_id, _, run_status = run_value.partition("\t")
        source_health = health_by_date.get(snapshot_date, {})
        artifacts = {
            source_id: _artifact_is_readable(snapshots_dir / snapshot_date / _FILE_MAP[source_id])
            for source_id in selected
        }
        source_states = {
            source_id: source_health.get(source_id, "missing") for source_id in selected
        }
        raw_complete = run_status == "complete" and all(
            state in healthy_statuses for state in source_states.values()
        )
        artifacts_complete = all(artifacts.values())
        result.append(
            {
                "date": snapshot_date,
                "run_id": run_id or None,
                "run_status": run_status or "missing",
                "raw_complete": raw_complete,
                "source_health": source_states,
                "artifacts": artifacts,
                # Recovery is intentionally limited to artifacts that were observed
                # on the date itself.  It never asks live adapters for old data.
                "recoverable_from_snapshots": not raw_complete and artifacts_complete,
            }
        )
        current += timedelta(days=1)
    return result


def coverage_summary(coverage: list[dict[str, object]]) -> dict[str, object]:
    """Вердикт по календарному покрытию: что именно пропущено и чем это чинится.

    Оператор до сих пор читал по-дневный JSON глазами. Для алерта нужен ответ, а не
    таблица, причём ответ обязан предлагать **только безопасное** действие:

    ``recover_snapshots``  — артефакты за день сохранены, не отработал финалайзер.
                             Сети не требует и данные не выдумывает.
    ``historical_recovery`` — артефактов нет, но дата в прошлом: date-aware публичные
                             интерфейсы вернут материал именно за неё.
    ``pending``            — это сегодня, сбор ещё может пройти. Не gap.
    ``unresolved``         — день в прошлом, артефактов нет и часть источников не имеет
                             исторического интерфейса. Честно называем неразрешённым,
                             а не предлагаем подставить сегодняшнюю выборку.
    """
    today = _utc_today()
    gaps: list[dict[str, str]] = []
    complete = 0
    for day in coverage:
        date = str(day["date"])
        if bool(day["raw_complete"]):
            complete += 1
            continue
        if date >= today:
            action = "pending"
            reason = "текущий UTC-день, сбор ещё не завершён"
        elif bool(day["recoverable_from_snapshots"]):
            action = "recover_snapshots"
            reason = "артефакты сохранены, не отработал финалайзер"
        else:
            artifacts = cast(dict[str, bool], day.get("artifacts") or {})
            present = sum(1 for ok in artifacts.values() if ok)
            action = "historical_recovery"
            reason = f"артефакты неполны ({present} из {len(artifacts)})"
        gaps.append({"date": date, "reason": reason, "recommended_action": action})
    # `pending` — не пропуск: сегодняшний день ещё собирается, и поднимать по нему
    # тревогу значило бы будить оператора каждую ночь.
    actionable = [gap for gap in gaps if gap["recommended_action"] != "pending"]
    return {
        "days_total": len(coverage),
        "days_complete": complete,
        "gap_count": len(actionable),
        "gaps": gaps,
        "actions": sorted({gap["recommended_action"] for gap in actionable}),
    }


def recover_snapshot_gaps(
    config: MonitorConfig,
    snapshots_dir: Path,
    db_path: Path,
    *,
    profile: str = DEFAULT_PROFILE,
    since: str,
    until: str,
) -> tuple[list[dict[str, object]], list[CollectionResult]]:
    """Finalize every recoverable gap from its existing dated JSONL artifacts.

    No adapter or LLM is invoked.  A date without its complete artifact set stays
    visible in the returned coverage report; treating a later live fetch as that
    date would corrupt the corpus chronology.
    """
    coverage = collection_coverage(
        snapshots_dir,
        db_path,
        profile=profile,
        since=since,
        until=until,
    )
    recovered: list[CollectionResult] = []
    # Сегодняшний день исключаем так же, как это делает `collect_sources` для
    # исторической даты: если прямо сейчас идёт сбор, его артефакты дописываются, и
    # финализация прочитала бы половину дня как полный. `collect` за сегодня закроет
    # его сам, а до тех пор день честно остаётся незавершённым.
    today = _utc_today()
    for day in coverage:
        if not bool(day["recoverable_from_snapshots"]):
            continue
        if str(day["date"]) >= today:
            continue
        recovered.append(
            finalize_snapshot_collection(
                config=config,
                snapshots_dir=snapshots_dir,
                db_path=db_path,
                profile=profile,
                snapshot_date=str(day["date"]),
            )
        )
    return coverage, recovered


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def collect_sources(
    config: MonitorConfig,
    snapshots_dir: Path,
    db_path: Path,
    sources: list[str] | None = None,
    profile: str = DEFAULT_PROFILE,
    snapshot_date: str | None = None,
    historical_recovery: bool = False,
    overwrite_artifacts: bool = False,
) -> CollectionResult:
    """Collect and persist raw corpus facts without derived intelligence."""
    from .db import get_db

    today = datetime.now(UTC).date()
    date = _canonical_snapshot_date(snapshot_date) if snapshot_date else today.isoformat()
    if historical_recovery and date >= today.isoformat():
        raise ValueError("historical recovery date must be before current UTC date")
    if snapshot_date and not historical_recovery and date != today.isoformat():
        raise ValueError("only historical recovery may collect a non-current snapshot date")

    run_id = f"{date}:{profile}"
    started_at = now_iso()
    snap_dir = snapshots_dir / date
    snap_dir.mkdir(parents=True, exist_ok=True)
    conn = get_db(db_path)
    migrate(conn)
    upsert_run(
        conn,
        run_id=run_id,
        snapshot_date=date,
        profile=profile,
        status="running",
        started_at=started_at,
    )
    conn.commit()

    source_results: list[SourceResult] = []
    all_items: list[ContentItem] = []
    for requested_id in sources or DEFAULT_SOURCES:
        source_id = _ALIASES.get(requested_id, requested_id)
        result = await run_source_adapter(
            source_id,
            config,
            snap_dir,
            date,
            historical_date=date if historical_recovery else None,
            overwrite_artifacts=overwrite_artifacts,
        )
        if historical_recovery:
            result = replace(
                result,
                message=(
                    f"Historical recovery for {date}; observed after the original day. "
                    f"{result.message}"
                ).strip(),
            )
        source_results.append(result)
        if result.status not in {"ok", "empty"}:
            continue
        filename = _FILE_MAP.get(source_id, f"{source_id}.jsonl")
        items, _ = load_legacy_jsonl(
            snap_dir / filename,
            filename,
            now_iso(),
        )
        all_items.extend(items)

    finished_at = now_iso()
    _statuses = {r.status for r in source_results}
    if source_results and _statuses <= {"ok", "empty"}:
        status = "complete"
    elif "pending" in _statuses:
        status = "pending"
    else:
        status = "partial"
    persist_collection(
        conn,
        run_id=run_id,
        snapshot_date=date,
        profile=profile,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        items=all_items,
        source_results=source_results,
    )
    conn.close()
    return CollectionResult(
        run_id=run_id,
        date=date,
        profile=profile,
        status=status,
        source_results=source_results,
        items=all_items,
        started_at=started_at,
        finished_at=finished_at,
    )


def finalize_snapshot_collection(
    config: MonitorConfig,
    snapshots_dir: Path,
    db_path: Path,
    *,
    sources: list[str] | None = None,
    profile: str = DEFAULT_PROFILE,
    snapshot_date: str | None = None,
) -> CollectionResult:
    """Persist an already collected snapshot as one truthful collection run.

    Source adapters are intentionally *not* invoked here.  This is the hand-off
    used when Reddit has been fetched from the Mac and the remaining adapters
    have written their JSONL artifacts on the VPS.  A missing artifact is
    marked ``pending`` (not ``error``) so the run stays ``pending`` until the
    artifact arrives and finalization is re-run; stale or unreadable input
    is still an explicit ``error`` making the run ``partial``.
    """

    del config  # Kept in the public signature alongside ``collect_sources``.
    date = _canonical_snapshot_date(snapshot_date) if snapshot_date else _utc_today()
    run_id = f"{date}:{profile}"
    started_at = now_iso()
    snap_dir = snapshots_dir / date
    selected = [
        _ALIASES.get(source.strip(), source.strip()) for source in (sources or DEFAULT_SOURCES)
    ]
    # Полнота считалась по набору, который *попросили* финализировать, а не по тому,
    # что обязан покрыть профиль: сужение ``--sources`` давало run со статусом
    # ``complete`` и одним источником. Недостающие источники дописываются как
    # ``pending`` — тем же статусом, что и отсутствующий артефакт, — поэтому run
    # остаётся pending до появления остальных. Экспериментальные профили ничего
    # не обязаны: требование полноты касается только прод-каналов.
    unrequested = (
        [source_id for source_id in DEFAULT_SOURCES if source_id not in selected]
        if profile in PRODUCTION_PROFILES
        else []
    )

    source_results: list[SourceResult] = []
    all_items: list[ContentItem] = []
    for source_id in selected:
        filename = _FILE_MAP.get(source_id, f"{source_id}.jsonl")
        path = snap_dir / filename
        if not path.exists():
            source_results.append(
                SourceResult(
                    source_id=source_id,
                    status="pending",
                    error_code="snapshot_missing",
                    message=f"Waiting for snapshot artifact: {filename}",
                )
            )
            continue
        try:
            items, _ = load_legacy_jsonl(path, filename, started_at)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            source_results.append(
                SourceResult(
                    source_id=source_id,
                    status="error",
                    error_code="snapshot_invalid",
                    message=f"Cannot read {filename}: {exc.__class__.__name__}",
                )
            )
            continue
        all_items.extend(items)
        source_results.append(
            SourceResult(
                source_id=source_id,
                status="ok" if items else "empty",
                count=len(items),
                message=f"Finalized existing snapshot artifact: {filename}",
            )
        )

    source_results.extend(
        SourceResult(
            source_id=source_id,
            status="pending",
            error_code="snapshot_missing",
            message=f"Source not requested for finalization: {source_id}",
        )
        for source_id in unrequested
    )

    finished_at = now_iso()
    _statuses = {r.status for r in source_results}
    if source_results and _statuses <= {"ok", "empty"}:
        status = "complete"
    elif "pending" in _statuses:
        status = "pending"
    else:
        status = "partial"
    from .db import get_db

    conn = get_db(db_path)
    try:
        migrate(conn)
        persist_collection(
            conn,
            run_id=run_id,
            snapshot_date=date,
            profile=profile,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            items=all_items,
            source_results=source_results,
        )
    finally:
        conn.close()
    return CollectionResult(
        run_id=run_id,
        date=date,
        profile=profile,
        status=status,
        source_results=source_results,
        items=all_items,
        started_at=started_at,
        finished_at=finished_at,
    )


def persist_collection(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    snapshot_date: str,
    profile: str,
    status: str,
    started_at: str,
    finished_at: str,
    items: list[ContentItem],
    source_results: list[SourceResult],
) -> None:
    """Persist only runs/items/observations/source health."""
    if items:
        upsert_items(conn, items)
        upsert_observations(
            conn,
            [
                Observation(
                    run_id=run_id,
                    item_id=item.item_id,
                    observed_at=finished_at,
                )
                for item in items
            ],
        )
    _upsert_source_health(conn, run_id, _build_source_health(source_results, items))
    upsert_run(
        conn,
        run_id=run_id,
        snapshot_date=snapshot_date,
        profile=profile,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
    )
    conn.commit()


def _upsert_source_health(
    conn: sqlite3.Connection,
    run_id: str,
    health_rows: list[SourceHealth],
) -> None:
    for health in health_rows:
        conn.execute(
            """INSERT INTO source_health
               (run_id, source_id, provider, cluster, status, count,
                duration_sec, error_code, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, source_id) DO UPDATE SET
                 provider = excluded.provider,
                 cluster = excluded.cluster,
                 status = excluded.status,
                 count = excluded.count,
                 duration_sec = excluded.duration_sec,
                 error_code = excluded.error_code,
                 message = excluded.message""",
            (
                run_id,
                health.source_id,
                health.provider,
                health.cluster,
                health.status,
                health.count,
                health.duration_sec,
                health.error_code,
                health.message,
            ),
        )


def _build_source_health(
    source_results: list[SourceResult],
    items: list[ContentItem],
) -> list[SourceHealth]:
    from .sources.registry import SOURCES

    health: list[SourceHealth] = []
    by_provider_section: dict[tuple[str, str], list[ContentItem]] = {}
    for item in items:
        section = item.source_section or item.provider
        by_provider_section.setdefault((item.provider, section), []).append(item)
    for (provider, section), section_items in sorted(by_provider_section.items()):
        health.append(
            SourceHealth(
                source_id=f"{provider}:{section}",
                provider=provider,
                cluster=section_items[0].source_cluster,
                status="ok",
                count=len(section_items),
                message=section,
            )
        )
    allowed_statuses = {"ok", "empty", "error", "not_configured", "skipped"}
    for result in source_results:
        source_definition = SOURCES.get(result.source_id)
        status = cast(
            SourceStatus,
            result.status if result.status in allowed_statuses else "partial",
        )
        health.append(
            SourceHealth(
                source_id=result.source_id,
                provider=(
                    source_definition.provider
                    if source_definition is not None
                    else result.source_id
                ),
                cluster=(source_definition.cluster if source_definition is not None else "voices"),
                status=status,
                count=result.count,
                duration_sec=result.duration_sec,
                error_code=result.error_code,
                message=result.message,
            )
        )
    return health


async def run_source_adapter(
    source_id: str,
    config: MonitorConfig,
    snap_dir: Path,
    snapshot_date: str,
    *,
    historical_date: str | None = None,
    overwrite_artifacts: bool = False,
) -> SourceResult:
    """Run one read-only source adapter."""
    source_id = _ALIASES.get(source_id, source_id)
    started = time.monotonic()
    artifact = snap_dir / _FILE_MAP[source_id]
    # Историческое восстановление идёт по датам, за которые артефакт часто уже лежит —
    # его просто не финализировали.  Запись поверх уничтожала настоящий дневной сбор и
    # подменяла его тем немногим, что отдаёт ретроспективный запрос: 530 items рассыпались
    # в 12.  Порядок шагов рунбука (сначала --recover-snapshots) теперь держит код, а не
    # только текст.
    has_artifact = artifact.is_file() and artifact.stat().st_size > 0
    if historical_date and not overwrite_artifacts and has_artifact:
        return SourceResult(
            source_id=source_id,
            status="skipped",
            message=(
                f"{artifact.name} already exists for {historical_date}: finalize it with "
                "--recover-snapshots, or pass --overwrite-artifacts to discard it"
            ),
        )
    try:
        cards = await _fetch_source_cards(
            source_id,
            config,
            snapshot_date,
            historical_date=historical_date,
        )
        message = (
            "Historical Ladder recovery through date-filtered Google News discovery"
            if source_id == "ladder" and historical_date
            else ""
        )
        if cards is None:
            return SourceResult(
                source_id=source_id,
                status="skipped",
                message=f"Unknown source: {source_id}",
            )
        from .export import write_posts_jsonl

        write_posts_jsonl(cards, artifact)
        return SourceResult(
            source_id=source_id,
            # ``empty`` здесь означает именно пустой день: адаптер, у которого не удался
            # ни один запрос, поднимает ``SourceTransportError`` и уходит в ``error``
            # ниже. Раньше он возвращал ``[]``, отказ становился ``empty``, run — уже
            # ``complete``, и провалившийся день навсегда исчезал из ``--coverage``.
            status="ok" if cards else "empty",
            count=len(cards),
            duration_sec=round(time.monotonic() - started, 1),
            message=message,
        )
    except Exception as exc:
        logger.exception("Source %s failed", source_id)
        return SourceResult(
            source_id=source_id,
            status="error",
            duration_sec=round(time.monotonic() - started, 1),
            error_code=type(exc).__name__,
            message=str(exc)[:200],
        )


async def _fetch_source_cards(
    source_id: str,
    config: MonitorConfig,
    snapshot_date: str,
    *,
    historical_date: str | None = None,
) -> list[PostCard] | None:
    if source_id == "reddit":
        from .fetch_subreddits import fetch_all_subreddits

        return list(
            await fetch_all_subreddits(
                config,
                snapshot_date,
                historical_date=historical_date,
            )
        )
    if source_id == "hackernews":
        from .sources.hackernews import fetch_hn_stories

        return list(
            await fetch_hn_stories(
                snapshot_date=snapshot_date,
                historical_date=historical_date,
            )
        )
    if source_id == "rss":
        from .sources.rss import fetch_all_rss

        return list(
            await fetch_all_rss(
                snapshot_date=snapshot_date,
                historical_date=historical_date,
            )
        )
    if source_id == "ladder":
        from .sources.ladder import fetch_all_ladder

        return list(
            await fetch_all_ladder(
                snapshot_date=snapshot_date,
                historical_date=historical_date,
            )
        )
    if source_id == "producthunt":
        from .sources.producthunt import fetch_producthunt

        return list(
            await fetch_producthunt(
                snapshot_date=snapshot_date,
                historical_date=historical_date,
            )
        )
    return None
