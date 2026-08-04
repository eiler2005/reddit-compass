"""CLI reddit-compass.

Usage:
    python scripts/run.py all
    python scripts/run.py fetch --limit 10
    python scripts/run.py search
    python scripts/run.py track
    python scripts/run.py virality
    python scripts/run.py report
    python scripts/run.py nightly
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import DEFAULT_HARVESTS_DIR, DEFAULT_PROFILE, DEFAULT_SNAPSHOTS_DIR, MonitorConfig
from .detect_virality import detect_virality
from .export import render_trends_report, write_snapshot, write_trends_report
from .fetch_subreddits import fetch_all_subreddits
from .intelligence.actor_types import DEFAULT_ACTOR_MODEL, DEFAULT_ACTOR_THRESHOLD
from .intelligence.cross_encoder import DEFAULT_CROSS_ENCODER_THRESHOLD
from .intelligence.embeddings import LEXICAL_HASH_EMBEDDING_MODEL
from .intelligence.engine import DEFAULT_TREND_METHOD
from .intelligence.trend_schema_llm import (
    DEFAULT_EXTRACT_MODEL,
    EXTRACT_BATCH,
    EXTRACT_CONCURRENCY,
)
from .models import PostCard, TrackedThreadState, ViralitySignal
from .search_keywords import search_all_keywords
from .track_threads import track_all_threads

logger = logging.getLogger("reddit_compass")


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_config(args: argparse.Namespace) -> MonitorConfig:
    if args.config:
        config = MonitorConfig.from_file(Path(args.config))
    else:
        config = MonitorConfig.from_profile(getattr(args, "profile", DEFAULT_PROFILE))
    if args.limit:
        config.settings.posts_per_subreddit = args.limit
    if args.time_filter:
        config.settings.time_filter = args.time_filter
    if getattr(args, "stealth", False):
        config.settings.stealth = True
    return config


def _snapshots_dir(args: argparse.Namespace) -> Path:
    return Path(args.output_dir) if args.output_dir else DEFAULT_SNAPSHOTS_DIR


# ── Subcommand handlers ────────────────────────────────────────────────────


def _dry_run_report(config: MonitorConfig, args: argparse.Namespace) -> bool:
    """Если --dry-run: печатает план сбора и возвращает True (early exit)."""
    if not getattr(args, "dry_run", False):
        return False
    settings = config.settings
    n_sub = len(config.all_subreddits)
    n_kw = len(config.keywords)
    n_threads = len(config.tracked_threads)
    # Оценка: 2 listing на сабреддит + comments_top_n на сабреддит + keywords + threads
    est_requests = n_sub * 2 + n_sub * settings.comments_for_top_n + n_kw + n_threads
    est_time_min = est_requests * (5 if settings.stealth else 4) / 60

    print("🔍 DRY RUN — сетевые запросы НЕ выполняются, данные НЕ пишутся")
    print(f"{'=' * 60}")
    print(f"Сабреддиты ({n_sub}):")
    for cluster, subs in config.subreddit_clusters.items():
        print(f"  [{cluster}] {', '.join(subs)}")
    print(f"Ключевые слова ({n_kw}): {', '.join(config.keywords[:5])}{'...' if n_kw > 5 else ''}")
    print(f"Tracked threads: {n_threads}")
    print(
        f"Настройки: {settings.posts_per_subreddit} постов/саб, "
        f"comments для top-{settings.comments_for_top_n}, "
        f"time_filter={settings.time_filter}"
    )
    print(f"Stealth: {'да' if settings.stealth else 'нет'}")
    print(f"{'=' * 60}")
    print(f"Оценка: ~{est_requests} запросов, ~{est_time_min:.0f} мин")
    return True


async def _cmd_fetch(args: argparse.Namespace) -> None:
    config = _load_config(args)
    if _dry_run_report(config, args):
        return
    snapshot_date = _today()
    cards = await fetch_all_subreddits(config, snapshot_date)
    snap_dir = _snapshots_dir(args) / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    from .export import write_posts_jsonl

    write_posts_jsonl(cards, snap_dir / "posts.jsonl")
    print(f"✅ Fetch: {len(cards)} постов → {snap_dir / 'posts.jsonl'}")


async def _cmd_search(args: argparse.Namespace) -> None:
    config = _load_config(args)
    if _dry_run_report(config, args):
        return
    snapshot_date = _today()
    cards = await search_all_keywords(config, snapshot_date)
    snap_dir = _snapshots_dir(args) / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    from .export import write_posts_jsonl

    write_posts_jsonl(cards, snap_dir / "keyword-search.jsonl")
    print(f"✅ Search: {len(cards)} постов → {snap_dir / 'keyword-search.jsonl'}")


async def _cmd_track(args: argparse.Namespace) -> None:
    config = _load_config(args)
    snapshot_date = _today()
    state_file = _snapshots_dir(args).parent / "tracked-threads-state.jsonl"
    states = await track_all_threads(config, snapshot_date, state_file)
    snap_dir = _snapshots_dir(args) / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    from .export import write_threads_jsonl

    write_threads_jsonl(states, snap_dir / "tracked-threads.jsonl")
    write_threads_jsonl(states, state_file)
    print(f"✅ Track: {len(states)} тредов → {snap_dir / 'tracked-threads.jsonl'}")


async def _cmd_virality(args: argparse.Namespace) -> None:
    config = _load_config(args)
    snapshot_date = _today()
    cards = await fetch_all_subreddits(config, snapshot_date)
    search_cards = await search_all_keywords(config, snapshot_date)
    signals = detect_virality(cards + search_cards, config, snapshot_date)
    snap_dir = _snapshots_dir(args) / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    from .export import write_virality_jsonl

    write_virality_jsonl(signals, snap_dir / "virality.jsonl")
    print(f"✅ Virality: {len(signals)} сигналов → {snap_dir / 'virality.jsonl'}")


async def _cmd_report(args: argparse.Namespace) -> None:
    import json

    output_dir = _snapshots_dir(args)
    snapshot_date = args.date or _today()
    snap_dir = output_dir / snapshot_date
    if not snap_dir.exists():
        print(f"❌ Snapshot не найден: {snap_dir}")
        sys.exit(1)

    cards: list[PostCard] = []
    posts_file = snap_dir / "posts.jsonl"
    if posts_file.exists():
        for line in posts_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cards.append(PostCard.from_dict(json.loads(line)))

    thread_states: list[TrackedThreadState] = []
    threads_file = snap_dir / "tracked-threads.jsonl"
    if threads_file.exists():
        for line in threads_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                thread_states.append(TrackedThreadState(**json.loads(line)))

    virality_signals: list[ViralitySignal] = []
    virality_file = snap_dir / "virality.jsonl"
    if virality_file.exists():
        for line in virality_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                virality_signals.append(ViralitySignal(**json.loads(line)))

    config = _load_config(args)
    report = render_trends_report(
        cards, thread_states, virality_signals, snapshot_date, config.subreddit_clusters
    )
    report_path = snap_dir / "trends-report.md"
    write_trends_report(report, report_path)
    print(f"✅ Report: {report_path}")


async def _cmd_all(args: argparse.Namespace) -> None:
    config = _load_config(args)
    if _dry_run_report(config, args):
        return
    snapshot_date = _today()
    output_dir = _snapshots_dir(args)
    state_file = output_dir.parent / "tracked-threads-state.jsonl"

    from .manifest import SourceResult, new_manifest, save_manifest

    manifest = new_manifest()
    snap_dir_path = output_dir / snapshot_date

    logger.info("=== Fetch: hot/top по сабреддитам ===")
    t0 = time.time()
    cards = await fetch_all_subreddits(config, snapshot_date)
    manifest.add_source(
        SourceResult(
            name="reddit-fetch",
            status="ok" if cards else "empty",
            count=len(cards),
            duration_sec=round(time.time() - t0, 1),
            note=f"{len(config.all_subreddits)} сабреддитов",
        )
    )

    logger.info("=== Search: keyword search ===")
    t0 = time.time()
    search_cards = await search_all_keywords(config, snapshot_date)
    manifest.add_source(
        SourceResult(
            name="reddit-search",
            status="ok" if search_cards else "empty",
            count=len(search_cards),
            duration_sec=round(time.time() - t0, 1),
            note=f"{len(config.keywords)} keywords",
        )
    )

    logger.info("=== Track: мониторинг тредов ===")
    t0 = time.time()
    thread_states = await track_all_threads(config, snapshot_date, state_file)
    manifest.add_source(
        SourceResult(
            name="reddit-track",
            status="ok" if thread_states else "empty",
            count=len(thread_states),
            duration_sec=round(time.time() - t0, 1),
            note=f"{len(config.tracked_threads)} тредов",
        )
    )

    all_cards = cards + search_cards

    logger.info("=== Virality: детекция виральности ===")
    signals = detect_virality(all_cards, config, snapshot_date)
    manifest.add_source(
        SourceResult(
            name="virality",
            status="ok" if signals else "empty",
            count=len(signals),
        )
    )

    logger.info("=== Export: запись snapshot ===")
    snap_dir = write_snapshot(
        output_dir, snapshot_date, all_cards, thread_states, signals, config.subreddit_clusters
    )

    from .export import write_threads_jsonl

    write_threads_jsonl(thread_states, state_file)

    manifest.finish()
    save_manifest(manifest, snap_dir_path)

    print(f"\n{'=' * 60}")
    print(f"✅ reddit-compass — snapshot {snapshot_date}")
    print(f"{'=' * 60}")
    print(f"  Постов (fetch):       {len(cards)}")
    print(f"  Постов (search):      {len(search_cards)}")
    print(f"  Tracked threads:      {len(thread_states)}")
    print(f"  Virality signals:     {len(signals)}")
    print(f"  Snapshot:             {snap_dir}")
    print(f"  Отчёт:                {snap_dir / 'trends-report.md'}")
    print(f"  Манифест:             {snap_dir_path / 'run-manifest.json'}")
    print(f"{'=' * 60}")


async def _cmd_nightly(args: argparse.Namespace) -> None:
    """Ночной прогон: all + trends analysis → harvests/. Stealth включён."""
    args.stealth = True
    await _cmd_all(args)

    from .trends_analysis import generate_trends_analysis

    config = _load_config(args)
    snapshot_date = _today()
    snap_dir = _snapshots_dir(args) / snapshot_date
    harvests_dir = DEFAULT_HARVESTS_DIR
    harvests_dir.mkdir(parents=True, exist_ok=True)

    output_path = harvests_dir / f"reddit-compass-{snapshot_date}.md"
    generate_trends_analysis(snap_dir, output_path, config, snapshot_date)
    print(f"📊 Trends analysis: {output_path}")


async def _cmd_signals(args: argparse.Namespace) -> None:
    """LLM-анализ сигналов (Qwen API): pain points, relevance, темы."""
    config = _load_config(args)
    if _dry_run_report(config, args):
        return
    snapshot_date = _today()
    snap_dir = _snapshots_dir(args) / snapshot_date

    from .signals import analyze_posts, render_signals_report, synthesize, write_signals_jsonl

    # Загружаем посты из ВСЕХ доступных JSONL (reddit, hn, rss, ladder, ph)
    _JSONL_SOURCES = [
        "posts.jsonl",
        "hackernews.jsonl",
        "rss.jsonl",
        "ladder.jsonl",
        "producthunt.jsonl",
    ]
    cards: list[PostCard] = []
    loaded_sources: list[str] = []
    for fname in _JSONL_SOURCES:
        fp = snap_dir / fname
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cards.append(PostCard.from_dict(json.loads(line)))
        loaded_sources.append(fname)

    if not cards:
        print(f"❌ Нет данных в {snap_dir}. Сначала соберите данные (fetch/hn/rss/ladder/ph).")
        sys.exit(1)

    print(f"   Источники: {', '.join(loaded_sources)}")

    # Лимит: топ-N по score для быстрого анализа
    limit = getattr(args, "top", 0)
    if limit and len(cards) > limit:
        cards = sorted(cards, key=lambda c: c.score, reverse=True)[:limit]
        print(f"   Лимит: топ-{limit} по score (из {len(loaded_sources)} источников)")

    print(f"🤖 LLM-анализ {len(cards)} постов (Qwen API)...")
    signals = await analyze_posts(cards)
    print(f"   Извлечено {len(signals)} сигналов")

    # Синтез
    synthesis = await synthesize(signals, snapshot_date, len(cards))
    if synthesis.top_themes:
        theme_names = [
            t.get("theme", str(t)) if isinstance(t, dict) else str(t)
            for t in synthesis.top_themes[:3]
        ]
        print(f"   Топ-темы: {', '.join(theme_names)}")

    # Запись
    signals_path = snap_dir / "signals.jsonl"
    write_signals_jsonl(signals, signals_path)

    report = render_signals_report(signals, synthesis, snapshot_date)
    report_path = snap_dir / "signals-report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"✅ Signals: {signals_path}")
    print(f"   Report: {report_path}")

    # История тем (для расчёта силы тренда и новизны)
    from .trend_strength import extract_themes_from_signals, save_theme_history

    data_dir = _snapshots_dir(args).parent
    signal_dicts = [s.to_dict() for s in signals]
    theme_snaps = extract_themes_from_signals(signal_dicts)
    if theme_snaps:
        # Устанавливаем дату если не была извлечена из сигналов
        for ts in theme_snaps:
            if not ts.date:
                ts.date = snapshot_date
        save_theme_history(data_dir, theme_snaps)
        print(f"   Theme history: {len(theme_snaps)} тем")

    # Trend radar с ссылками
    from .signals import render_trend_radar

    radar = render_trend_radar(snap_dir, snapshot_date)
    radar_path = snap_dir / "trend-radar.md"
    radar_path.write_text(radar, encoding="utf-8")
    print(f"   Radar: {radar_path}")


async def _cmd_hn(args: argparse.Namespace) -> None:
    """Hacker News: AI-stories через Algolia API."""
    config = _load_config(args)
    if _dry_run_report(config, args):
        return
    snapshot_date = _today()

    from .sources.hackernews import fetch_hn_stories

    print("📡 Hacker News: загрузка AI-stories (Algolia API)...")
    cards = await fetch_hn_stories(snapshot_date=snapshot_date)

    snap_dir = _snapshots_dir(args) / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    from .export import write_posts_jsonl

    write_posts_jsonl(cards, snap_dir / "hackernews.jsonl")
    print(f"✅ HN: {len(cards)} stories → {snap_dir / 'hackernews.jsonl'}")


async def _cmd_radar(args: argparse.Namespace) -> None:
    """Trend radar: отчёт с ссылками из собранных данных (без LLM)."""
    snapshot_date = _today()
    snap_dir = _snapshots_dir(args) / snapshot_date

    if not snap_dir.exists():
        print(f"❌ Snapshot не найден: {snap_dir}. Сначала соберите данные.")
        sys.exit(1)

    from .signals import render_trend_radar

    radar = render_trend_radar(snap_dir, snapshot_date)
    radar_path = snap_dir / "trend-radar.md"
    radar_path.write_text(radar, encoding="utf-8")
    print(f"✅ Trend radar: {radar_path}")


async def _cmd_rss(args: argparse.Namespace) -> None:
    """RSS: BBC, Guardian, Reuters, TechCrunch, Verge, Ars Technica."""
    config = _load_config(args)
    if _dry_run_report(config, args):
        return
    snapshot_date = _today()

    from .sources.rss import fetch_all_rss

    print("📡 RSS: загрузка 6 источников...")
    cards = await fetch_all_rss(snapshot_date=snapshot_date)

    snap_dir = _snapshots_dir(args) / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    from .export import write_posts_jsonl

    write_posts_jsonl(cards, snap_dir / "rss.jsonl")
    print(f"✅ RSS: {len(cards)} статей → {snap_dir / 'rss.jsonl'}")


async def _cmd_ladder(args: argparse.Namespace) -> None:
    """Ladder: NYT, WaPo, FT, Wired, Medium + остальные (paywall bypass)."""
    config = _load_config(args)
    if _dry_run_report(config, args):
        return
    snapshot_date = _today()

    from .sources.ladder import fetch_all_ladder

    print("🪜 Ladder: загрузка 12 paywall-источников...")
    cards = await fetch_all_ladder(snapshot_date=snapshot_date)

    snap_dir = _snapshots_dir(args) / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    from .export import write_posts_jsonl

    write_posts_jsonl(cards, snap_dir / "ladder.jsonl")
    print(f"✅ Ladder: {len(cards)} страниц → {snap_dir / 'ladder.jsonl'}")


async def _cmd_ph(args: argparse.Namespace) -> None:
    """ProductHunt: топ продуктов через GraphQL API."""
    config = _load_config(args)
    if _dry_run_report(config, args):
        return
    snapshot_date = _today()

    from .sources.producthunt import fetch_producthunt

    print("🚀 ProductHunt: загрузка топ продуктов...")
    cards = await fetch_producthunt(snapshot_date=snapshot_date)

    snap_dir = _snapshots_dir(args) / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    from .export import write_posts_jsonl

    write_posts_jsonl(cards, snap_dir / "producthunt.jsonl")
    print(f"✅ ProductHunt: {len(cards)} продуктов → {snap_dir / 'producthunt.jsonl'}")


async def _execute_collection(args: argparse.Namespace) -> object:
    from .collector import collect_sources, finalize_snapshot_collection

    config = _load_config(args)
    snapshots_dir = _snapshots_dir(args)
    db_path = (
        Path(args.source_db)
        if getattr(args, "source_db", None)
        else DEFAULT_SNAPSHOTS_DIR.parent / "compass.db"
    )
    sources = None
    if args.sources:
        sources = [s.strip() for s in args.sources.split(",")]
    profile = getattr(args, "profile", DEFAULT_PROFILE)
    snapshot_date = getattr(args, "date", None)
    if getattr(args, "from_snapshots", False):
        print(
            "📦 Finalize snapshot collection: "
            f"sources={sources or 'all'}, profile={profile}, date={snapshot_date or 'today'}"
        )
        return finalize_snapshot_collection(
            config=config,
            snapshots_dir=snapshots_dir,
            db_path=db_path,
            sources=sources,
            profile=profile,
            snapshot_date=snapshot_date,
        )
    print(f"🔄 Collect: sources={sources or 'all'}, profile={profile}")
    return await collect_sources(
        config=config,
        snapshots_dir=snapshots_dir,
        db_path=db_path,
        sources=sources,
        profile=profile,
    )


def _print_collection_result(result: object) -> None:
    from .collector import CollectionResult

    if not isinstance(result, CollectionResult):
        raise TypeError("Expected CollectionResult")
    print(f"\n{'=' * 60}")
    print(f"✅ Collection {result.run_id}: {result.status}")
    print(f"{'=' * 60}")
    for sr in result.source_results:
        print(f"  {sr.source_id}: {sr.status} ({sr.count} items, {sr.duration_sec}s)")
    print(f"  Total items: {len(result.items)}")
    print(f"{'=' * 60}")


def _record_corpus_version(result: object) -> None:
    """Отметить в реестре, какой корпус собран.

    Пишется после сбора, а не при публикации: между ними может пройти день, и без
    этой записи нельзя отличить «данные старые, потому что не собирали» от
    «собрали, но не опубликовали». Реестр живёт в trend_engine.db; если его ещё нет,
    сбор не должен падать из-за учёта версий.
    """
    from .collector import CollectionResult

    if not isinstance(result, CollectionResult):
        return
    try:
        from .intelligence.engine import DEFAULT_ENGINE_DB_PATH, engine_db, record_runtime_version

        conn = engine_db(DEFAULT_ENGINE_DB_PATH)
        try:
            record_runtime_version(
                conn,
                "corpus",
                result.run_id,
                {
                    "status": result.status,
                    "item_count": len(result.items),
                    "sources": len(result.source_results),
                },
            )
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Не удалось записать версию корпуса в реестр: %s", exc)


async def _cmd_collect(args: argparse.Namespace) -> None:
    """Collection-only process: network + raw corpus persistence."""
    result = await _execute_collection(args)
    _record_corpus_version(result)
    _print_collection_result(result)


async def _cmd_run(args: argparse.Namespace) -> None:
    """Compatibility alias: collect, optionally build unpublished engine releases."""
    from .collector import CollectionResult

    result = await _execute_collection(args)
    _print_collection_result(result)
    if not getattr(args, "analyze", False):
        return
    if not isinstance(result, CollectionResult):
        raise TypeError("Expected CollectionResult")
    if not result.items:
        print("⚠️ Engine skipped: collection contains no items")
        return
    print("⚠️ `run --analyze` deprecated: using separated Trend Engine stages")
    from .intelligence.engine import (
        create_data_release,
        create_facet_release,
        create_story_release,
        create_trend_release,
        engine_db,
        open_corpus_readonly,
    )

    corpus_path = DEFAULT_SNAPSHOTS_DIR.parent / "compass.db"
    corpus_conn = open_corpus_readonly(corpus_path)
    engine_conn = engine_db()
    try:
        data_release = create_data_release(
            corpus_conn,
            engine_conn,
            source_db_path=corpus_path,
            run_ids=[result.run_id],
        )
        config = _load_config(args)
        facets = create_facet_release(
            engine_conn,
            data_release_id=data_release.release_id,
            theme_catalog={theme.id: theme.keywords for theme in config.themes},
        )
        stories = create_story_release(
            engine_conn,
            facet_release_id=facets.facet_release_id,
        )
        trends = create_trend_release(
            engine_conn,
            story_release_id=stories.story_release_id,
        )
    finally:
        corpus_conn.close()
        engine_conn.close()
    print(
        "✅ Engine preview: "
        f"data={data_release.release_id}, stories={stories.story_release_id}, "
        f"trends={trends.trend_release_id}. Publish remains manual."
    )


def _engine_review_requested(args: argparse.Namespace) -> bool:
    """Whether either bounded Engine review stage needs a Qwen runner."""
    return int(args.review_limit) > 0 or int(args.trend_review_limit) > 0


async def _review_trend_jobs(
    jobs: list[dict[str, Any]],
    *,
    model: str,
    review_runner: Callable[[str, str], Awaitable[str]],
    store_response: Callable[..., dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Review a bounded batch without letting one Qwen failure abort it.

    A transport timeout is deliberately not persisted as an ``invalid`` LLM
    decision: invalid answers are cacheable, while a transient provider error
    must remain eligible for a later retry.
    """
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for job in jobs:
        target_id = str(job["target_id"])
        try:
            raw_response = await review_runner(str(job["prompt"]), model)
        except Exception as exc:  # bounded optional review; retain the batch
            error = exc.__class__.__name__
            errors.append(f"{target_id}:{error}")
            logger.warning("Trend review failed for %s: %s", target_id, error)
            results.append(
                {
                    "target_id": target_id,
                    "decision": "error",
                    "valid": False,
                    "error": error,
                }
            )
            continue
        results.append(
            store_response(
                target_id=target_id,
                input_hash=str(job["input_hash"]),
                raw_response=raw_response,
                allowed_story_ids={str(story_id) for story_id in job["story_ids"]},
                model=model,
                prompt_version=str(job["prompt_version"]),
            )
        )
    return results, errors


async def _cmd_engine(args: argparse.Namespace) -> None:
    """Versioned Story/Trend Engine; never mutates compass.db."""
    from dataclasses import asdict

    from .intelligence.actor_types import dump_actor_types, type_titles
    from .intelligence.engine import (
        DEFAULT_ENGINE_DB_PATH,
        _git_sha,
        _hash_json,
        _stable_id,
        active_label_story_pairs,
        auto_label_story_pairs,
        cache_release_embeddings,
        calibrate_dense_thresholds,
        compare_engine_versions,
        compare_story_engine_variants,
        create_data_release,
        create_facet_release,
        create_story_release,
        create_trend_release,
        diagnose_engine_release,
        engine_db,
        evaluate_story_release,
        evaluate_trend_release,
        export_golden_candidates,
        export_story_candidates_for_release,
        import_golden_labels,
        import_legacy_lab,
        inspect_story_release,
        inspect_trend_release,
        label_engine_target,
        list_data_releases,
        list_publications,
        now_iso,
        open_corpus_readonly,
        prepare_story_review_jobs,
        prepare_trend_review_jobs,
        publish_radar,
        rollback_publication,
        run_engine_cycle,
        store_quality_report,
        store_story_review_response,
        store_trend_review_response,
        train_story_merge_model,
        verify_data_release,
    )
    from .intelligence.trend_schema_llm import (
        extract_schemas,
        load_schemas,
        store_schemas,
        title_key,
    )

    engine_path = (
        Path(args.engine_db) if getattr(args, "engine_db", None) else DEFAULT_ENGINE_DB_PATH
    )
    engine_conn = engine_db(engine_path)
    try:
        if args.engine_group == "release":
            if args.engine_action == "create":
                corpus_path = (
                    Path(args.source_db)
                    if getattr(args, "source_db", None)
                    else DEFAULT_SNAPSHOTS_DIR.parent / "compass.db"
                )
                corpus_conn = open_corpus_readonly(corpus_path)
                try:
                    data_release_output = create_data_release(
                        corpus_conn,
                        engine_conn,
                        source_db_path=corpus_path,
                        run_ids=args.run,
                    )
                finally:
                    corpus_conn.close()
                print(json.dumps(asdict(data_release_output), ensure_ascii=False, indent=2))
                return
            if args.engine_action == "list":
                print(
                    json.dumps(
                        [
                            asdict(data_release_item)
                            for data_release_item in list_data_releases(engine_conn)
                        ],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            if args.engine_action == "verify":
                valid = verify_data_release(engine_conn, args.release)
                print(json.dumps({"release_id": args.release, "valid": valid}, indent=2))
                if not valid:
                    raise SystemExit(1)
                return
        if args.engine_group == "embeddings":
            result = cache_release_embeddings(
                engine_conn,
                data_release_id=args.release,
                model_name=args.model,
                model_revision=args.model_revision,
                batch_size=args.batch_size,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.engine_group == "calibrate":
            calibration = calibrate_dense_thresholds(
                engine_conn,
                data_release_id=args.release,
                model_name=args.model,
                model_revision=args.model_revision,
                reference_model=args.reference_model or None,
                max_positive_pairs=args.max_positive_pairs,
                max_negative_pairs=args.max_negative_pairs,
                seed=args.seed,
            )
            print(json.dumps(calibration, ensure_ascii=False, indent=2))
            return
        if args.engine_group == "golden":
            if args.engine_action == "export":
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if args.format == "review":
                    payload = export_golden_candidates(
                        engine_conn,
                        args.story_release,
                        output_format="review",
                        sample=args.sample,
                        seed=args.seed,
                    )
                    lines = [json.dumps(pair, ensure_ascii=False) for pair in payload["pairs"]]
                    output_path.write_text(
                        "\n".join(lines) + ("\n" if lines else ""),
                        encoding="utf-8",
                    )
                    print(
                        json.dumps(
                            {
                                "output": str(output_path),
                                "format": "review",
                                "pairs": len(payload["pairs"]),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    return
                payload = export_golden_candidates(
                    engine_conn,
                    args.story_release,
                    pair_limit=args.pair_limit,
                    group_limit=args.group_limit,
                )
                output_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(
                    json.dumps(
                        {
                            "output": str(output_path),
                            "pairs": len(payload["pairs"]),
                            "groups": len(payload["groups"]),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            if args.engine_action == "import":
                raw_text = Path(args.input).read_text(encoding="utf-8")
                stripped = raw_text.lstrip()
                if stripped.startswith("{") and "\n{" in stripped.rstrip():
                    # JSONL из `--format review`: одна размеченная пара на строку.
                    # story_release_id в строках не лежит, поэтому берём его из флага.
                    if not args.story_release:
                        raise ValueError(
                            "JSONL review labels require --story-release "
                            "(the file carries pairs only)"
                        )
                    payload_raw = {
                        "story_release_id": args.story_release,
                        "pairs": [
                            json.loads(line) for line in raw_text.splitlines() if line.strip()
                        ],
                    }
                else:
                    payload_raw = json.loads(raw_text)
                    if not isinstance(payload_raw, dict):
                        raise ValueError("Golden Set root must be a JSON object")
                result = import_golden_labels(engine_conn, payload_raw, source=args.note)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
        if args.engine_group == "legacy":
            result = import_legacy_lab(
                engine_conn,
                legacy_lab_path=Path(args.lab_db),
                source_db_path=Path(args.source_db),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.engine_group == "facets":
            config = _load_config(args)
            facet_release_output = create_facet_release(
                engine_conn,
                data_release_id=args.release,
                theme_catalog={theme.id: theme.keywords for theme in config.themes},
            )
            print(json.dumps(asdict(facet_release_output), ensure_ascii=False, indent=2))
            return
        if args.engine_group == "experiments" and args.engine_action == "compare":
            result = compare_story_engine_variants(
                engine_conn,
                facet_release_id=args.facet_release,
                base_params={
                    "embedding_model": args.embedding_model,
                    "embedding_revision": args.embedding_revision,
                    "dense_top_k": args.dense_top_k,
                    "dense_candidate_threshold": args.dense_threshold,
                    "auto_merge_threshold": args.auto_merge_threshold,
                    "review_threshold": args.review_threshold,
                    "semantic_dedup_threshold": args.semantic_dedup_threshold,
                    "semantic_dedup_max_days": args.semantic_dedup_max_days,
                    "near_duplicate_max_bucket_size": args.near_duplicate_max_bucket_size,
                    "near_duplicate_simhash_distance": args.near_duplicate_simhash_distance,
                    "near_duplicate_shingle_jaccard": args.near_duplicate_shingle_jaccard,
                },
                limit=args.limit,
                domain=args.domain,
                sample_limit=args.sample_limit,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.engine_group == "diagnose":
            result = diagnose_engine_release(
                engine_conn,
                data_release_id=args.release,
                story_release_id=args.story_release,
                trend_release_id=args.trend_release,
                limit=args.limit,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return
        if args.engine_group == "stories":
            if args.engine_action == "candidates":
                story_params = {
                    "embedding_model": args.embedding_model,
                    "embedding_revision": args.embedding_revision,
                    "dense_top_k": args.dense_top_k,
                    "dense_candidate_threshold": args.dense_threshold,
                    "auto_merge_threshold": args.auto_merge_threshold,
                    "review_threshold": args.review_threshold,
                    "near_duplicate_enabled": not args.no_near_duplicates,
                    "near_duplicate_max_bucket_size": args.near_duplicate_max_bucket_size,
                    "near_duplicate_simhash_distance": args.near_duplicate_simhash_distance,
                    "near_duplicate_shingle_jaccard": args.near_duplicate_shingle_jaccard,
                    "semantic_dedup_enabled": args.semantic_dedup,
                    "semantic_dedup_threshold": args.semantic_dedup_threshold,
                    "semantic_dedup_max_days": args.semantic_dedup_max_days,
                }
                result = export_story_candidates_for_release(
                    engine_conn,
                    facet_release_id=args.facet_release,
                    params=story_params,
                    limit=args.limit,
                    domain=args.domain,
                    candidate_limit=args.candidate_limit,
                )
                if args.output:
                    output_path = Path(args.output)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(
                        "\n".join(
                            json.dumps(candidate, ensure_ascii=False)
                            for candidate in result["candidates"]
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    summary = {k: v for k, v in result.items() if k != "candidates"}
                    summary["output"] = str(output_path)
                    print(json.dumps(summary, ensure_ascii=False, indent=2))
                else:
                    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                return
            if args.engine_action == "propose":
                story_params = {
                    "embedding_model": args.embedding_model,
                    "embedding_revision": args.embedding_revision,
                    "dense_top_k": args.dense_top_k,
                    "near_duplicate_enabled": not args.no_near_duplicates,
                    "near_duplicate_max_bucket_size": args.near_duplicate_max_bucket_size,
                    "near_duplicate_simhash_distance": args.near_duplicate_simhash_distance,
                    "near_duplicate_shingle_jaccard": args.near_duplicate_shingle_jaccard,
                    "semantic_dedup_enabled": args.semantic_dedup,
                    "semantic_dedup_max_days": args.semantic_dedup_max_days,
                    "cross_encoder_enabled": bool(args.cross_encoder),
                    "cross_encoder_threshold": float(args.cross_encoder_threshold),
                }
                # Пороги, зависящие от модели эмбеддингов, передаём ТОЛЬКО если их явно
                # задали флагом. Иначе дефолт CLI перебил бы профиль модели
                # (embeddings.DENSE_THRESHOLD_PROFILES) и вернул общие константы.
                for flag_name, param_name in (
                    ("dense_threshold", "dense_candidate_threshold"),
                    ("auto_merge_threshold", "auto_merge_threshold"),
                    ("review_threshold", "review_threshold"),
                    ("semantic_dedup_threshold", "semantic_dedup_threshold"),
                ):
                    value = getattr(args, flag_name)
                    if value is not None:
                        story_params[param_name] = value
                story_release_output = create_story_release(
                    engine_conn,
                    facet_release_id=args.facet_release,
                    limit=args.limit,
                    domain=args.domain,
                    params=story_params,
                )
                print(json.dumps(asdict(story_release_output), ensure_ascii=False, indent=2))
                return
            if args.engine_action == "inspect":
                result = inspect_story_release(
                    engine_conn,
                    args.story_release,
                    limit=args.limit,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                return
            if args.engine_action == "verified":
                from .intelligence.verified_stories import get_verified_stories

                verified = get_verified_stories(
                    engine_conn,
                    args.story_release,
                    signal_release_id=args.signal_release,
                )
                output = [
                    {
                        "story_id": v.story_id,
                        "title": v.title[:100],
                        "reasons": v.verification_reasons,
                        "source_count": v.source_count,
                        "item_count": v.item_count,
                        "cross_source": v.is_cross_source,
                        "providers": v.providers,
                    }
                    for v in verified[: args.limit]
                ]
                print(
                    json.dumps(
                        {
                            "story_release_id": args.story_release,
                            "total_verified": len(verified),
                            "shown": len(output),
                            "stories": output,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            if args.engine_action == "eval":
                result = evaluate_story_release(engine_conn, args.story_release)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
            if args.engine_action == "review":
                from .signals import call_qwen_json

                jobs = prepare_story_review_jobs(
                    engine_conn,
                    args.story_release,
                    limit=args.limit,
                    model=args.model,
                )
                results = []
                for job in jobs:
                    raw_response = await call_qwen_json(
                        str(job["prompt"]),
                        model=args.model,
                    )
                    results.append(
                        store_story_review_response(
                            engine_conn,
                            target_id=str(job["target_id"]),
                            input_hash=str(job["input_hash"]),
                            raw_response=raw_response,
                            allowed_item_ids={str(item_id) for item_id in job["item_ids"]},
                            model=args.model,
                            prompt_version=str(job["prompt_version"]),
                        )
                    )
                print(
                    json.dumps(
                        {
                            "story_release_id": args.story_release,
                            "reviewed": len(results),
                            "results": results,
                            "next": (
                                "Run `engine stories propose` again with the same "
                                "facet release to create a reviewed attempt."
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
        if args.engine_group == "schemas":
            titles = [
                str(row["title"] or "")
                for row in engine_conn.execute(
                    "SELECT title FROM engine_stories WHERE story_release_id = ? ORDER BY story_id",
                    (args.story_release,),
                )
            ]
            if args.engine_action == "extract":
                if args.limit > 0:
                    titles = titles[: args.limit]
                # Кэш читаем ОДИН раз. Вызов внутри условия давал O(n²): на 9 317
                # заголовках это ~87 млн хэшей и полный скан таблицы на каждый элемент —
                # процесс жёг 99 % CPU и не доходил до первого запроса к модели.
                have = load_schemas(engine_conn, titles)
                pending = [t for t in titles if title_key(t) not in have]
                print(
                    f"заголовков {len(titles)}, в кэше {len(titles) - len(pending)}, "
                    f"извлекать {len(pending)}",
                    file=sys.stderr,
                )
                records = await extract_schemas(
                    pending,
                    lambda prompt, model: call_qwen_json(
                        prompt, model=model, timeout_seconds=180.0
                    ),
                    model=args.model,
                    batch_size=int(args.batch_size),
                    on_batch=lambda n, total: print(f"  батч {n}/{total}", file=sys.stderr),
                )
                written = store_schemas(engine_conn, records, model=args.model)
                print(json.dumps({"extracted": written, "requested": len(pending)}))
                return
            if args.engine_action == "stats":
                cached = load_schemas(engine_conn, titles)
                events = [r for r in cached.values() if r.get("is_event")]
                other = [r for r in events if r.get("key") == "other"]
                by_key: dict[str, int] = {}
                for record in events:
                    by_key[str(record.get("key"))] = by_key.get(str(record.get("key")), 0) + 1
                print(
                    json.dumps(
                        {
                            "titles": len(titles),
                            "cached": len(cached),
                            "events": len(events),
                            "event_share": round(100 * len(events) / max(len(cached), 1), 1),
                            "other_share": round(100 * len(other) / max(len(events), 1), 1),
                            "by_action": dict(sorted(by_key.items(), key=lambda kv: -kv[1])),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
        if args.engine_group == "actors":
            if args.engine_action == "export-titles":
                rows = engine_conn.execute(
                    "SELECT title FROM engine_stories WHERE story_release_id = ? ORDER BY story_id",
                    (args.story_release,),
                ).fetchall()
                print(json.dumps([str(row["title"] or "") for row in rows], ensure_ascii=False))
                return
            if args.engine_action == "type":
                raw = sys.stdin.read() if args.titles == "-" else Path(args.titles).read_text()
                titles = [str(title) for title in json.loads(raw)]
                table = type_titles(titles, model_id=args.model, threshold=float(args.threshold))
                Path(args.out).write_text(
                    dump_actor_types(
                        table,
                        model_id=args.model,
                        threshold=float(args.threshold),
                        built_at=now_iso(),
                    ),
                    encoding="utf-8",
                )
                print(
                    json.dumps(
                        {"titles": len(titles), "typed": len(table), "out": args.out},
                        ensure_ascii=False,
                    )
                )
                return
        if args.engine_group == "trends":
            if args.engine_action == "propose":
                trend_release_output = create_trend_release(
                    engine_conn,
                    story_release_id=args.story_release,
                    window=args.window,
                    method=args.method,
                    params={
                        "trend_top_k": args.top_k,
                        "trend_edge_threshold": args.edge_threshold,
                        "trend_medoid_threshold": args.medoid_threshold,
                        "trend_max_feature_df": args.max_feature_df,
                        "trend_max_candidate_pairs": args.max_candidate_pairs,
                        "trend_schema_depth": args.trend_depth,
                        **({"actor_types_path": args.actor_types} if args.actor_types else {}),
                    },
                    verified_only=args.verified_only,
                    signal_release_id=args.signal_release,
                )
                print(json.dumps(asdict(trend_release_output), ensure_ascii=False, indent=2))
                return
            if args.engine_action == "inspect":
                result = inspect_trend_release(
                    engine_conn,
                    args.trend_release,
                    limit=args.limit,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                return
            if args.engine_action == "eval":
                result = evaluate_trend_release(engine_conn, args.trend_release)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
            if args.engine_action == "review":
                from .signals import call_qwen_json

                jobs = prepare_trend_review_jobs(
                    engine_conn,
                    args.trend_release,
                    limit=args.limit,
                    model=args.model,
                )
                results, errors = await _review_trend_jobs(
                    jobs,
                    model=args.model,
                    review_runner=lambda prompt, model: call_qwen_json(prompt, model=model),
                    store_response=lambda **kwargs: store_trend_review_response(
                        engine_conn, **kwargs
                    ),
                )
                print(
                    json.dumps(
                        {
                            "trend_release_id": args.trend_release,
                            "reviewed": len(results),
                            "failed": len(errors),
                            "errors": errors,
                            "results": results,
                            "next": (
                                "Run `engine trends propose` again with the same "
                                "story release to create a reviewed attempt."
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
        if args.engine_group == "reddit-pulse":
            if args.pulse_action == "propose":
                from .intelligence.reddit_pulse import (
                    build_reddit_pulse_signals,
                    perspective_gap_available_counts,
                    tokenize_title,
                )

                # Load items from the data release
                rows = engine_conn.execute(
                    """
                    SELECT item_id, provider, source_cluster, external_id,
                            source_section, title, excerpt, canonical_url,
                            discussion_url, target_url, domain_ids,
                            metadata, raw_engagement, snapshot_date,
                            published_at, observed_at
                    FROM release_items
                    WHERE release_id = ? AND snapshot_date = ? AND provider = 'reddit'
                    ORDER BY json_extract(raw_engagement, '$.score') DESC
                    """,
                    (args.release, args.date),
                ).fetchall()
                if not rows:
                    print(
                        json.dumps(
                            {
                                "error": "no reddit items found",
                                "release": args.release,
                                "date": args.date,
                            }
                        )
                    )
                    return
                from .intelligence.models import ContentItem

                items: list[ContentItem] = []
                for r in rows:
                    meta = json.loads(r["metadata"] or "{}")
                    eng = json.loads(r["raw_engagement"] or "{}")
                    domain_ids = json.loads(r["domain_ids"] or '["other"]')
                    items.append(
                        ContentItem(
                            item_id=r["item_id"],
                            provider=r["provider"],
                            source_cluster=r["source_cluster"],
                            external_id=r["external_id"],
                            source_section=r["source_section"],
                            title=r["title"],
                            excerpt=r["excerpt"] or "",
                            canonical_url=r["canonical_url"] or "",
                            discussion_url=r["discussion_url"] or "",
                            target_url=r["target_url"] or "",
                            domain_ids=domain_ids,
                            metadata=meta,
                            raw_engagement=eng,
                            snapshot_date=r["snapshot_date"],
                            observed_at=r["observed_at"] or "",
                            published_at=r["published_at"] or None,
                        )
                    )
                config = _load_config(args)
                pack_by_subreddit = {
                    subreddit.lower(): pack_id
                    for pack_id, subreddits in config.subreddits.items()
                    for subreddit in subreddits
                }
                history_rows = engine_conn.execute(
                    """
                    SELECT ri.title
                    FROM release_items ri
                    JOIN data_releases dr ON dr.release_id = ri.release_id
                    WHERE dr.status = 'finalized'
                      AND dr.profile = (
                        SELECT profile FROM data_releases WHERE release_id = ?
                      )
                      AND ri.provider = 'reddit'
                      AND ri.snapshot_date < ?
                      AND date(ri.snapshot_date) >= date(?, '-' || ? || ' days')
                    """,
                    (args.release, args.date, args.date, args.history_window_days),
                ).fetchall()
                seen_titles = {
                    " ".join(sorted(tokenize_title(str(row["title"])))) for row in history_rows
                }
                story_id_by_item_id: dict[str, str] = {}
                mainstream_coverage_by_story_id: dict[str, int] = {}
                facet_release_id = args.facet_release or ""
                if args.story_release:
                    release_row = engine_conn.execute(
                        """
                        SELECT sr.facet_release_id, fr.data_release_id
                        FROM story_releases sr
                        JOIN facet_releases fr
                          ON fr.facet_release_id = sr.facet_release_id
                        WHERE sr.story_release_id = ?
                        """,
                        (args.story_release,),
                    ).fetchone()
                    if release_row is None:
                        raise ValueError(f"Story release not found: {args.story_release}")
                    if str(release_row["data_release_id"]) != args.release:
                        raise ValueError(
                            "Story release belongs to a different data release: "
                            f"{release_row['data_release_id']} != {args.release}"
                        )
                    facet_release_id = str(release_row["facet_release_id"])
                    story_rows = engine_conn.execute(
                        """
                        SELECT item_id, story_id
                        FROM engine_story_items
                        WHERE story_release_id = ?
                        """,
                        (args.story_release,),
                    ).fetchall()
                    story_id_by_item_id = {
                        str(row["item_id"]): str(row["story_id"]) for row in story_rows
                    }
                    coverage_rows = engine_conn.execute(
                        """
                        SELECT esi.story_id,
                               COUNT(DISTINCT ri.provider || ':' ||
                                     COALESCE(NULLIF(ri.source_section, ''), ri.source_cluster))
                               AS mainstream_coverage
                        FROM engine_story_items esi
                        JOIN release_items ri
                          ON ri.release_id = ?
                         AND ri.item_id = esi.item_id
                        WHERE esi.story_release_id = ?
                          AND ri.provider != 'reddit'
                          AND ri.source_cluster IN ('mainstream', 'business', 'tech_culture')
                        GROUP BY esi.story_id
                        """,
                        (args.release, args.story_release),
                    ).fetchall()
                    mainstream_coverage_by_story_id = {
                        str(row["story_id"]): int(row["mainstream_coverage"] or 0)
                        for row in coverage_rows
                    }
                # Баланс для разрыва перспективы считаем по ВСЕМУ релизу, а не по
                # reddit-only выборке (иначе guard всегда триггерит — баг Фазы 4).
                balance_rows = engine_conn.execute(
                    """SELECT source_cluster, COUNT(*) AS n
                       FROM release_items WHERE release_id = ?
                       GROUP BY source_cluster""",
                    (args.release,),
                ).fetchall()
                cluster_counts = {str(r["source_cluster"]): int(r["n"]) for r in balance_rows}
                gap_available = perspective_gap_available_counts(
                    cluster_counts.get("voices", 0),
                    cluster_counts.get("mainstream", 0),
                )
                signals = build_reddit_pulse_signals(
                    items,
                    seen_titles,
                    pack_by_subreddit=pack_by_subreddit,
                    story_id_by_item_id=story_id_by_item_id,
                    mainstream_coverage_by_story_id=mainstream_coverage_by_story_id,
                    history_available=bool(history_rows),
                    gap_available=gap_available,
                )
                # Store in DB
                import datetime

                method = args.method_version
                pulse_params = {
                    "method": method,
                    "profile": args.profile,
                    "date": args.date,
                    "history_window_days": args.history_window_days,
                    "story_release_id": args.story_release or "",
                    "facet_release_id": facet_release_id,
                    "history_item_count": len(history_rows),
                    "pack_count": len(pack_by_subreddit),
                }
                params_hash = _hash_json(pulse_params)
                signal_release_id = args.signal_release_id or _stable_id(
                    "signals",
                    args.release,
                    args.date,
                    args.profile,
                    method,
                    params_hash,
                    datetime.datetime.now(datetime.UTC).isoformat(),
                )
                now = datetime.datetime.now(datetime.UTC).isoformat()
                metrics = {
                    "schema_version": 2,
                    "signal_count": len(signals),
                    "history_item_count": len(history_rows),
                    "history_available": bool(history_rows),
                    "linked_story_count": len(
                        {s.linked_story_id for s in signals if s.linked_story_id}
                    ),
                    "mainstream_covered_signal_count": sum(
                        1 for s in signals if s.mainstream_coverage_count > 0
                    ),
                    "perspective_gap_available": gap_available,
                    "neutral_novelty": not bool(history_rows),
                }
                engine_conn.execute(
                    """INSERT OR REPLACE INTO signal_releases
                        (signal_release_id, data_release_id, facet_release_id,
                         story_release_id, date, method, params_hash, metrics_json,
                         git_sha, status, signal_count, created_at, finalized_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'finalized', ?, ?, ?)""",
                    (
                        signal_release_id,
                        args.release,
                        facet_release_id,
                        args.story_release,
                        args.date,
                        method,
                        params_hash,
                        json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                        _git_sha(),
                        len(signals),
                        now,
                        now,
                    ),
                )
                engine_conn.execute(
                    "DELETE FROM community_signals WHERE signal_release_id = ?",
                    (signal_release_id,),
                )
                for s in signals:
                    engine_conn.execute(
                        """INSERT OR REPLACE INTO community_signals
                           (signal_release_id, signal_id, item_id, subreddit, pack_id,
                            signal_type, title, discussion_url, target_url,
                            pulse_score, subreddit_percentile, score_velocity,
                            comment_velocity, discussion_depth, comment_score_ratio,
                             cross_subreddit_repetition, novelty,
                             domain_ids_json, theme_ids_json, pain_points_json,
                             project_scores_json, linked_story_id,
                             mainstream_coverage_count, perspective_gap)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            signal_release_id,
                            s.signal_id,
                            s.item_id,
                            s.subreddit,
                            s.pack_id,
                            s.signal_type,
                            s.title,
                            s.discussion_url,
                            s.target_url,
                            s.pulse_score,
                            s.subreddit_percentile,
                            s.score_velocity,
                            s.comment_velocity,
                            s.discussion_depth,
                            s.comment_score_ratio,
                            s.cross_subreddit_repetition,
                            s.novelty,
                            json.dumps(s.domain_ids, ensure_ascii=False),
                            json.dumps(s.theme_ids, ensure_ascii=False),
                            json.dumps(s.pain_points, ensure_ascii=False),
                            json.dumps(s.project_scores, ensure_ascii=False),
                            s.linked_story_id,
                            s.mainstream_coverage_count,
                            s.perspective_gap,
                        ),
                    )
                engine_conn.commit()
                # Summary
                by_type: dict[str, int] = {}
                for s in signals:
                    by_type[s.signal_type] = by_type.get(s.signal_type, 0) + 1
                top5 = sorted(signals, key=lambda x: x.pulse_score, reverse=True)[:5]
                print(
                    json.dumps(
                        {
                            "signal_release_id": signal_release_id,
                            "total_signals": len(signals),
                            "metrics": metrics,
                            "by_type": by_type,
                            "top5": [
                                {
                                    "title": s.title[:80],
                                    "subreddit": s.subreddit,
                                    "pulse_score": s.pulse_score,
                                    "type": s.signal_type,
                                }
                                for s in top5
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            if args.pulse_action == "inspect":
                where = "signal_release_id = ?"
                params: list[str] = [args.signal_release]
                if args.signal_type:
                    where += " AND signal_type = ?"
                    params.append(args.signal_type)
                if args.subreddit:
                    where += " AND LOWER(subreddit) = ?"
                    params.append(args.subreddit.lower())
                rows = engine_conn.execute(
                    f"""SELECT signal_id, subreddit, signal_type, title,
                               pulse_score, subreddit_percentile, comment_velocity,
                               discussion_depth, cross_subreddit_repetition, novelty
                        FROM community_signals
                        WHERE {where}
                        ORDER BY pulse_score DESC
                        LIMIT ?""",
                    (*params, args.limit),
                ).fetchall()
                print(
                    json.dumps(
                        [dict(r) for r in rows],
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                )
                return
        if args.engine_group == "label":
            if args.label_action == "active":
                if not args.story_release:
                    raise SystemExit("engine label active requires --story-release")
                result = active_label_story_pairs(
                    engine_conn,
                    args.story_release,
                    target=int(args.target or 150),
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
            if args.label_action == "auto":
                if not args.story_release:
                    raise SystemExit("engine label auto requires --story-release")
                result = auto_label_story_pairs(engine_conn, args.story_release)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
            if args.label_action == "train":
                if not args.story_release:
                    raise SystemExit("engine label train requires --story-release")
                result = train_story_merge_model(
                    engine_conn,
                    args.story_release,
                    target_precision=float(args.target_precision),
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
            if not args.kind or not args.target or not args.label or not args.release:
                raise SystemExit(
                    "engine label requires --kind, --target, --release and --label "
                    "unless using `engine label active|auto|train`"
                )
            target_id = args.target
            if args.kind == "story_pair":
                target_id = "|".join(sorted(part.strip() for part in target_id.split(",")))
            label_id = label_engine_target(
                engine_conn,
                target_kind=args.kind,
                target_id=target_id,
                release_id=args.release,
                label=args.label,
                note=args.note,
            )
            print(json.dumps({"label_id": label_id}, indent=2))
            return
        if args.engine_group == "quality":
            from .intelligence.quality import (
                compute_quality,
                evaluate_floors,
                evaluate_regressions,
                load_baseline,
                save_baseline,
            )

            data_release = args.data_release
            story_release = args.story_release
            trend_release = args.trend_release
            signal_release = args.signal_release
            if not (data_release and story_release and trend_release):
                row = engine_conn.execute(
                    "SELECT current_publication_id FROM published_channels WHERE channel = ?",
                    (args.channel,),
                ).fetchone()
                if row is None:
                    raise SystemExit(
                        f"No publication for channel {args.channel}; pass releases explicitly"
                    )
                pub = engine_conn.execute(
                    """SELECT data_release_id, story_release_id, trend_release_id
                       FROM radar_publications WHERE publication_id = ?""",
                    (row["current_publication_id"],),
                ).fetchone()
                data_release = data_release or str(pub["data_release_id"])
                story_release = story_release or str(pub["story_release_id"])
                trend_release = trend_release or str(pub["trend_release_id"])
                if not signal_release:
                    sr = engine_conn.execute(
                        """SELECT signal_release_id FROM signal_releases
                           WHERE data_release_id = ? ORDER BY created_at DESC LIMIT 1""",
                        (data_release,),
                    ).fetchone()
                    signal_release = str(sr["signal_release_id"]) if sr else None
            metrics = compute_quality(
                engine_conn,
                data_release_id=data_release,
                story_release_id=story_release,
                trend_release_id=trend_release,
                signal_release_id=signal_release,
            )
            floors = evaluate_floors(metrics)
            report = store_quality_report(
                engine_conn,
                data_release_id=data_release,
                story_release_id=story_release,
                trend_release_id=trend_release,
                signal_release_id=signal_release,
                metrics=metrics,
                floors=floors,
            )
            if args.quality_action == "snapshot":
                save_baseline(Path(args.out), metrics)
                print(
                    json.dumps(
                        {"saved": args.out, "metrics": metrics}, ensure_ascii=False, indent=2
                    )
                )
                return
            if args.quality_action == "report":
                print(
                    json.dumps(
                        report,
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            baseline = load_baseline(Path(args.baseline))
            regressions = evaluate_regressions(metrics, baseline)
            floor_fail = [asdict(f) for f in floors if not f.passed]
            reg_fail = [r for r in regressions if r["regressed"]]
            print(
                json.dumps(
                    {"floors_failed": floor_fail, "regressions": reg_fail},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            if floor_fail or reg_fail:
                raise SystemExit(1)
            return
        if args.engine_group == "cycle":
            from .signals import call_qwen_json

            config = _load_config(args)
            theme_catalog = {theme.id: theme.keywords for theme in config.themes}
            pack_by_subreddit = {
                sub.lower(): pack for pack, subs in config.subreddits.items() for sub in subs
            }
            corpus_path = DEFAULT_SNAPSHOTS_DIR.parent / "compass.db"
            corpus_conn = open_corpus_readonly(corpus_path)
            review_runner = (
                (lambda prompt, model: call_qwen_json(prompt, model=model))
                if _engine_review_requested(args)
                else None
            )
            try:
                result = await run_engine_cycle(
                    corpus_conn,
                    engine_conn,
                    corpus_path=corpus_path,
                    profile=args.profile,
                    window=int(args.window),
                    theme_catalog=theme_catalog,
                    pack_by_subreddit=pack_by_subreddit,
                    trend_method=args.trend_method,
                    trend_depth=int(args.trend_depth),
                    embed_model=args.embed_model,
                    review_model=args.review_model,
                    review_limit=int(args.review_limit),
                    trend_review_model=args.trend_review_model,
                    trend_review_limit=int(args.trend_review_limit),
                    review_runner=review_runner,
                    publish_channel=args.publish_channel or None,
                    allow_partial=args.allow_partial,
                    pulse=not args.no_pulse,
                    cross_encoder=bool(args.cross_encoder),
                    cross_encoder_threshold=float(args.cross_encoder_threshold),
                )
            finally:
                corpus_conn.close()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.engine_group == "publish":
            publication = publish_radar(
                engine_conn,
                story_release_id=args.story_release,
                trend_release_id=args.trend_release,
                channel=args.channel,
                allow_partial=args.allow_partial,
                force=getattr(args, "force", False),
            )
            print(json.dumps(asdict(publication), ensure_ascii=False, indent=2))
            return
        if args.engine_group == "rollback":
            publication = rollback_publication(
                engine_conn,
                channel=args.channel,
                to_publication_id=args.to,
            )
            print(json.dumps(asdict(publication), ensure_ascii=False, indent=2))
            return
        if args.engine_group == "publications":
            publications = list_publications(engine_conn, args.channel)
            print(
                json.dumps(
                    [asdict(publication) for publication in publications],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if args.engine_group == "compare":
            result = compare_engine_versions(engine_conn, args.left, args.right)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return
        raise SystemExit("Unknown engine command")
    finally:
        engine_conn.close()


async def _cmd_version(args: argparse.Namespace) -> None:
    """Реестр версий: что развёрнуто и на каких данных работает."""
    from .intelligence.engine import (
        DEFAULT_ENGINE_DB_PATH,
        engine_db,
        open_engine_readonly,
        record_runtime_version,
    )
    from .versioning import (
        APP_COMPONENT,
        ASSETS_COMPONENT,
        app_version,
        assets_version,
        build_info,
        version_report,
    )

    engine_path = Path(args.engine_db) if args.engine_db else DEFAULT_ENGINE_DB_PATH
    if args.record:
        # Пишется в момент деплоя: после него узнать, какой SHA собран, уже неоткуда —
        # на VPS исходники лежат копией без git.
        build = build_info()
        conn = engine_db(engine_path)
        try:
            record_runtime_version(
                conn,
                APP_COMPONENT,
                build.get("version", app_version()),
                {"git_sha": build.get("git_sha", "unknown"), "built_at": build.get("built_at", "")},
            )
            record_runtime_version(conn, ASSETS_COMPONENT, assets_version())
        finally:
            conn.close()

    reader = open_engine_readonly(engine_path) if engine_path.exists() else None
    try:
        report = version_report(engine_conn=reader)
    finally:
        if reader is not None:
            reader.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _cmd_serve_sync(args: argparse.Namespace) -> None:
    """Запуск REST API (FastAPI/uvicorn). Синхронный — uvicorn сам управляет event loop."""
    import uvicorn

    from .api.app import create_app

    app = create_app()
    print("🚀 reddit-compass API: http://127.0.0.1:8900")
    print("   Docs: http://127.0.0.1:8900/docs")
    uvicorn.run(app, host="0.0.0.0", port=8900, log_level="info")


async def _cmd_serve(args: argparse.Namespace) -> None:
    """Обёртка для совместимости с async handler."""
    _cmd_serve_sync(args)


async def _cmd_db(args: argparse.Namespace) -> None:
    """SQLite: init / stats / rebuild."""
    from .db import get_db, query_stats

    db_path = (
        Path(args.source_db)
        if getattr(args, "source_db", None)
        else DEFAULT_SNAPSHOTS_DIR.parent / "compass.db"
    )
    action = args.db_action

    if action == "init":
        conn = get_db(db_path)
        conn.close()
        print(f"✅ SQLite инициализирована: {db_path}")
    elif action == "stats":
        conn = get_db(db_path)
        stats = query_stats(conn)
        conn.close()
        print(f"📊 Snapshots: {stats['total_snapshots']}")
        print(f"   Posts: {stats['total_posts']}")
        print(f"   Signals: {stats['total_signals']}")
        print(f"   Latest: {stats['latest_snapshot']}")
        if stats["top_subreddits"]:
            print("   Top subreddits:")
            for s in stats["top_subreddits"][:5]:
                print(f"     r/{s['subreddit']}: {s['cnt']} постов, avg score {s['avg_score']:.0f}")
    elif action == "rebuild":
        from .intelligence.migrations import migrate
        from .intelligence.rebuild import rebuild_from_snapshots

        conn = get_db(db_path)
        migrate(conn)
        snapshots_dir = _snapshots_dir(args)
        target_date = getattr(args, "date", None)
        profile = getattr(args, "profile", DEFAULT_PROFILE)

        print(f"🔄 Rebuild из {snapshots_dir}...")
        stats = rebuild_from_snapshots(conn, snapshots_dir, profile, target_date)
        conn.close()
        print(
            f"✅ Rebuild: {stats['dates']} дат, {stats['items']} items, "
            f"{stats['skipped']} пропущено"
        )
    elif action == "repair":
        from .intelligence.repair import repair_corpus_db

        conn = get_db(db_path)
        snapshots_dir = _snapshots_dir(args)
        print(f"🛠️ Repair {db_path} из локальных snapshots {snapshots_dir}...")
        stats = repair_corpus_db(conn, snapshots_dir)
        conn.close()
        print(
            "✅ Repair: "
            f"{stats['dates']} дат, "
            f"{stats['items_backfilled']} items backfilled, "
            f"{stats['snapshot_items_missing_in_db']} snapshot items missing, "
            f"{stats['runs_health_rebuilt']} runs health rebuilt, "
            f"{stats['source_health_rows']} source_health rows"
        )


# ── CLI parser ─────────────────────────────────────────────────────────────


async def _cmd_lab(args: argparse.Namespace) -> None:
    """Cluster Lab: immutable releases + experimental story/trend proposals."""
    import sqlite3

    from .intelligence.lab import (
        DEFAULT_LAB_DB_PATH,
        DEFAULT_RELEASES_DIR,
        compare,
        create_experiment,
        create_release,
        lab_db,
        list_releases,
        open_source_db_for_experiment,
        propose,
    )

    print(
        "⚠️ `lab` is a compatibility alias for one release. "
        "Use `engine`; migrate metadata with `engine legacy`."
    )
    lab_path = Path(args.lab_db) if getattr(args, "lab_db", None) else DEFAULT_LAB_DB_PATH
    lab_conn = lab_db(lab_path)
    source_db_path = (
        Path(args.source_db)
        if getattr(args, "source_db", None)
        else DEFAULT_SNAPSHOTS_DIR.parent / "compass.db"
    )

    try:
        if args.lab_group == "release" and args.lab_action == "create":
            source_conn = sqlite3.connect(f"file:{source_db_path}?mode=ro", uri=True)
            source_conn.row_factory = sqlite3.Row
            release = create_release(
                source_conn,
                lab_conn,
                source_db_path=source_db_path,
                releases_dir=(
                    Path(args.releases_dir)
                    if getattr(args, "releases_dir", None)
                    else DEFAULT_RELEASES_DIR
                ),
                profile=args.profile,
                dates=args.date,
            )
            source_conn.close()
            print(
                f"✅ Release {release.release_id}: {release.item_count} items, "
                f"profile={release.profile}, dates={','.join(release.dates)}"
            )
            return

        if args.lab_group == "release" and args.lab_action == "list":
            releases = list_releases(lab_conn)
            for release in releases:
                print(
                    f"{release.release_id}\t{release.profile}\t"
                    f"{','.join(release.dates)}\t{release.item_count} items"
                )
            if not releases:
                print("No lab releases")
            return

        if args.lab_group == "experiment" and args.lab_action == "create":
            experiment = create_experiment(
                lab_conn,
                release_id=args.release,
                method=args.method,
                prompt_version=args.prompt_version,
            )
            print(
                f"✅ Experiment {experiment.experiment_id}: "
                f"release={experiment.release_id}, method={experiment.method}"
            )
            return

        if args.lab_group == "propose":
            source_conn = open_source_db_for_experiment(lab_conn, args.experiment)
            stats = propose(
                source_conn,
                lab_conn,
                experiment_id=args.experiment,
                domain=args.domain,
                limit=args.limit,
            )
            source_conn.close()
            print(
                "✅ Proposals: "
                f"{stats.story_proposals} story, {stats.trend_proposals} trend, "
                f"{stats.candidate_pairs} pairs, {stats.selected_items}/{stats.release_items} items"
            )
            return

        if args.lab_group in {"compare", "eval"}:
            source_conn = open_source_db_for_experiment(lab_conn, args.experiment)
            result = compare(source_conn, lab_conn, experiment_id=args.experiment)
            source_conn.close()
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return

        if args.lab_group in {"review", "promote", "rollback"}:
            raise SystemExit(
                f"lab {args.lab_group} is intentionally guarded in this release. "
                "Use release/create, experiment/create, propose, compare first."
            )
    finally:
        lab_conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reddit-compass",
        description="reddit-compass — компас по трендам Reddit: сбор, поиск, виральность, разбор.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="Debug-логирование")
    common.add_argument("--config", type=str, default=None, help="Путь к config.json")
    common.add_argument("--output-dir", type=str, default=None, help="Директория для snapshots")
    common.add_argument("--limit", type=int, default=None, help="Постов на сабреддит")
    common.add_argument(
        "--time-filter", type=str, default=None, choices=["day", "week", "month", "year", "all"]
    )
    common.add_argument(
        "--stealth",
        action="store_true",
        help="Jitter пауз + exponential backoff (для ночного прогона)",
    )
    common.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать что соберётся, без записи и сетевых запросов",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", parents=[common], help="Hot/top по сабреддитам")
    sub.add_parser("search", parents=[common], help="Keyword search")
    sub.add_parser("track", parents=[common], help="Мониторинг tracked threads")
    sub.add_parser("virality", parents=[common], help="Cross-posting / виральность")

    report_p = sub.add_parser("report", parents=[common], help="Markdown-отчёт из snapshot")
    report_p.add_argument("--date", type=str, default=None, help="Дата snapshot (YYYY-MM-DD)")

    sub.add_parser(
        "all", parents=[common], help="Полный цикл: fetch + search + track + virality + report"
    )
    sub.add_parser(
        "nightly", parents=[common], help="Ночной прогон: all + trends analysis → harvests/"
    )

    p_signals = sub.add_parser(
        "signals", parents=[common], help="LLM-анализ (Qwen API): pain points, темы"
    )
    p_signals.add_argument(
        "--top",
        type=int,
        default=0,
        help="Топ-N постов по score (0 = все). Для быстрого анализа: --top 200",
    )
    sub.add_parser("radar", parents=[common], help="Trend radar: отчёт с ссылками (без LLM)")
    sub.add_parser("hn", parents=[common], help="Hacker News: AI-stories через Algolia API")
    sub.add_parser(
        "rss", parents=[common], help="RSS: BBC, Guardian, Reuters, TechCrunch, Verge, Ars"
    )
    sub.add_parser(
        "ladder", parents=[common], help="Ladder: NYT, WaPo, FT, Wired, Medium (paywall)"
    )
    sub.add_parser("ph", parents=[common], help="ProductHunt: топ продуктов (GraphQL API)")

    run_p = sub.add_parser(
        "run",
        parents=[common],
        help="Deprecated compatibility orchestrator: collect + optional engine preview",
    )
    run_p.add_argument(
        "--sources",
        type=str,
        default=None,
        help="Источники через запятую: reddit,hn,rss,ladder,ph",
    )
    run_p.add_argument("--profile", type=str, default=DEFAULT_PROFILE, help="Профиль")
    run_p.add_argument("--analyze", action="store_true", help="Запустить LLM-анализ")
    run_p.add_argument("--allow-partial", action="store_true", help="Разрешить partial run")

    collect_p = sub.add_parser(
        "collect",
        parents=[common],
        help="Collection-only: network adapters + raw corpus facts",
    )
    collect_p.add_argument(
        "--sources",
        type=str,
        default=None,
        help="Источники через запятую: reddit,hn,rss,ladder,ph",
    )
    collect_p.add_argument("--profile", type=str, default=DEFAULT_PROFILE, help="Профиль")
    collect_p.add_argument(
        "--from-snapshots",
        action="store_true",
        help=(
            "Не ходить в сеть: собрать единый raw run из уже записанных JSONL "
            "артефактов snapshots/YYYY-MM-DD"
        ),
    )
    collect_p.add_argument(
        "--date",
        type=str,
        default=None,
        help="Дата snapshot для --from-snapshots (YYYY-MM-DD; по умолчанию UTC today)",
    )

    engine_p = sub.add_parser(
        "engine",
        parents=[common],
        help="Versioned Story/Trend Engine over immutable data releases",
    )
    engine_p.add_argument("--engine-db", type=str, default=None)
    engine_sub = engine_p.add_subparsers(dest="engine_group", required=True)

    engine_release = engine_sub.add_parser("release", help="Immutable corpus releases")
    engine_release_sub = engine_release.add_subparsers(
        dest="engine_action",
        required=True,
    )
    engine_release_create = engine_release_sub.add_parser("create")
    engine_release_create.add_argument(
        "--run",
        action="append",
        required=True,
        help="Finalized collection run ID; repeat for a multi-day release",
    )
    engine_release_create.add_argument("--source-db", type=str, default=None)
    engine_release_sub.add_parser("list")
    engine_release_verify = engine_release_sub.add_parser("verify")
    engine_release_verify.add_argument("--release", required=True)

    engine_embeddings = engine_sub.add_parser(
        "embeddings",
        help="Cache local multilingual embeddings for a data release",
    )
    engine_embeddings.add_argument("--release", required=True)
    engine_embeddings.add_argument(
        "--model",
        default="intfloat/multilingual-e5-small",
    )
    engine_embeddings.add_argument("--model-revision", default="default")
    engine_embeddings.add_argument("--batch-size", type=int, default=32)

    engine_calibrate = engine_sub.add_parser(
        "calibrate",
        help="Calibrate dense-similarity thresholds for an embedding model (no labels needed)",
    )
    engine_calibrate.add_argument("--release", required=True)
    engine_calibrate.add_argument("--model", required=True)
    engine_calibrate.add_argument("--model-revision", default="default")
    engine_calibrate.add_argument(
        "--reference-model",
        default="",
        help="Transfer thresholds from this model by matching negative-score quantiles",
    )
    engine_calibrate.add_argument("--max-positive-pairs", type=int, default=3000)
    engine_calibrate.add_argument("--max-negative-pairs", type=int, default=20000)
    engine_calibrate.add_argument("--seed", type=int, default=13)

    engine_golden = engine_sub.add_parser(
        "golden",
        help="Export/import a version-scoped human review set",
    )
    engine_golden_sub = engine_golden.add_subparsers(
        dest="engine_action",
        required=True,
    )
    engine_golden_export = engine_golden_sub.add_parser("export")
    engine_golden_export.add_argument("--story-release", required=True)
    engine_golden_export.add_argument("--output", required=True)
    engine_golden_export.add_argument("--pair-limit", type=int, default=120)
    engine_golden_export.add_argument("--group-limit", type=int, default=30)
    engine_golden_export.add_argument(
        "--format",
        choices=["json", "review"],
        default="json",
        help=(
            "json: legacy pairs/groups payload for `engine golden import`. "
            "review: compact JSONL of decision='review' pairs stratified toward the "
            "auto/reject score boundary, for human/LLM labeling."
        ),
    )
    engine_golden_export.add_argument(
        "--sample",
        type=int,
        default=200,
        help="Pair count for --format review (ignored for --format json).",
    )
    engine_golden_export.add_argument(
        "--seed",
        type=int,
        default=13,
        help="Deterministic sampling seed for --format review.",
    )
    engine_golden_import = engine_golden_sub.add_parser("import")
    engine_golden_import.add_argument("--input", required=True)
    engine_golden_import.add_argument(
        "--note",
        default="",
        choices=["", "claude_review", "qwen_review", "assistant_review", "auto_label"],
        help=(
            "Label source for the whole batch; empty means human review. "
            "Priority when sources disagree: human > claude_review > qwen_review > "
            "assistant_review > auto_label."
        ),
    )
    engine_golden_import.add_argument(
        "--story-release",
        default="",
        help="Required for JSONL review labels, which carry pairs without a release id",
    )

    engine_legacy = engine_sub.add_parser(
        "legacy",
        help="Safely migrate compatible cluster_lab.db metadata",
    )
    engine_legacy.add_argument("--lab-db", default="data/cluster_lab.db")
    engine_legacy.add_argument("--source-db", default="data/compass.db")

    engine_facets = engine_sub.add_parser("facets", help="Build versioned item facets")
    engine_facets.add_argument("--release", required=True)
    engine_facets.add_argument("--profile", default=DEFAULT_PROFILE)

    engine_experiments = engine_sub.add_parser(
        "experiments",
        help="Run unpublished A/B experiments over frozen Engine releases",
    )
    engine_experiments_sub = engine_experiments.add_subparsers(
        dest="engine_action",
        required=True,
    )
    engine_experiments_compare = engine_experiments_sub.add_parser("compare")
    engine_experiments_compare.add_argument("--facet-release", required=True)
    engine_experiments_compare.add_argument("--limit", type=int, default=300)
    engine_experiments_compare.add_argument("--domain", default=None)
    engine_experiments_compare.add_argument("--sample-limit", type=int, default=5)
    engine_experiments_compare.add_argument(
        "--embedding-model",
        default=LEXICAL_HASH_EMBEDDING_MODEL,
    )
    engine_experiments_compare.add_argument("--embedding-revision", default="default")
    engine_experiments_compare.add_argument("--dense-top-k", type=int, default=24)
    engine_experiments_compare.add_argument("--dense-threshold", type=float, default=0.55)
    engine_experiments_compare.add_argument("--auto-merge-threshold", type=float, default=0.82)
    engine_experiments_compare.add_argument("--review-threshold", type=float, default=0.55)
    engine_experiments_compare.add_argument("--semantic-dedup-threshold", type=float, default=0.92)
    engine_experiments_compare.add_argument("--semantic-dedup-max-days", type=int, default=7)
    engine_experiments_compare.add_argument(
        "--near-duplicate-max-bucket-size",
        type=int,
        default=40,
    )
    engine_experiments_compare.add_argument(
        "--near-duplicate-simhash-distance",
        type=int,
        default=18,
    )
    engine_experiments_compare.add_argument(
        "--near-duplicate-shingle-jaccard",
        type=float,
        default=0.34,
    )

    engine_diagnose = engine_sub.add_parser(
        "diagnose",
        help="Explain current frozen release, story undermerge and trend readiness",
    )
    engine_diagnose.add_argument("--release", default=None)
    engine_diagnose.add_argument("--story-release", default=None)
    engine_diagnose.add_argument("--trend-release", default=None)
    engine_diagnose.add_argument("--limit", type=int, default=10)

    engine_stories = engine_sub.add_parser("stories", help="Story release operations")
    engine_stories_sub = engine_stories.add_subparsers(
        dest="engine_action",
        required=True,
    )
    engine_stories_candidates = engine_stories_sub.add_parser(
        "candidates",
        help="Export scored Story Engine pair candidates without saving a release",
    )
    engine_stories_candidates.add_argument("--facet-release", required=True)
    engine_stories_candidates.add_argument("--limit", type=int, default=300)
    engine_stories_candidates.add_argument("--domain", default=None)
    engine_stories_candidates.add_argument("--candidate-limit", type=int, default=0)
    engine_stories_candidates.add_argument("--output", default="")
    engine_stories_candidates.add_argument(
        "--embedding-model",
        default="intfloat/multilingual-e5-small",
        help=(
            "Embedding model hash to read for dense retrieval. Use "
            f"{LEXICAL_HASH_EMBEDDING_MODEL} for dependency-free VPS runs."
        ),
    )
    engine_stories_candidates.add_argument("--embedding-revision", default="default")
    engine_stories_candidates.add_argument("--dense-top-k", type=int, default=16)
    engine_stories_candidates.add_argument("--dense-threshold", type=float, default=0.62)
    engine_stories_candidates.add_argument("--auto-merge-threshold", type=float, default=0.82)
    engine_stories_candidates.add_argument("--review-threshold", type=float, default=0.58)
    engine_stories_candidates.add_argument(
        "--no-near-duplicates",
        action="store_true",
        help="Disable SimHash/MinHash-style near-duplicate candidate generation.",
    )
    engine_stories_candidates.add_argument(
        "--near-duplicate-max-bucket-size",
        type=int,
        default=40,
    )
    engine_stories_candidates.add_argument(
        "--near-duplicate-simhash-distance",
        type=int,
        default=18,
    )
    engine_stories_candidates.add_argument(
        "--near-duplicate-shingle-jaccard",
        type=float,
        default=0.34,
    )
    engine_stories_candidates.add_argument(
        "--semantic-dedup",
        action="store_true",
        help="Enable guarded semantic embedding auto-merge for dense candidates.",
    )
    engine_stories_candidates.add_argument("--semantic-dedup-threshold", type=float, default=0.92)
    engine_stories_candidates.add_argument("--semantic-dedup-max-days", type=int, default=7)
    engine_stories_propose = engine_stories_sub.add_parser("propose")
    engine_stories_propose.add_argument("--facet-release", required=True)
    engine_stories_propose.add_argument("--limit", type=int, default=0)
    engine_stories_propose.add_argument("--domain", default=None)
    engine_stories_propose.add_argument(
        "--embedding-model",
        default="intfloat/multilingual-e5-small",
        help=(
            "Embedding model hash to read for dense retrieval. Use "
            f"{LEXICAL_HASH_EMBEDDING_MODEL} for dependency-free VPS runs."
        ),
    )
    engine_stories_propose.add_argument("--embedding-revision", default="default")
    engine_stories_propose.add_argument("--dense-top-k", type=int, default=16)
    # default=None — не задан флаг, значит порог берётся из профиля модели эмбеддингов
    # (embeddings.DENSE_THRESHOLD_PROFILES). Жёсткий дефолт здесь молча перебивал бы его.
    engine_stories_propose.add_argument(
        "--dense-threshold",
        type=float,
        default=None,
        help="Override dense candidate threshold; default comes from the embedding profile",
    )
    engine_stories_propose.add_argument("--auto-merge-threshold", type=float, default=None)
    engine_stories_propose.add_argument("--review-threshold", type=float, default=None)
    # Стадия была доступна только внутри `engine cycle`, поэтому её нельзя было прогнать
    # отдельно на готовом facet-релизе — а именно от неё зависит, берутся ли полы полноты.
    engine_stories_propose.add_argument(
        "--cross-encoder",
        action="store_true",
        help=(
            "Разобрать серую зону готовым cross-encoder'ом. Без стадии полы полноты не "
            "берутся (замер: 50.6 multi/1k при поле 65). Требует reddit-compass[adjudicate]."
        ),
    )
    engine_stories_propose.add_argument(
        "--cross-encoder-threshold",
        type=float,
        default=DEFAULT_CROSS_ENCODER_THRESHOLD,
    )
    engine_stories_propose.add_argument(
        "--no-near-duplicates",
        action="store_true",
        help="Disable SimHash/MinHash-style near-duplicate candidate generation.",
    )
    engine_stories_propose.add_argument("--near-duplicate-max-bucket-size", type=int, default=40)
    engine_stories_propose.add_argument("--near-duplicate-simhash-distance", type=int, default=18)
    engine_stories_propose.add_argument(
        "--near-duplicate-shingle-jaccard",
        type=float,
        default=0.34,
    )
    engine_stories_propose.add_argument(
        "--semantic-dedup",
        action="store_true",
        help="Enable guarded semantic embedding auto-merge for dense candidates.",
    )
    engine_stories_propose.add_argument("--semantic-dedup-threshold", type=float, default=None)
    engine_stories_propose.add_argument("--semantic-dedup-max-days", type=int, default=7)
    engine_stories_inspect = engine_stories_sub.add_parser("inspect")
    engine_stories_inspect.add_argument("--story-release", required=True)
    engine_stories_inspect.add_argument("--limit", type=int, default=20)
    engine_stories_verified = engine_stories_sub.add_parser(
        "verified", help="List verified stories by provenance"
    )
    engine_stories_verified.add_argument("--story-release", required=True)
    engine_stories_verified.add_argument("--signal-release", default=None)
    engine_stories_verified.add_argument("--limit", type=int, default=50)
    engine_stories_eval = engine_stories_sub.add_parser("eval")
    engine_stories_eval.add_argument("--story-release", required=True)
    engine_stories_review = engine_stories_sub.add_parser(
        "review",
        help="Review only ambiguous pairs with Qwen and cache strict JSON decisions",
    )
    engine_stories_review.add_argument("--story-release", required=True)
    engine_stories_review.add_argument("--limit", type=int, default=100)
    engine_stories_review.add_argument("--model", default="qwen3.6-flash")

    # Типизация акторов разнесена на два шага, потому что цикл идёт на VPS, а
    # опциональная зависимость [actors] в прод-образ намеренно не входит: VPS отдаёт
    # заголовки (`export-titles`, чистая операция), Mac считает типы (`type`, нужен
    # GLiNER) и кладёт таблицу обратно в /data. См. scripts/fetch-and-sync.sh.
    # Извлечение схемы LLM отделено от построения релиза намеренно: это долгая сетевая
    # стадия, а `trends propose` обязан оставаться быстрым и воспроизводимым. В релиз
    # едет кэш, а не модель.
    engine_schemas = engine_sub.add_parser("schemas", help="LLM event-schema cache for schema_v3")
    engine_schemas_sub = engine_schemas.add_subparsers(dest="engine_action", required=True)
    engine_schemas_extract = engine_schemas_sub.add_parser(
        "extract",
        help="Прогреть кэш извлечения по заголовкам story-релиза (нужен ключ Qwen)",
    )
    engine_schemas_extract.add_argument("--story-release", required=True)
    engine_schemas_extract.add_argument("--model", default=DEFAULT_EXTRACT_MODEL)
    engine_schemas_extract.add_argument("--batch-size", type=int, default=EXTRACT_BATCH)
    engine_schemas_extract.add_argument(
        "--concurrency",
        type=int,
        default=EXTRACT_CONCURRENCY,
        help="Сколько батчей держать в полёте одновременно",
    )
    engine_schemas_extract.add_argument(
        "--limit", type=int, default=0, help="Ограничить число заголовков (0 — все)"
    )
    engine_schemas_stats = engine_schemas_sub.add_parser(
        "stats", help="Что уже в кэше: доля событий и доля `other`"
    )
    engine_schemas_stats.add_argument("--story-release", required=True)

    engine_actors = engine_sub.add_parser("actors", help="Actor typing for schema_v2 depth 3")
    engine_actors_sub = engine_actors.add_subparsers(dest="engine_action", required=True)
    engine_actors_export = engine_actors_sub.add_parser(
        "export-titles",
        help="Заголовки story-релиза одним JSON-массивом; зависимостей не требует",
    )
    engine_actors_export.add_argument("--story-release", required=True)
    engine_actors_type = engine_actors_sub.add_parser(
        "type",
        help="Посчитать типы акторов по заголовкам (требует reddit-compass[actors])",
    )
    engine_actors_type.add_argument(
        "--titles",
        required=True,
        help="Файл с JSON-массивом заголовков; «-» — читать stdin",
    )
    engine_actors_type.add_argument("--out", required=True, help="Куда записать actor_types.json")
    engine_actors_type.add_argument("--model", default=DEFAULT_ACTOR_MODEL)
    engine_actors_type.add_argument("--threshold", type=float, default=DEFAULT_ACTOR_THRESHOLD)

    engine_trends = engine_sub.add_parser("trends", help="Trend release operations")
    engine_trends_sub = engine_trends.add_subparsers(
        dest="engine_action",
        required=True,
    )
    engine_trends_propose = engine_trends_sub.add_parser("propose")
    engine_trends_propose.add_argument("--story-release", required=True)
    engine_trends_propose.add_argument("--window", default="30d")
    engine_trends_propose.add_argument(
        "--method",
        default=DEFAULT_TREND_METHOD,
        choices=["story_graph_v1", "embedding_v2", "schema_v2", "schema_v3"],
        help=(
            "Trend discovery method. schema_v3 берёт действие из LLM-извлечения "
            "(нужен прогретый `engine schemas extract`), schema_v2 — из лексикона."
        ),
    )
    engine_trends_propose.add_argument("--top-k", type=int, default=12)
    engine_trends_propose.add_argument("--edge-threshold", type=float, default=0.45)
    engine_trends_propose.add_argument("--medoid-threshold", type=float, default=0.4)
    engine_trends_propose.add_argument("--max-feature-df", type=int, default=0)
    engine_trends_propose.add_argument("--max-candidate-pairs", type=int, default=150_000)
    engine_trends_propose.add_argument(
        "--trend-depth",
        type=int,
        default=2,
        choices=[2, 3],
        help=(
            "Компонентов в схемном ключе (только schema_v2): 2 = (действие, домен), "
            "3 = + тип актора. На 3 нужна таблица actor_types.json, иначе глубина "
            "деградирует до 2 с предупреждением."
        ),
    )
    engine_trends_propose.add_argument(
        "--actor-types",
        help="Путь к actor_types.json; по умолчанию $DATA_DIR/actor_types.json",
    )
    engine_trends_propose.add_argument(
        "--verified-only",
        action="store_true",
        default=False,
        help="Only use verified stories for trend discovery",
    )
    engine_trends_propose.add_argument(
        "--signal-release",
        default=None,
        help="Signal release ID for community_only verification",
    )
    engine_trends_inspect = engine_trends_sub.add_parser("inspect")
    engine_trends_inspect.add_argument("--trend-release", required=True)
    engine_trends_inspect.add_argument("--limit", type=int, default=20)
    engine_trends_eval = engine_trends_sub.add_parser("eval")
    engine_trends_eval.add_argument("--trend-release", required=True)
    engine_trends_review = engine_trends_sub.add_parser(
        "review",
        help="Validate candidate trends with Qwen Max and cache strict JSON decisions",
    )
    engine_trends_review.add_argument("--trend-release", required=True)
    engine_trends_review.add_argument("--limit", type=int, default=50)
    engine_trends_review.add_argument("--model", default="qwen3.8-max-preview")

    engine_pulse = engine_sub.add_parser("reddit-pulse", help="Reddit Pulse signal operations")
    engine_pulse_sub = engine_pulse.add_subparsers(dest="pulse_action", required=True)
    engine_pulse_propose = engine_pulse_sub.add_parser("propose")
    engine_pulse_propose.add_argument("--release", required=True)
    engine_pulse_propose.add_argument("--date", required=True)
    engine_pulse_propose.add_argument("--profile", default="broad")
    engine_pulse_propose.add_argument(
        "--story-release",
        default=None,
        help="Optional StoryRelease for linked_story_id and mainstream coverage.",
    )
    engine_pulse_propose.add_argument(
        "--facet-release",
        default=None,
        help="Optional FacetRelease metadata when no story release is provided.",
    )
    engine_pulse_propose.add_argument("--history-window-days", type=int, default=7)
    engine_pulse_propose.add_argument("--method-version", default="reddit_pulse_v2")
    engine_pulse_propose.add_argument(
        "--signal-release-id",
        default=None,
        help="Optional explicit ID for deterministic tests/backfills.",
    )
    engine_pulse_inspect = engine_pulse_sub.add_parser("inspect")
    engine_pulse_inspect.add_argument("--signal-release", required=True)
    engine_pulse_inspect.add_argument("--limit", type=int, default=50)
    engine_pulse_inspect.add_argument("--signal-type", default=None)
    engine_pulse_inspect.add_argument("--subreddit", default=None)

    engine_label = engine_sub.add_parser("label", help="Add version-scoped manual label")
    engine_label.add_argument(
        "label_action",
        nargs="?",
        choices=["active", "auto", "train"],
        help=(
            "active: interactive pair labeling; auto: deterministic high-confidence "
            "auto-labels (no human); train: learn merge model from labels."
        ),
    )
    engine_label.add_argument(
        "--kind",
        choices=["story_pair", "story", "trend"],
    )
    engine_label.add_argument("--target", default="")
    engine_label.add_argument(
        "--target-precision",
        default="0.95",
        help="Target precision for `train` threshold calibration.",
    )
    engine_label.add_argument("--release", default="")
    engine_label.add_argument("--story-release", default="")
    engine_label.add_argument(
        "--label",
        choices=[
            "same_story",
            "different_story",
            "overmerge",
            "undermerge",
            "low_signal",
            "useful_trend",
            "useless_trend",
        ],
    )
    engine_label.add_argument("--note", default="")

    engine_cycle = engine_sub.add_parser(
        "cycle",
        help="Full nightly cycle: release → stories → labels → train → trends → pulse → publish",
    )
    engine_cycle.add_argument("--profile", default="broad")
    engine_cycle.add_argument("--window", type=int, default=7)
    engine_cycle.add_argument(
        "--trend-method",
        default="embedding_v2",
        choices=["story_graph_v1", "embedding_v2", "schema_v2", "schema_v3"],
    )
    engine_cycle.add_argument(
        "--trend-depth",
        type=int,
        default=2,
        choices=[2, 3],
        help="Компонентов в схемном ключе; см. `engine trends propose --trend-depth`.",
    )
    engine_cycle.add_argument(
        "--embed-model",
        default="minishlab/potion-base-8M",
        help="Embedding model for embedding_v2 (model2vec, torch-free). Empty = no embeddings.",
    )
    engine_cycle.add_argument("--review-model", default="qwen3.6-flash")
    engine_cycle.add_argument(
        "--cross-encoder",
        action="store_true",
        help=(
            "Разобрать серую зону готовым cross-encoder'ом вместо построчного LLM-ревью. "
            "Без этой стадии полы полноты не берутся (51.9 multi/1k при поле 65). "
            "Требует reddit-compass[engine]."
        ),
    )
    engine_cycle.add_argument(
        "--cross-encoder-threshold",
        type=float,
        default=0.95,
        help="Порог слияния; 0.95 — precision-first точка с запасом по всем полам.",
    )
    engine_cycle.add_argument(
        "--review-limit",
        type=int,
        default=0,
        help="Qwen-adjudicate up to N gray-zone pairs (0 = skip, deterministic only).",
    )
    engine_cycle.add_argument(
        "--trend-review-limit",
        type=int,
        default=0,
        help="Qwen-review up to N trend candidates and materialize confirmed status (0 = skip).",
    )
    engine_cycle.add_argument(
        "--trend-review-model",
        default="qwen3.8-max-preview",
        help="Qwen model for final bounded trend review.",
    )
    engine_cycle.add_argument(
        "--publish-channel",
        default="",
        help="Publish to this channel after the cycle (e.g. shadow). Empty = no publish.",
    )
    engine_cycle.add_argument("--allow-partial", action="store_true")
    engine_cycle.add_argument("--no-pulse", action="store_true", help="Skip Reddit Pulse step.")

    engine_quality = engine_sub.add_parser(
        "quality",
        help="Compute quality metrics for a release; report / snapshot / check against baseline",
    )
    engine_quality.add_argument("quality_action", choices=["report", "check", "snapshot"])
    engine_quality.add_argument(
        "--channel", default="shadow", help="Resolve releases from this channel."
    )
    engine_quality.add_argument("--data-release", default="")
    engine_quality.add_argument("--story-release", default="")
    engine_quality.add_argument("--trend-release", default="")
    engine_quality.add_argument("--signal-release", default="")
    engine_quality.add_argument(
        "--baseline",
        default="config/quality_baselines.json",
        help="Baseline snapshot for `check` (regression detection).",
    )
    engine_quality.add_argument(
        "--out",
        default="config/quality_baselines.json",
        help="Where `snapshot` writes the baseline.",
    )

    engine_publish = engine_sub.add_parser("publish", help="Publish immutable Radar version")
    engine_publish.add_argument("--story-release", required=True)
    engine_publish.add_argument("--trend-release", required=True)
    engine_publish.add_argument("--channel", default="broad")
    engine_publish.add_argument("--allow-partial", action="store_true")
    engine_publish.add_argument(
        "--force",
        action="store_true",
        help="Bypass quality gate checks for production channels",
    )

    engine_rollback = engine_sub.add_parser("rollback", help="Switch channel pointer")
    engine_rollback.add_argument("--channel", default="broad")
    engine_rollback.add_argument("--to", required=True, help="Publication ID")

    engine_publications = engine_sub.add_parser("publications")
    engine_publications.add_argument("--channel", default=None)
    engine_compare = engine_sub.add_parser("compare")
    engine_compare.add_argument("--left", required=True)
    engine_compare.add_argument("--right", required=True)

    sub.add_parser("serve", parents=[common], help="Запуск REST API (FastAPI/uvicorn)")

    version_p = sub.add_parser(
        "version", parents=[common], help="Реестр версий: код, статика, схемы, данные"
    )
    version_p.add_argument("--engine-db", default="")
    version_p.add_argument(
        "--record",
        action="store_true",
        help="Зафиксировать текущие версии кода и статики в реестре (вызывается деплоем)",
    )

    db_p = sub.add_parser("db", parents=[common], help="SQLite: init / stats / rebuild / repair")
    db_p.add_argument(
        "db_action",
        choices=["init", "stats", "rebuild", "repair"],
        help="Действие с БД",
    )
    db_p.add_argument("--date", type=str, default=None, help="Дата для rebuild (YYYY-MM-DD)")
    db_p.add_argument("--profile", type=str, default=DEFAULT_PROFILE, help="Профиль для rebuild")
    db_p.add_argument(
        "--source-db",
        type=str,
        default=None,
        help="Путь к compass.db для init/stats/rebuild/repair",
    )

    lab_p = sub.add_parser(
        "lab",
        parents=[common],
        help="Cluster Lab: immutable data releases + experimental story/trend proposals",
    )
    lab_p.add_argument("--lab-db", type=str, default=None, help="Путь к cluster_lab.db")
    lab_sub = lab_p.add_subparsers(dest="lab_group", required=True)

    lab_release_p = lab_sub.add_parser("release", help="Data release operations")
    lab_release_sub = lab_release_p.add_subparsers(dest="lab_action", required=True)
    lab_release_create = lab_release_sub.add_parser("create", help="Create immutable data release")
    lab_release_create.add_argument(
        "--date",
        action="append",
        required=True,
        help="Дата run для release; можно указать несколько раз",
    )
    lab_release_create.add_argument("--profile", type=str, default=DEFAULT_PROFILE)
    lab_release_create.add_argument("--source-db", type=str, default=None)
    lab_release_create.add_argument("--releases-dir", type=str, default=None)
    lab_release_sub.add_parser("list", help="List data releases")

    lab_experiment_p = lab_sub.add_parser("experiment", help="Experiment operations")
    lab_experiment_sub = lab_experiment_p.add_subparsers(dest="lab_action", required=True)
    lab_experiment_create = lab_experiment_sub.add_parser("create", help="Create experiment")
    lab_experiment_create.add_argument("--release", required=True, help="Release ID")
    lab_experiment_create.add_argument("--method", default="hybrid_v1")
    lab_experiment_create.add_argument("--prompt-version", default="")

    lab_propose_p = lab_sub.add_parser("propose", help="Build heuristic proposals")
    lab_propose_p.add_argument("--experiment", required=True)
    lab_propose_p.add_argument("--domain", default=None)
    lab_propose_p.add_argument("--limit", type=int, default=150)

    lab_compare_p = lab_sub.add_parser("compare", help="Compare current vs proposed")
    lab_compare_p.add_argument("--experiment", required=True)
    lab_eval_p = lab_sub.add_parser("eval", help="Alias for compare until eval cases exist")
    lab_eval_p.add_argument("--experiment", required=True)
    lab_review_p = lab_sub.add_parser("review", help="Guarded LLM review placeholder")
    lab_review_p.add_argument("--experiment", required=True)
    lab_review_p.add_argument("--with-llm", action="store_true")
    lab_promote_p = lab_sub.add_parser("promote", help="Guarded promotion placeholder")
    lab_promote_p.add_argument("--experiment", required=True)
    lab_rollback_p = lab_sub.add_parser("rollback", help="Guarded rollback placeholder")
    lab_rollback_p.add_argument("--promotion", required=True)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _setup_logging(args.verbose)

    handlers = {
        "fetch": _cmd_fetch,
        "search": _cmd_search,
        "track": _cmd_track,
        "virality": _cmd_virality,
        "report": _cmd_report,
        "all": _cmd_all,
        "nightly": _cmd_nightly,
        "signals": _cmd_signals,
        "radar": _cmd_radar,
        "hn": _cmd_hn,
        "rss": _cmd_rss,
        "ladder": _cmd_ladder,
        "ph": _cmd_ph,
        "collect": _cmd_collect,
        "run": _cmd_run,
        "engine": _cmd_engine,
        "serve": _cmd_serve,
        "version": _cmd_version,
        "db": _cmd_db,
        "lab": _cmd_lab,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "serve":
            # uvicorn управляет event loop сам — без asyncio.run()
            _cmd_serve_sync(args)
        else:
            asyncio.run(handler(args))
    except KeyboardInterrupt:
        print("\n⏹ Прервано пользователем.")
        sys.exit(130)
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
