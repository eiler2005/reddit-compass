"""Ночной разбор трендов: анализ snapshot → подробный md с темами.

Config-driven: группирует по кластерам из активного профиля, ранжирует по score и
обсуждаемости, выделяет сильные темы, кандидатов для сводки, tracked threads и виральные
сигналы. Потребители (контент, дайджест, ресёрч) читают готовый md, не завися от рантайма.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from .models import PostCard, TrackedThreadState, ViralitySignal

if TYPE_CHECKING:
    from .config import MonitorConfig

logger = logging.getLogger("reddit_compass")


def _load_snapshot(
    snap_dir: Path,
) -> tuple[list[PostCard], list[TrackedThreadState], list[ViralitySignal]]:
    cards: list[PostCard] = []
    posts_file = snap_dir / "posts.jsonl"
    if posts_file.exists():
        for line in posts_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cards.append(PostCard.from_dict(json.loads(line)))

    threads: list[TrackedThreadState] = []
    threads_file = snap_dir / "tracked-threads.jsonl"
    if threads_file.exists():
        for line in threads_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                threads.append(TrackedThreadState(**json.loads(line)))

    signals: list[ViralitySignal] = []
    virality_file = snap_dir / "virality.jsonl"
    if virality_file.exists():
        for line in virality_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                signals.append(ViralitySignal(**json.loads(line)))

    return cards, threads, signals


def _fmt_score(score: int) -> str:
    if score >= 1000:
        return f"{score / 1000:.1f}k"
    return str(score)


def _truncate(text: str, max_len: int = 120) -> str:
    text = text.strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"


def generate_trends_analysis(
    snap_dir: Path,
    output_path: Path,
    config: MonitorConfig,
    snapshot_date: str,
) -> None:
    """Генерирует подробный md с трендами и рекомендациями."""
    cards, threads, signals = _load_snapshot(snap_dir)

    if not cards:
        logger.warning("Нет данных для анализа в %s", snap_dir)
        output_path.write_text(
            f"# Reddit Trends — {snapshot_date}\n\n> Нет данных. Snapshot пуст.\n",
            encoding="utf-8",
        )
        return

    lines: list[str] = []
    lines.append(f"# Reddit Trends — {snapshot_date}")
    lines.append("")
    lines.append(
        f"> Собрано **{len(cards)} постов** из {len({c.subreddit for c in cards})} сабреддитов. "
        f"Tracked threads: {len(threads)}. Virality signals: {len(signals)}."
    )
    lines.append(f"> Движок: Playwright JSON API. Дата: {snapshot_date}.")
    lines.append("")

    # ── Топ-20 по score ──
    lines.append("---")
    lines.append("")
    lines.append("## 🔥 Топ-20 по score")
    lines.append("")
    top_by_score = sorted(cards, key=lambda c: c.score, reverse=True)[:20]
    for i, card in enumerate(top_by_score, 1):
        flair = f" `{card.link_flair_text}`" if card.link_flair_text else ""
        lines.append(
            f"{i}. **[{_truncate(card.title, 100)}]({card.full_url})** "
            f"— r/{card.subreddit}, ⬆ {_fmt_score(card.score)}, 💬 {card.num_comments}{flair}"
        )
        if card.top_comments:
            top_c = card.top_comments[0]
            lines.append(
                f"   - 💬 «{_truncate(top_c.body, 150)}» "
                f"(⬆ {_fmt_score(top_c.score)}, u/{top_c.author})"
            )
    lines.append("")

    # ── Топ-10 по обсуждаемости ──
    lines.append("---")
    lines.append("")
    lines.append("## 💬 Топ-10 по обсуждаемости")
    lines.append("")
    top_by_comments = sorted(cards, key=lambda c: c.num_comments, reverse=True)[:10]
    for i, card in enumerate(top_by_comments, 1):
        lines.append(
            f"{i}. **[{_truncate(card.title, 100)}]({card.full_url})** "
            f"— r/{card.subreddit}, 💬 {card.num_comments}, ⬆ {_fmt_score(card.score)}"
        )
    lines.append("")

    # ── Тренды по кластерам ──
    lines.append("---")
    lines.append("")
    lines.append("## 📊 Тренды по кластерам")
    lines.append("")

    sub_to_cluster: dict[str, str] = {}
    for cluster, subs in config.subreddit_clusters.items():
        for s in subs:
            sub_to_cluster[s.lower()] = cluster

    by_cluster: dict[str, list[PostCard]] = defaultdict(list)
    for card in cards:
        cluster = sub_to_cluster.get(card.subreddit.lower(), "other")
        by_cluster[cluster].append(card)

    # Порядок кластеров — из активного профиля; «other» и прочее добавляем следом.
    cluster_order = list(config.subreddit_clusters.keys())
    for extra in by_cluster:
        if extra not in cluster_order:
            cluster_order.append(extra)

    for cluster in cluster_order:
        cluster_cards = by_cluster.get(cluster, [])
        if not cluster_cards:
            continue
        cluster_cards.sort(key=lambda c: c.score, reverse=True)

        lines.append(f"### {cluster} ({len(cluster_cards)} постов)")
        lines.append("")

        for card in cluster_cards[:8]:
            lines.append(
                f"- [{_truncate(card.title, 100)}]({card.full_url}) "
                f"— r/{card.subreddit}, ⬆ {_fmt_score(card.score)}, 💬 {card.num_comments}"
            )
        lines.append("")

    # ── Сильные темы по кластерам ──
    lines.append("---")
    lines.append("")
    lines.append("## 📝 Сильные темы по кластерам")
    lines.append("")
    lines.append("Посты с высоким score и обсуждаемостью по кластерам:")
    lines.append("")

    for cluster in cluster_order:
        cluster_cards = by_cluster.get(cluster, [])
        hot = [c for c in cluster_cards if c.score >= 100 or c.num_comments >= 50]
        if not hot:
            continue
        hot.sort(key=lambda c: c.score + c.num_comments, reverse=True)
        lines.append(f"**{cluster}:**")
        lines.append("")
        for card in hot[:5]:
            lines.append(
                f"- [{_truncate(card.title, 100)}]({card.full_url}) "
                f"— r/{card.subreddit}, ⬆ {_fmt_score(card.score)}, 💬 {card.num_comments}"
            )
        lines.append("")

    # ── Темы для дайджеста ──
    lines.append("---")
    lines.append("")
    lines.append("## 📰 Темы для сводки/дайджеста")
    lines.append("")
    lines.append(
        "Формат «три этажа»: первичка (цифра) → рамка (эксперты) → голос/контраст (Reddit)."
    )
    lines.append("")

    digest_candidates = sorted(cards, key=lambda c: c.score + c.num_comments * 2, reverse=True)[:10]
    for card in digest_candidates:
        lines.append(
            f"- **[{_truncate(card.title, 90)}]({card.full_url})** "
            f"(r/{card.subreddit}, ⬆ {_fmt_score(card.score)})"
        )
        if card.top_comments:
            top_c = card.top_comments[0]
            lines.append(f"  - Голос: «{_truncate(top_c.body, 150)}» (u/{top_c.author})")
    lines.append("")

    # ── Tracked threads ──
    if threads:
        lines.append("---")
        lines.append("")
        lines.append("## 📌 Tracked Threads")
        lines.append("")
        lines.append("| Тред | Score | Коммент. | Δ коммент. | Δ score |")
        lines.append("|------|------:|--------:|----------:|-------:|")
        for state in threads:
            title_short = _truncate(state.title, 60)
            dc = state.new_comments_since_last
            ds = state.score_delta
            dc_str = f"+{dc}" if dc > 0 else str(dc)
            ds_str = f"+{ds}" if ds > 0 else str(ds)
            lines.append(
                f"| [{title_short}]({state.url}) "
                f"| {_fmt_score(state.score)} "
                f"| {state.num_comments} "
                f"| {dc_str} "
                f"| {ds_str} |"
            )
        lines.append("")

    # ── Виральные сигналы ──
    if signals:
        lines.append("---")
        lines.append("")
        lines.append("## 🌐 Виральные сигналы")
        lines.append("")
        type_labels = {
            "crosspost": "🔁 Cross-post",
            "score_surge": "🚀 Score surge",
            "multi_subreddit": "📡 Multi-subreddit",
        }
        for sig in sorted(signals, key=lambda s: s.total_score, reverse=True):
            label = type_labels.get(sig.signal_type, sig.signal_type)
            lines.append(
                f"- {label}: [{_truncate(sig.title, 100)}]({sig.url}) "
                f"— r/{sig.original_subreddit}, ⬆ {_fmt_score(sig.total_score)}"
            )
            if sig.crossposted_to:
                lines.append(f"  - Распространение: {', '.join(sig.crossposted_to[:5])}")
        lines.append("")

    # ── Рекомендации ──
    lines.append("---")
    lines.append("")
    lines.append("## ✅ Рекомендации: что взять в работу")
    lines.append("")

    # Топ-3 самых горячих поста
    top3 = sorted(cards, key=lambda c: c.score + c.num_comments * 2, reverse=True)[:3]
    lines.append("**Самые горячие темы прямо сейчас:**")
    lines.append("")
    for i, card in enumerate(top3, 1):
        lines.append(f"{i}. [{_truncate(card.title, 100)}]({card.full_url})")
        lines.append(f"   - r/{card.subreddit}, ⬆ {_fmt_score(card.score)}, 💬 {card.num_comments}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Сгенерировано reddit-compass, {snapshot_date}*")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Trends analysis записан → %s (%d строк)", output_path, len(lines))
