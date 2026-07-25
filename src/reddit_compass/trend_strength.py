"""Сила тренда и новизна: отслеживание тем между снапшотами.

Для каждой темы считаем composite score:
  strength = cross_source × volume_factor × novelty_bonus × direction

- cross_source: сколько типов источников (reddit, hn, rss, ladder, ph) содержат тему
- volume_factor: log-нормированное число постов
- novelty_bonus: 1.5 для новых (≤7 дней), 1.0 для повторяющихся
- direction: 1.2 растёт / 1.0 стабилен / 0.8 падает (vs предыдущее появление)

История тем: data/theme-history.jsonl (одна строка на тему на дату).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("reddit_compass")

HISTORY_FILE = "theme-history.jsonl"
NOVELTY_WINDOW_DAYS = 7


@dataclass
class ThemeSnapshot:
    """Тема в конкретном снапшоте."""

    date: str
    theme: str
    count: int
    sources: list[str] = field(default_factory=list)
    avg_score: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class TrendInfo:
    """Тема с расчётной силой и новизной."""

    theme: str
    count: int
    sources: list[str]
    strength: float
    is_new: bool
    weeks_seen: int
    direction: str  # "growing" | "stable" | "fading" | "new"

    @property
    def strength_label(self) -> str:
        if self.strength >= 30:
            return "🔥🔥🔥"
        if self.strength >= 15:
            return "🔥🔥"
        if self.strength >= 5:
            return "🔥"
        return "·"

    @property
    def novelty_label(self) -> str:
        if self.is_new:
            return "🆕"
        return f"🔄 {self.weeks_seen} нед"


def load_theme_history(data_dir: Path) -> list[ThemeSnapshot]:
    """Загружает историю тем из theme-history.jsonl."""
    fp = data_dir / HISTORY_FILE
    if not fp.exists():
        return []
    history: list[ThemeSnapshot] = []
    for line in fp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            history.append(
                ThemeSnapshot(
                    date=d["date"],
                    theme=d["theme"],
                    count=d["count"],
                    sources=d.get("sources", []),
                    avg_score=d.get("avg_score", 0.0),
                )
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return history


def save_theme_history(
    data_dir: Path,
    current: list[ThemeSnapshot],
) -> None:
    """Дописывает текущие темы в историю (дедупликация по date+theme)."""
    fp = data_dir / HISTORY_FILE
    existing: set[tuple[str, str]] = set()
    lines: list[str] = []

    if fp.exists():
        for line in fp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                key = (d["date"], d["theme"].lower())
                if key not in existing:
                    existing.add(key)
                    lines.append(line)
            except (json.JSONDecodeError, KeyError):
                continue

    for snap in current:
        key = (snap.date, snap.theme.lower())
        if key not in existing:
            existing.add(key)
            lines.append(snap.to_json())

    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("История тем: %d записей → %s", len(lines), fp)


def extract_themes_from_signals(
    signals: list[dict[str, Any]],
) -> list[ThemeSnapshot]:
    """Извлекает агрегированные темы из signals.jsonl (один снапшот)."""
    if not signals:
        return []

    date = signals[0].get("snapshot_date", "")
    if not date:
        # Пробуем из snapshot_date первого сигнала или из post_id
        date = ""

    theme_data: dict[str, dict[str, Any]] = {}
    for sig in signals:
        # Определяем тип источника
        src_type = _source_type(sig)
        for theme in sig.get("themes", []):
            key = theme.lower().strip()
            if not key:
                continue
            if key not in theme_data:
                theme_data[key] = {
                    "theme": theme,
                    "count": 0,
                    "sources": set(),
                    "total_score": 0,
                }
            theme_data[key]["count"] += 1
            theme_data[key]["sources"].add(src_type)
            theme_data[key]["total_score"] += sig.get("score", 0)

    snapshots: list[ThemeSnapshot] = []
    for data in theme_data.values():
        count = data["count"]
        snapshots.append(
            ThemeSnapshot(
                date=date,
                theme=data["theme"],
                count=count,
                sources=sorted(data["sources"]),
                avg_score=data["total_score"] / count if count else 0,
            )
        )
    return snapshots


def _source_type(sig: dict[str, Any]) -> str:
    """Определяет тип источника из сигнала."""
    src = str(sig.get("source", ""))
    sub = str(sig.get("subreddit", ""))
    if src in ("hackernews", "rss", "ladder", "producthunt"):
        return src
    if sub in ("hackernews", "producthunt"):
        return sub
    if src == "reddit" or (sub and sub not in ("hackernews", "producthunt")):
        return "reddit"
    return src or "unknown"


def compute_trends(
    current_themes: list[ThemeSnapshot],
    history: list[ThemeSnapshot],
    current_date: str,
) -> list[TrendInfo]:
    """Считает силу и новизну для текущих тем на основе истории."""
    from datetime import datetime, timedelta

    # Индекс истории: theme_lower → список ThemeSnapshot по датам
    hist_index: dict[str, list[ThemeSnapshot]] = {}
    for h in history:
        key = h.theme.lower().strip()
        hist_index.setdefault(key, []).append(h)

    # Сортируем историю по дате
    for key in hist_index:
        hist_index[key].sort(key=lambda x: x.date)

    # Парсим текущую дату
    try:
        cur_dt = datetime.strptime(current_date, "%Y-%m-%d")
    except ValueError:
        cur_dt = datetime.now()

    novelty_cutoff = (cur_dt - timedelta(days=NOVELTY_WINDOW_DAYS)).strftime("%Y-%m-%d")

    trends: list[TrendInfo] = []
    for snap in current_themes:
        key = snap.theme.lower().strip()
        past = hist_index.get(key, [])
        # Убираем текущую дату из истории (если уже записана)
        past = [p for p in past if p.date < current_date]

        # Новизна: тема впервые за последние 7 дней?
        recent = [p for p in past if p.date >= novelty_cutoff]
        is_new = len(recent) == 0
        weeks_seen = _weeks_seen(past, current_date)

        # Направление: сравниваем count с предыдущим появлением
        direction = "new" if is_new else _compute_direction(snap, past)

        # Сила
        strength = _compute_strength(snap, is_new, direction)

        trends.append(
            TrendInfo(
                theme=snap.theme,
                count=snap.count,
                sources=snap.sources,
                strength=round(strength, 1),
                is_new=is_new,
                weeks_seen=weeks_seen,
                direction=direction,
            )
        )

    # Сортировка: новые сильные первыми, затем по силе
    trends.sort(key=lambda t: (-t.strength, not t.is_new))
    return trends


def _weeks_seen(past: list[ThemeSnapshot], current_date: str) -> int:
    """Сколько недель тема встречается (включая текущую)."""
    if not past:
        return 1
    dates = {p.date for p in past}
    dates.add(current_date)
    # Грубо: количество уникальных дат / 7, минимум 1
    return max(1, len(dates))


def _compute_direction(snap: ThemeSnapshot, past: list[ThemeSnapshot]) -> str:
    """Растёт / стабилен / падает vs предыдущее появление."""
    if not past:
        return "new"
    prev = past[-1]
    if prev.count == 0:
        return "stable"
    ratio = snap.count / prev.count
    if ratio > 1.3:
        return "growing"
    if ratio < 0.7:
        return "fading"
    return "stable"


def _compute_strength(
    snap: ThemeSnapshot,
    is_new: bool,
    direction: str,
) -> float:
    """Composite score: cross_source × volume × novelty × direction."""
    # Cross-source: 1..5 источников
    cross = max(1, len(snap.sources))

    # Volume: log-нормировка (1 пост = 1, 10 = ~2.3, 100 = ~4.6)
    volume = 1 + math.log1p(snap.count)

    # Novelty bonus
    novelty = 1.5 if is_new else 1.0

    # Direction multiplier
    dir_mult = {"growing": 1.2, "stable": 1.0, "fading": 0.8, "new": 1.5}.get(direction, 1.0)

    return cross * volume * novelty * dir_mult
