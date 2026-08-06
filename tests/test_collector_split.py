"""Collector writes corpus facts only."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from reddit_compass.collector import (
    SourceResult,
    collect_sources,
    collection_coverage,
    finalize_snapshot_collection,
    persist_collection,
    recover_snapshot_gaps,
)
from reddit_compass.config import MonitorConfig
from reddit_compass.export import write_posts_jsonl
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import ContentItem
from reddit_compass.models import PostCard


def test_persist_collection_does_not_write_derived_tables(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "compass.db")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    item = ContentItem(
        item_id="raw_1",
        provider="reuters",
        source_cluster="business",
        external_id="raw_1",
        canonical_url="https://example.com/raw-1",
        title="Raw collection item",
        observed_at="2026-07-29T07:00:00Z",
        snapshot_date="2026-07-29",
    )

    persist_collection(
        conn,
        run_id="2026-07-29:broad",
        snapshot_date="2026-07-29",
        profile="broad",
        status="complete",
        started_at="2026-07-29T07:00:00Z",
        finished_at="2026-07-29T07:10:00Z",
        items=[item],
        source_results=[SourceResult(source_id="rss", status="ok", count=1)],
    )

    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM source_health").fetchone()[0] >= 1
    assert conn.execute("SELECT COUNT(*) FROM item_signals").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM story_metrics").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == 0


def test_partial_collection_updates_one_source_without_erasing_other_health(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(tmp_path / "compass.db")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    common = {
        "conn": conn,
        "run_id": "2026-07-29:broad",
        "snapshot_date": "2026-07-29",
        "profile": "broad",
        "started_at": "2026-07-29T07:00:00Z",
        "finished_at": "2026-07-29T07:10:00Z",
        "items": [],
    }
    persist_collection(
        **common,
        status="partial",
        source_results=[SourceResult(source_id="rss", status="ok", count=10)],
    )
    persist_collection(
        **common,
        status="complete",
        source_results=[SourceResult(source_id="hackernews", status="ok", count=20)],
    )

    source_ids = {
        row[0]
        for row in conn.execute(
            "SELECT source_id FROM source_health WHERE run_id = '2026-07-29:broad'"
        ).fetchall()
    }
    assert {"rss", "hackernews"} <= source_ids


def test_finalize_snapshot_collection_creates_one_complete_raw_run(tmp_path: Path) -> None:
    date = "2026-07-30"
    snapshot_dir = tmp_path / "snapshots" / date
    for filename in ("posts.jsonl", "hackernews.jsonl", "rss.jsonl", "ladder.jsonl"):
        write_posts_jsonl([_legacy_card(f"{filename}-post", date)], snapshot_dir / filename)
    write_posts_jsonl([_legacy_card("ph-post", date)], snapshot_dir / "producthunt.jsonl")

    result = finalize_snapshot_collection(
        config=MonitorConfig(),
        snapshots_dir=tmp_path / "snapshots",
        db_path=tmp_path / "compass.db",
        profile="broad",
        snapshot_date=date,
    )

    conn = sqlite3.connect(tmp_path / "compass.db")
    assert result.status == "complete"
    assert result.run_id == f"{date}:broad"
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "complete"
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 5
    assert {row[0] for row in conn.execute("SELECT source_id FROM source_health").fetchall()} >= {
        "reddit",
        "rss",
    }


def test_finalize_snapshot_collection_narrowed_sources_cannot_be_complete(tmp_path: Path) -> None:
    """Сужение ``--sources`` не даёт прод-профилю объявить себя полным.

    Ровно так 2026-08-01 получил ``complete`` с одним reddit из одиннадцати провайдеров:
    полнота считалась по списку, который попросили финализировать.
    """
    date = "2026-07-30"
    snapshot_dir = tmp_path / "snapshots" / date
    write_posts_jsonl([_legacy_card("reddit-post", date)], snapshot_dir / "posts.jsonl")

    result = finalize_snapshot_collection(
        config=MonitorConfig(),
        snapshots_dir=tmp_path / "snapshots",
        db_path=tmp_path / "compass.db",
        sources=["reddit"],
        profile="broad",
        snapshot_date=date,
    )

    assert result.status == "pending"
    missing = {source.source_id for source in result.source_results if source.status == "pending"}
    assert missing == {"hackernews", "rss", "ladder", "producthunt"}


def test_finalize_snapshot_collection_narrowed_sources_ok_off_production(tmp_path: Path) -> None:
    """Экспериментальный профиль полноты не обязан — прежнее поведение сохраняется."""
    date = "2026-07-30"
    snapshot_dir = tmp_path / "snapshots" / date
    write_posts_jsonl([_legacy_card("reddit-post", date)], snapshot_dir / "posts.jsonl")

    result = finalize_snapshot_collection(
        config=MonitorConfig(),
        snapshots_dir=tmp_path / "snapshots",
        db_path=tmp_path / "compass.db",
        sources=["reddit"],
        profile="starter",
        snapshot_date=date,
    )

    assert result.status == "complete"


def test_finalize_snapshot_collection_marks_missing_artifact_pending(tmp_path: Path) -> None:
    date = "2026-07-30"
    snapshot_dir = tmp_path / "snapshots" / date
    write_posts_jsonl([_legacy_card("reddit-post", date)], snapshot_dir / "posts.jsonl")

    result = finalize_snapshot_collection(
        config=MonitorConfig(),
        snapshots_dir=tmp_path / "snapshots",
        db_path=tmp_path / "compass.db",
        sources=["reddit", "hn"],
        profile="broad",
        snapshot_date=date,
    )

    by_source = {source.source_id: source for source in result.source_results}
    assert result.status == "pending"
    assert by_source["reddit"].error_code is None
    assert by_source["hackernews"].error_code == "snapshot_missing"


def test_collection_coverage_detects_recoverable_snapshot_gap(tmp_path: Path) -> None:
    date = "2026-08-04"
    snapshot_dir = tmp_path / "snapshots" / date
    for filename in (
        "posts.jsonl",
        "hackernews.jsonl",
        "rss.jsonl",
        "ladder.jsonl",
        "producthunt.jsonl",
    ):
        write_posts_jsonl([_legacy_card(f"{filename}-post", date)], snapshot_dir / filename)
    db_path = tmp_path / "compass.db"
    conn = sqlite3.connect(db_path)
    migrate(conn)
    conn.close()

    coverage = collection_coverage(
        tmp_path / "snapshots",
        db_path,
        profile="broad",
        since=date,
        until=date,
    )

    assert coverage == [
        {
            "date": date,
            "run_id": None,
            "run_status": "missing",
            "raw_complete": False,
            "source_health": {
                "reddit": "missing",
                "hackernews": "missing",
                "rss": "missing",
                "ladder": "missing",
                "producthunt": "missing",
            },
            "artifacts": {
                "reddit": True,
                "hackernews": True,
                "rss": True,
                "ladder": True,
                "producthunt": True,
            },
            "recoverable_from_snapshots": True,
        }
    ]


def test_recover_snapshot_gaps_finalizes_only_saved_complete_artifacts(tmp_path: Path) -> None:
    date = "2026-08-04"
    snapshot_dir = tmp_path / "snapshots" / date
    for filename in (
        "posts.jsonl",
        "hackernews.jsonl",
        "rss.jsonl",
        "ladder.jsonl",
        "producthunt.jsonl",
    ):
        write_posts_jsonl([_legacy_card(f"{filename}-post", date)], snapshot_dir / filename)

    coverage, recovered = recover_snapshot_gaps(
        MonitorConfig(),
        tmp_path / "snapshots",
        tmp_path / "compass.db",
        profile="broad",
        since=date,
        until=date,
    )

    assert coverage[0]["recoverable_from_snapshots"] is True
    assert [(result.run_id, result.status) for result in recovered] == [
        (f"{date}:broad", "complete")
    ]

    conn = sqlite3.connect(tmp_path / "compass.db")
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "complete"


def test_historical_collection_passes_target_date_to_each_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    date = "2026-08-04"
    calls: list[tuple[str, str | None]] = []

    async def fake_run_source_adapter(
        source_id: str,
        config: MonitorConfig,
        snap_dir: Path,
        snapshot_date: str,
        *,
        historical_date: str | None = None,
        overwrite_artifacts: bool = False,
    ) -> SourceResult:
        del config, snap_dir, snapshot_date, overwrite_artifacts
        calls.append((source_id, historical_date))
        return SourceResult(source_id=source_id, status="empty")

    monkeypatch.setattr("reddit_compass.collector.run_source_adapter", fake_run_source_adapter)

    result = asyncio.run(
        collect_sources(
            MonitorConfig(),
            tmp_path / "snapshots",
            tmp_path / "compass.db",
            sources=["reddit", "hn"],
            profile="starter",
            snapshot_date=date,
            historical_recovery=True,
        )
    )

    assert calls == [("reddit", date), ("hackernews", date)]
    assert result.run_id == f"{date}:starter"
    assert result.status == "complete"
    assert all("Historical recovery for 2026-08-04" in row.message for row in result.source_results)


def _legacy_card(post_id: str, date: str) -> PostCard:
    return PostCard(
        subreddit="technology",
        post_id=post_id,
        title=f"Title for {post_id}",
        author="author",
        created_utc=f"{date}T07:00:00Z",
        score=12,
        upvote_ratio=0.9,
        num_comments=4,
        url=f"https://example.com/{post_id}",
        selftext="Excerpt",
        link_flair_text=None,
        is_self=False,
        permalink=f"/r/technology/comments/{post_id}",
        monitoring_type="hot",
        snapshot_date=date,
    )


def test_total_adapter_failure_is_an_error_not_an_empty_day(tmp_path: Path, monkeypatch) -> None:
    """Провал всех запросов адаптера обязан дать `partial`, а не `complete`.

    Адаптеры ловили HTTP- и сетевые ошибки внутри себя и возвращали `[]`. Дальше
    `status="ok" if cards else "empty"` превращало отказ в пустой день, набор
    `{"ok", "empty"}` давал run `complete`, а `collection_coverage` считала такой день
    собранным. Ночь, где Algolia отдаёт 429, а фиды 503, записывалась полным днём с
    одним Reddit и навсегда исчезала из `--coverage`.
    """
    from reddit_compass.sources.errors import SourceTransportError

    async def fake_fetch(source_id, config, snapshot_date, *, historical_date=None):
        del config, snapshot_date, historical_date
        if source_id == "reddit":
            return [_legacy_card("r1", "2026-08-04")]
        raise SourceTransportError(source_id, 3, ["HTTP 429", "HTTP 503", "HTTP 503"])

    monkeypatch.setattr("reddit_compass.collector._fetch_source_cards", fake_fetch)

    result = asyncio.run(
        collect_sources(
            MonitorConfig(),
            tmp_path / "snapshots",
            tmp_path / "compass.db",
            sources=["reddit", "hn"],
            profile="starter",
        )
    )

    assert result.status == "partial"
    failed = [row for row in result.source_results if row.source_id == "hackernews"]
    assert failed[0].status == "error"
    assert failed[0].error_code == "SourceTransportError"
    # Отказ не оставляет за собой артефакт, который выглядел бы как пустой день.
    assert not (tmp_path / "snapshots" / result.date / "hackernews.jsonl").exists()


def test_a_genuinely_empty_day_still_counts_as_collected(tmp_path: Path, monkeypatch) -> None:
    """Пустой день — нормальное явление и обязан остаться `complete`.

    Различать надо не «нет карточек», а «ни один запрос не удался»: иначе тихие сутки
    Product Hunt начали бы блокировать релиз наравне с реальным отказом.
    """

    async def fake_fetch(source_id, config, snapshot_date, *, historical_date=None):
        del config, snapshot_date, historical_date
        return [_legacy_card("r1", "2026-08-04")] if source_id == "reddit" else []

    monkeypatch.setattr("reddit_compass.collector._fetch_source_cards", fake_fetch)

    result = asyncio.run(
        collect_sources(
            MonitorConfig(),
            tmp_path / "snapshots",
            tmp_path / "compass.db",
            sources=["reddit", "hn"],
            profile="starter",
        )
    )

    assert result.status == "complete"
    assert [row.status for row in result.source_results] == ["ok", "empty"]


def test_coverage_normalizes_a_non_padded_date_range(tmp_path: Path) -> None:
    """`2026-8-3` обязан дать тот же ответ, что и канонический `2026-08-03`.

    `strptime` принимает неканоническую запись, но даты хранятся с ведущими нулями, а
    coverage сравнивает их как текст: `'2026-08-04' >= '2026-8-3'` ложно, и BETWEEN не
    матчил ничего — полностью собранная неделя показывалась как missing.
    """
    db_path = tmp_path / "compass.db"
    snapshots = tmp_path / "snapshots"
    conn = sqlite3.connect(db_path)
    migrate(conn)
    conn.close()

    canonical = collection_coverage(
        snapshots, db_path, profile="starter", since="2026-08-03", until="2026-08-05"
    )
    loose = collection_coverage(
        snapshots, db_path, profile="starter", since="2026-8-3", until="2026-8-5"
    )

    assert [day["date"] for day in loose] == ["2026-08-03", "2026-08-04", "2026-08-05"]
    assert loose == canonical


def test_recover_snapshot_gaps_leaves_today_alone(tmp_path: Path) -> None:
    """Финализация сегодняшнего дня прочитала бы идущий сбор как завершённый.

    `collect_sources` для исторической даты требует, чтобы она была строго раньше
    текущей UTC; у recovery такой защиты не было. Запуск во время идущего сбора
    финализировал наполовину дописанные артефакты, а живой прогон затем переписывал
    статус — транзиентно неверный `complete` на дне, который ещё собирается.
    """
    from datetime import UTC, datetime

    today = datetime.now(UTC).date().isoformat()
    snapshot_dir = tmp_path / "snapshots" / today
    for filename in (
        "posts.jsonl",
        "hackernews.jsonl",
        "rss.jsonl",
        "ladder.jsonl",
        "producthunt.jsonl",
    ):
        write_posts_jsonl([_legacy_card(f"{filename}-post", today)], snapshot_dir / filename)
    db_path = tmp_path / "compass.db"
    conn = sqlite3.connect(db_path)
    migrate(conn)
    conn.close()

    coverage, recovered = recover_snapshot_gaps(
        MonitorConfig(),
        tmp_path / "snapshots",
        db_path,
        profile="broad",
        since=today,
        until=today,
    )

    # День виден как восстановимый, но recovery его не трогает — его закроет `collect`.
    assert coverage[0]["recoverable_from_snapshots"] is True
    assert recovered == []


def test_corrupt_artifact_is_not_reported_as_recoverable(tmp_path: Path) -> None:
    """Битый JSONL не может вечно числиться восстановимым.

    Проверка была только `is_file()`, поэтому день с обрезанным артефактом навсегда
    оставался `recoverable_from_snapshots: true`: каждый прогон пытался его
    финализировать и падал на той же строке, а оператор видел «восстановимо» и ждал.
    """
    date = "2026-08-04"
    snapshot_dir = tmp_path / "snapshots" / date
    for filename in ("posts.jsonl", "hackernews.jsonl", "ladder.jsonl", "producthunt.jsonl"):
        write_posts_jsonl([_legacy_card(f"{filename}-post", date)], snapshot_dir / filename)
    # Обрыв на середине строки — ровно то, что оставляла незавершённая запись.
    (snapshot_dir / "rss.jsonl").write_text('{"post_id": "half-writ', encoding="utf-8")
    db_path = tmp_path / "compass.db"
    conn = sqlite3.connect(db_path)
    migrate(conn)
    conn.close()

    coverage = collection_coverage(
        tmp_path / "snapshots", db_path, profile="broad", since=date, until=date
    )

    assert coverage[0]["artifacts"]["rss"] is False
    assert coverage[0]["recoverable_from_snapshots"] is False


def test_empty_artifact_stays_recoverable(tmp_path: Path) -> None:
    """Пустой файл читаем и валиден: это честно пустой источник, а не поломка."""
    date = "2026-08-04"
    snapshot_dir = tmp_path / "snapshots" / date
    snapshot_dir.mkdir(parents=True)
    for filename in ("posts.jsonl", "hackernews.jsonl", "ladder.jsonl", "producthunt.jsonl"):
        write_posts_jsonl([_legacy_card(f"{filename}-post", date)], snapshot_dir / filename)
    (snapshot_dir / "rss.jsonl").write_text("", encoding="utf-8")
    db_path = tmp_path / "compass.db"
    conn = sqlite3.connect(db_path)
    migrate(conn)
    conn.close()

    coverage = collection_coverage(
        tmp_path / "snapshots", db_path, profile="broad", since=date, until=date
    )

    assert coverage[0]["artifacts"]["rss"] is True
    assert coverage[0]["recoverable_from_snapshots"] is True


def test_recovery_moves_snapshot_date_backwards_only(tmp_path: Path) -> None:
    """Восстановленный день обязан переписать дату материала назад, но не вперёд.

    `ON CONFLICT` не обновлял `snapshot_date` вовсе, поэтому материал, впервые
    вставленный сегодняшним прогоном, после recovery за вчера оставался помеченным
    сегодняшним днём — то есть выпадал из окна релиза за собственный день.
    """
    from reddit_compass.intelligence.models import ContentItem
    from reddit_compass.intelligence.repository import upsert_items

    conn = sqlite3.connect(tmp_path / "compass.db")
    conn.row_factory = sqlite3.Row
    migrate(conn)

    def item(snapshot_date: str, published_at: str) -> ContentItem:
        return ContentItem(
            item_id="rss:1",
            provider="reuters",
            source_cluster="business",
            external_id="1",
            canonical_url="https://example.com/a",
            title="A story",
            observed_at=f"{snapshot_date}T07:00:00Z",
            snapshot_date=snapshot_date,
            published_at=published_at,
        )

    upsert_items(conn, [item("2026-08-05", "")])
    upsert_items(conn, [item("2026-08-04", "2026-08-04T06:00:00Z")])
    row = conn.execute("SELECT snapshot_date, published_at, observed_at FROM items").fetchone()
    assert row["snapshot_date"] == "2026-08-04"
    # Пустая дата публикации дозаполняется восстановлением.
    assert row["published_at"] == "2026-08-04T06:00:00Z"
    # Момент первого наблюдения — провенанс, его переписывать нельзя.
    assert row["observed_at"] == "2026-08-05T07:00:00Z"

    # Живой прогон не двигает дату вперёд у материала, уже отнесённого к прошлому дню.
    upsert_items(conn, [item("2026-08-06", "2026-08-06T06:00:00Z")])
    row = conn.execute("SELECT snapshot_date, published_at FROM items").fetchone()
    assert row["snapshot_date"] == "2026-08-04"
    assert row["published_at"] == "2026-08-04T06:00:00Z"


def test_coverage_summary_names_only_safe_actions(tmp_path: Path) -> None:
    """Алерт обязан предлагать безопасное действие, а не «собери сегодняшнее за вчера».

    Оператор читал по-дневный JSON глазами. Для cron нужен вердикт, и он не имеет права
    предложить живой сбор за прошлую дату — только финализацию сохранённых артефактов
    либо date-aware историческое восстановление.
    """
    from datetime import UTC, datetime

    from reddit_compass.collector import coverage_summary

    today = datetime.now(UTC).date().isoformat()
    summary = coverage_summary(
        [
            {
                "date": "2026-08-01",
                "raw_complete": True,
                "recoverable_from_snapshots": False,
                "artifacts": {"rss": True},
            },
            {
                "date": "2026-08-02",
                "raw_complete": False,
                "recoverable_from_snapshots": True,
                "artifacts": {"rss": True},
            },
            {
                "date": "2026-08-03",
                "raw_complete": False,
                "recoverable_from_snapshots": False,
                "artifacts": {"rss": False},
            },
            {
                "date": today,
                "raw_complete": False,
                "recoverable_from_snapshots": False,
                "artifacts": {"rss": False},
            },
        ]
    )

    assert summary["days_complete"] == 1
    actions = {gap["date"]: gap["recommended_action"] for gap in summary["gaps"]}
    assert actions["2026-08-02"] == "recover_snapshots"
    assert actions["2026-08-03"] == "historical_recovery"
    # Сегодняшний день ещё собирается — тревожить по нему нельзя.
    assert actions[today] == "pending"
    assert summary["gap_count"] == 2
    assert "pending" not in summary["actions"]


def test_live_collection_with_narrowed_sources_cannot_be_complete(
    tmp_path: Path, monkeypatch
) -> None:
    """Сужение `--sources` не даёт прод-профилю объявить себя полным и при живом сборе.

    Guard стоял только в `finalize_snapshot_collection`. Живой прогон 2026-08-06
    (`collect --sources hn,rss,ladder,ph --profile broad`) записал production-run как
    `complete` вообще без строки Reddit — зеркало того дефекта, из-за которого
    2026-08-01 получил `complete` с одним Reddit из одиннадцати провайдеров.
    """

    async def fake_fetch(source_id, config, snapshot_date, *, historical_date=None):
        del config, snapshot_date, historical_date
        return [_legacy_card(f"{source_id}-1", "2026-08-06")]

    monkeypatch.setattr("reddit_compass.collector._fetch_source_cards", fake_fetch)

    result = asyncio.run(
        collect_sources(
            MonitorConfig(),
            tmp_path / "snapshots",
            tmp_path / "compass.db",
            sources=["hn", "rss", "ladder", "ph"],
            profile="broad",
        )
    )

    assert result.status == "pending"
    missing = {row.source_id for row in result.source_results if row.status == "pending"}
    assert missing == {"reddit"}


def test_live_collection_narrowed_sources_ok_off_production(tmp_path: Path, monkeypatch) -> None:
    """Экспериментальный профиль полноты не обязан — требование касается прод-каналов."""

    async def fake_fetch(source_id, config, snapshot_date, *, historical_date=None):
        del config, snapshot_date, historical_date
        return [_legacy_card(f"{source_id}-1", "2026-08-06")]

    monkeypatch.setattr("reddit_compass.collector._fetch_source_cards", fake_fetch)

    result = asyncio.run(
        collect_sources(
            MonitorConfig(),
            tmp_path / "snapshots",
            tmp_path / "compass.db",
            sources=["hn", "rss"],
            profile="starter",
        )
    )

    assert result.status == "complete"
