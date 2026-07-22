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
import logging
import sys
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


async def _cmd_fetch(args: argparse.Namespace) -> None:
    config = _load_config(args)
    snapshot_date = _today()
    cards = await fetch_all_subreddits(config, snapshot_date)
    snap_dir = _snapshots_dir(args) / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    from .export import write_posts_jsonl

    write_posts_jsonl(cards, snap_dir / "posts.jsonl")
    print(f"✅ Fetch: {len(cards)} постов → {snap_dir / 'posts.jsonl'}")


async def _cmd_search(args: argparse.Namespace) -> None:
    config = _load_config(args)
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
    snapshot_date = _today()
    output_dir = _snapshots_dir(args)
    state_file = output_dir.parent / "tracked-threads-state.jsonl"

    logger.info("=== Fetch: hot/top по сабреддитам ===")
    cards = await fetch_all_subreddits(config, snapshot_date)

    logger.info("=== Search: keyword search ===")
    search_cards = await search_all_keywords(config, snapshot_date)

    logger.info("=== Track: мониторинг тредов ===")
    thread_states = await track_all_threads(config, snapshot_date, state_file)

    all_cards = cards + search_cards

    logger.info("=== Virality: детекция виральности ===")
    signals = detect_virality(all_cards, config, snapshot_date)

    logger.info("=== Export: запись snapshot ===")
    snap_dir = write_snapshot(
        output_dir, snapshot_date, all_cards, thread_states, signals, config.subreddit_clusters
    )

    from .export import write_threads_jsonl

    write_threads_jsonl(thread_states, state_file)

    print(f"\n{'=' * 60}")
    print(f"✅ reddit-compass — snapshot {snapshot_date}")
    print(f"{'=' * 60}")
    print(f"  Постов (fetch):       {len(cards)}")
    print(f"  Постов (search):      {len(search_cards)}")
    print(f"  Tracked threads:      {len(thread_states)}")
    print(f"  Virality signals:     {len(signals)}")
    print(f"  Snapshot:             {snap_dir}")
    print(f"  Отчёт:                {snap_dir / 'trends-report.md'}")
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


async def _cmd_serve(args: argparse.Namespace) -> None:
    """Запуск REST API (FastAPI/uvicorn)."""
    import uvicorn

    from .api.app import create_app

    app = create_app()
    print("🚀 reddit-compass API: http://127.0.0.1:8900")
    print("   Docs: http://127.0.0.1:8900/docs")
    uvicorn.run(app, host="127.0.0.1", port=8900, log_level="info")


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
        "serve": _cmd_serve,
        "db": _cmd_db,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        asyncio.run(handler(args))
    except KeyboardInterrupt:
        print("\n⏹ Прервано пользователем.")
        sys.exit(130)
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
