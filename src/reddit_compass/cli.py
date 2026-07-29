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
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .config import DEFAULT_HARVESTS_DIR, DEFAULT_PROFILE, DEFAULT_SNAPSHOTS_DIR, MonitorConfig
from .detect_virality import detect_virality
from .export import render_trends_report, write_snapshot, write_trends_report
from .fetch_subreddits import fetch_all_subreddits
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
    from .collector import collect_sources

    config = _load_config(args)
    snapshots_dir = _snapshots_dir(args)
    db_path = DEFAULT_SNAPSHOTS_DIR.parent / "compass.db"
    sources = None
    if args.sources:
        sources = [s.strip() for s in args.sources.split(",")]
    profile = getattr(args, "profile", DEFAULT_PROFILE)
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


async def _cmd_collect(args: argparse.Namespace) -> None:
    """Collection-only process: network + raw corpus persistence."""
    result = await _execute_collection(args)
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


async def _cmd_engine(args: argparse.Namespace) -> None:
    """Versioned Story/Trend Engine; never mutates compass.db."""
    from dataclasses import asdict

    from .intelligence.engine import (
        DEFAULT_ENGINE_DB_PATH,
        cache_release_embeddings,
        compare_engine_versions,
        create_data_release,
        create_facet_release,
        create_story_release,
        create_trend_release,
        engine_db,
        evaluate_story_release,
        evaluate_trend_release,
        export_golden_candidates,
        import_golden_labels,
        import_legacy_lab,
        inspect_story_release,
        inspect_trend_release,
        label_engine_target,
        list_data_releases,
        list_publications,
        open_corpus_readonly,
        prepare_story_review_jobs,
        prepare_trend_review_jobs,
        publish_radar,
        rollback_publication,
        store_story_review_response,
        store_trend_review_response,
        verify_data_release,
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
        if args.engine_group == "golden":
            if args.engine_action == "export":
                payload = export_golden_candidates(
                    engine_conn,
                    args.story_release,
                    pair_limit=args.pair_limit,
                    group_limit=args.group_limit,
                )
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
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
                payload_raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
                if not isinstance(payload_raw, dict):
                    raise ValueError("Golden Set root must be a JSON object")
                result = import_golden_labels(engine_conn, payload_raw)
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
        if args.engine_group == "stories":
            if args.engine_action == "propose":
                story_release_output = create_story_release(
                    engine_conn,
                    facet_release_id=args.facet_release,
                    limit=args.limit,
                    domain=args.domain,
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
        if args.engine_group == "trends":
            if args.engine_action == "propose":
                trend_release_output = create_trend_release(
                    engine_conn,
                    story_release_id=args.story_release,
                    window=args.window,
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
                results = []
                for job in jobs:
                    raw_response = await call_qwen_json(
                        str(job["prompt"]),
                        model=args.model,
                    )
                    results.append(
                        store_trend_review_response(
                            engine_conn,
                            target_id=str(job["target_id"]),
                            input_hash=str(job["input_hash"]),
                            raw_response=raw_response,
                            allowed_story_ids={str(story_id) for story_id in job["story_ids"]},
                            model=args.model,
                            prompt_version=str(job["prompt_version"]),
                        )
                    )
                print(
                    json.dumps(
                        {
                            "trend_release_id": args.trend_release,
                            "reviewed": len(results),
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
        if args.engine_group == "label":
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
        if args.engine_group == "publish":
            publication = publish_radar(
                engine_conn,
                story_release_id=args.story_release,
                trend_release_id=args.trend_release,
                channel=args.channel,
                allow_partial=args.allow_partial,
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

    db_path = DEFAULT_SNAPSHOTS_DIR.parent / "compass.db"
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
    engine_golden_import = engine_golden_sub.add_parser("import")
    engine_golden_import.add_argument("--input", required=True)

    engine_legacy = engine_sub.add_parser(
        "legacy",
        help="Safely migrate compatible cluster_lab.db metadata",
    )
    engine_legacy.add_argument("--lab-db", default="data/cluster_lab.db")
    engine_legacy.add_argument("--source-db", default="data/compass.db")

    engine_facets = engine_sub.add_parser("facets", help="Build versioned item facets")
    engine_facets.add_argument("--release", required=True)
    engine_facets.add_argument("--profile", default=DEFAULT_PROFILE)

    engine_stories = engine_sub.add_parser("stories", help="Story release operations")
    engine_stories_sub = engine_stories.add_subparsers(
        dest="engine_action",
        required=True,
    )
    engine_stories_propose = engine_stories_sub.add_parser("propose")
    engine_stories_propose.add_argument("--facet-release", required=True)
    engine_stories_propose.add_argument("--limit", type=int, default=0)
    engine_stories_propose.add_argument("--domain", default=None)
    engine_stories_inspect = engine_stories_sub.add_parser("inspect")
    engine_stories_inspect.add_argument("--story-release", required=True)
    engine_stories_inspect.add_argument("--limit", type=int, default=20)
    engine_stories_eval = engine_stories_sub.add_parser("eval")
    engine_stories_eval.add_argument("--story-release", required=True)
    engine_stories_review = engine_stories_sub.add_parser(
        "review",
        help="Review only ambiguous pairs with Qwen and cache strict JSON decisions",
    )
    engine_stories_review.add_argument("--story-release", required=True)
    engine_stories_review.add_argument("--limit", type=int, default=100)
    engine_stories_review.add_argument("--model", default="qwen-plus")

    engine_trends = engine_sub.add_parser("trends", help="Trend release operations")
    engine_trends_sub = engine_trends.add_subparsers(
        dest="engine_action",
        required=True,
    )
    engine_trends_propose = engine_trends_sub.add_parser("propose")
    engine_trends_propose.add_argument("--story-release", required=True)
    engine_trends_propose.add_argument("--window", default="30d")
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
    engine_trends_review.add_argument("--model", default="qwen-max")

    engine_label = engine_sub.add_parser("label", help="Add version-scoped manual label")
    engine_label.add_argument(
        "--kind",
        required=True,
        choices=["story_pair", "story", "trend"],
    )
    engine_label.add_argument("--target", required=True)
    engine_label.add_argument("--release", required=True)
    engine_label.add_argument(
        "--label",
        required=True,
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

    engine_publish = engine_sub.add_parser("publish", help="Publish immutable Radar version")
    engine_publish.add_argument("--story-release", required=True)
    engine_publish.add_argument("--trend-release", required=True)
    engine_publish.add_argument("--channel", default="broad")
    engine_publish.add_argument("--allow-partial", action="store_true")

    engine_rollback = engine_sub.add_parser("rollback", help="Switch channel pointer")
    engine_rollback.add_argument("--channel", default="broad")
    engine_rollback.add_argument("--to", required=True, help="Publication ID")

    engine_publications = engine_sub.add_parser("publications")
    engine_publications.add_argument("--channel", default=None)
    engine_compare = engine_sub.add_parser("compare")
    engine_compare.add_argument("--left", required=True)
    engine_compare.add_argument("--right", required=True)

    sub.add_parser("serve", parents=[common], help="Запуск REST API (FastAPI/uvicorn)")

    db_p = sub.add_parser("db", parents=[common], help="SQLite: init / stats / rebuild")
    db_p.add_argument("db_action", choices=["init", "stats", "rebuild"], help="Действие с БД")
    db_p.add_argument("--date", type=str, default=None, help="Дата для rebuild (YYYY-MM-DD)")
    db_p.add_argument("--profile", type=str, default=DEFAULT_PROFILE, help="Профиль для rebuild")

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
