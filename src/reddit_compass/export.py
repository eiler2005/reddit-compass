"""Экспорт данных: JSONL-файлы + Markdown-отчёт о трендах."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from pathlib import Path

from .models import PostCard, TrackedThreadState, ViralitySignal

logger = logging.getLogger("reddit_compass")


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


# ── JSONL writers ──────────────────────────────────────────────────────────


def _write_lines_atomically(path: Path, lines: list[str]) -> None:
    """Записать файл целиком или не тронуть прежний.

    ``mode="w"`` усекает файл в момент открытия, поэтому падение адаптера на середине
    записи оставляло дневной артефакт обрезанным, а отказ ещё до первой строки — пустым.
    Пустой артефакт неотличим от честно пустого дня, и день уходил в релиз как собранный.
    """
    _ensure_dir(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_posts_jsonl(cards: list[PostCard], path: Path) -> int:
    _write_lines_atomically(path, [card.to_json() for card in cards])
    logger.info("Записано %d постов → %s", len(cards), path)
    return len(cards)


def write_threads_jsonl(states: list[TrackedThreadState], path: Path) -> int:
    _ensure_dir(path)
    with path.open("w", encoding="utf-8") as f:
        for state in states:
            f.write(state.to_json() + "\n")
    logger.info("Записано %d тредов → %s", len(states), path)
    return len(states)


def write_virality_jsonl(signals: list[ViralitySignal], path: Path) -> int:
    _ensure_dir(path)
    with path.open("w", encoding="utf-8") as f:
        for sig in signals:
            f.write(sig.to_json() + "\n")
    logger.info("Записано %d сигналов → %s", len(signals), path)
    return len(signals)


# ── Markdown report ────────────────────────────────────────────────────────


def _format_score(score: int) -> str:
    if score >= 1000:
        return f"{score / 1000:.1f}k"
    return str(score)


def _truncate(text: str, max_len: int = 200) -> str:
    text = text.strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"


def render_trends_report(
    cards: list[PostCard],
    thread_states: list[TrackedThreadState],
    virality_signals: list[ViralitySignal],
    snapshot_date: str,
    subreddit_clusters: dict[str, list[str]] | None = None,
) -> str:
    """Рендерит Markdown-отчёт о трендах."""
    lines: list[str] = []
    lines.append(f"# Reddit Trends Report — {snapshot_date}")
    lines.append("")
    lines.append(
        f"> Собрано {len(cards)} постов, "
        f"{len(thread_states)} отслеживаемых тредов, "
        f"{len(virality_signals)} сигналов виральности."
    )
    lines.append("")

    # ── Hot & Rising по кластерам ──
    lines.append("---")
    lines.append("")
    lines.append("## 🔥 Hot & Rising по сабреддитам")
    lines.append("")

    by_subreddit: dict[str, list[PostCard]] = defaultdict(list)
    for card in cards:
        if card.monitoring_type in ("hot", "rising", "top"):
            by_subreddit[card.subreddit.lower()].append(card)

    cluster_names = subreddit_clusters or {}
    sub_to_cluster: dict[str, str] = {}
    for cluster, subs in cluster_names.items():
        for s in subs:
            sub_to_cluster[s.lower()] = cluster

    clusters_seen: dict[str, list[str]] = defaultdict(list)
    for sub_name in by_subreddit:
        cluster = sub_to_cluster.get(sub_name, "other")
        clusters_seen[cluster].append(sub_name)

    for cluster, subs in sorted(clusters_seen.items()):
        lines.append(f"### Кластер: {cluster}")
        lines.append("")
        for sub_name in sorted(subs):
            sub_cards = sorted(by_subreddit[sub_name], key=lambda c: c.score, reverse=True)
            lines.append(f"**r/{sub_name}** ({len(sub_cards)} постов)")
            lines.append("")
            for card in sub_cards[:10]:
                badge = f"`{card.monitoring_type}`"
                flair = f" [{card.link_flair_text}]" if card.link_flair_text else ""
                lines.append(
                    f"- {badge} [{_truncate(card.title, 120)}]({card.full_url}) "
                    f"— ⬆ {_format_score(card.score)}, 💬 {card.num_comments}{flair}"
                )
                if card.top_comments:
                    top = card.top_comments[0]
                    lines.append(
                        f"  - 💬 Top: «{_truncate(top.body, 150)}» "
                        f"(⬆ {_format_score(top.score)}, u/{top.author})"
                    )
            lines.append("")

    # ── Keyword Search ──
    search_cards = [c for c in cards if c.monitoring_type == "search"]
    if search_cards:
        lines.append("---")
        lines.append("")
        lines.append("## 🔍 Keyword Search")
        lines.append("")
        by_keyword: dict[str, list[PostCard]] = defaultdict(list)
        for card in search_cards:
            by_keyword[card.keyword or "(none)"].append(card)
        for keyword, kw_cards in sorted(by_keyword.items()):
            kw_cards.sort(key=lambda c: c.score, reverse=True)
            lines.append(f"### «{keyword}» ({len(kw_cards)} постов)")
            lines.append("")
            for card in kw_cards[:8]:
                lines.append(
                    f"- [{_truncate(card.title, 120)}]({card.full_url}) "
                    f"— r/{card.subreddit}, ⬆ {_format_score(card.score)}, "
                    f"💬 {card.num_comments}"
                )
            lines.append("")

    # ── Tracked Threads ──
    if thread_states:
        lines.append("---")
        lines.append("")
        lines.append("## 📌 Tracked Threads")
        lines.append("")
        lines.append("| Тред | Score | Коммент. | Δ коммент. | Δ score |")
        lines.append("|------|------:|--------:|----------:|-------:|")
        for state in thread_states:
            title_short = _truncate(state.title, 60)
            dc = state.new_comments_since_last
            ds = state.score_delta
            dc_str = f"+{dc}" if dc > 0 else str(dc)
            ds_str = f"+{ds}" if ds > 0 else str(ds)
            lines.append(
                f"| [{title_short}]({state.url}) "
                f"| {_format_score(state.score)} "
                f"| {state.num_comments} "
                f"| {dc_str} "
                f"| {ds_str} |"
            )
        lines.append("")

    # ── Virality Signals ──
    if virality_signals:
        lines.append("---")
        lines.append("")
        lines.append("## 🌐 Virality Signals")
        lines.append("")
        type_labels = {
            "crosspost": "🔁 Cross-post",
            "score_surge": "🚀 Score surge",
            "multi_subreddit": "📡 Multi-subreddit",
        }
        for sig in sorted(virality_signals, key=lambda s: s.total_score, reverse=True):
            label = type_labels.get(sig.signal_type, sig.signal_type)
            lines.append(
                f"- {label}: [{_truncate(sig.title, 100)}]({sig.url}) "
                f"— r/{sig.original_subreddit}, ⬆ {_format_score(sig.total_score)}, "
                f"💬 {sig.total_comments}"
            )
            if sig.crossposted_to:
                lines.append(f"  - Распространение: {', '.join(sig.crossposted_to[:5])}")
        lines.append("")

    # ── Trend Summary ──
    lines.append("---")
    lines.append("")
    lines.append("## 📊 Trend Summary")
    lines.append("")

    # Топ постов по score
    top_by_score = sorted(cards, key=lambda c: c.score, reverse=True)[:5]
    if top_by_score:
        lines.append("**Топ-5 по score:**")
        lines.append("")
        for i, card in enumerate(top_by_score, 1):
            lines.append(
                f"{i}. [{_truncate(card.title, 100)}]({card.full_url}) "
                f"— r/{card.subreddit}, ⬆ {_format_score(card.score)}"
            )
        lines.append("")

    # Топ по комментариям
    top_by_comments = sorted(cards, key=lambda c: c.num_comments, reverse=True)[:5]
    if top_by_comments:
        lines.append("**Топ-5 по обсуждаемости:**")
        lines.append("")
        for i, card in enumerate(top_by_comments, 1):
            lines.append(
                f"{i}. [{_truncate(card.title, 100)}]({card.full_url}) "
                f"— r/{card.subreddit}, 💬 {card.num_comments}"
            )
        lines.append("")

    # Распределение по кластерам
    if cluster_names:
        lines.append("**Объём по кластерам:**")
        lines.append("")
        for cluster, subs in sorted(cluster_names.items()):
            cluster_cards = [c for c in cards if c.subreddit.lower() in {s.lower() for s in subs}]
            if cluster_cards:
                lines.append(f"- {cluster}: {len(cluster_cards)} постов")
        lines.append("")

    lines.append("---")
    lines.append(f"*Сгенерировано reddit-compass, {snapshot_date}*")
    lines.append("")

    return "\n".join(lines)


def write_trends_report(report: str, path: Path) -> None:
    _ensure_dir(path)
    path.write_text(report, encoding="utf-8")
    logger.info("Отчёт записан → %s", path)


def write_snapshot(
    output_dir: Path,
    snapshot_date: str,
    cards: list[PostCard],
    thread_states: list[TrackedThreadState],
    virality_signals: list[ViralitySignal],
    subreddit_clusters: dict[str, list[str]] | None = None,
) -> Path:
    """Записывает полный snapshot: JSONL + Markdown отчёт."""
    snap_dir = output_dir / snapshot_date
    snap_dir.mkdir(parents=True, exist_ok=True)

    write_posts_jsonl(cards, snap_dir / "posts.jsonl")
    if thread_states:
        write_threads_jsonl(thread_states, snap_dir / "tracked-threads.jsonl")
    if virality_signals:
        write_virality_jsonl(virality_signals, snap_dir / "virality.jsonl")

    report = render_trends_report(
        cards, thread_states, virality_signals, snapshot_date, subreddit_clusters
    )
    write_trends_report(report, snap_dir / "trends-report.md")

    logger.info("Snapshot записан → %s", snap_dir)
    return snap_dir
