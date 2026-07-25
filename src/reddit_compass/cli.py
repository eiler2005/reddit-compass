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

from .config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_HARVESTS_DIR,
    DEFAULT_SNAPSHOTS_DIR,
    MonitorConfig,
)
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
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    config = MonitorConfig.from_file(config_path)
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
        print(f"   Топ-темы: {', '.join(synthesis.top_themes[:3])}")

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
    """SQLite: init / stats."""
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


# ── CLI parser ─────────────────────────────────────────────────────────────


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
    sub.add_parser("serve", parents=[common], help="Запуск REST API (FastAPI/uvicorn)")

    db_p = sub.add_parser("db", parents=[common], help="SQLite: init / stats")
    db_p.add_argument("db_action", choices=["init", "stats"], help="Действие с БД")

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
        "serve": _cmd_serve,
        "db": _cmd_db,
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
