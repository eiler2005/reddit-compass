"""Конфигурация reddit-compass: профиль (сабреддиты, keywords, tracked threads) и пути.

Все каталоги — внутри проекта и переопределяются через переменные окружения. Никаких
привязок к внешним репозиториям: сервис автономен.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Корень репозитория: src/reddit_compass/config.py -> parents[2].
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _dir(env: str, default: Path) -> Path:
    """Каталог из переменной окружения или значение по умолчанию."""
    val = os.environ.get(env)
    return Path(val).expanduser() if val else default


DEFAULT_DATA_DIR = _dir("DATA_DIR", PROJECT_ROOT / "data")
DEFAULT_SNAPSHOTS_DIR = DEFAULT_DATA_DIR / "snapshots"
DEFAULT_HARVESTS_DIR = _dir("HARVESTS_DIR", DEFAULT_DATA_DIR / "harvests")
DEFAULT_CONFIG_PATH = _dir(
    "REDDIT_COMPASS_CONFIG", PROJECT_ROOT / "config" / "profiles" / "ai-native.json"
)


@dataclass
class MonitorSettings:
    posts_per_subreddit: int = 25
    top_comments_per_post: int = 5
    comments_for_top_n: int = 5
    stealth: bool = False
    time_filter: str = "week"
    search_sort: str = "relevance"
    search_time_filter: str = "week"
    virality_score_threshold: int = 1000
    virality_crosspost_min: int = 2


@dataclass
class MonitorConfig:
    subreddits: dict[str, list[str]] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    tracked_threads: list[str] = field(default_factory=list)
    settings: MonitorSettings = field(default_factory=MonitorSettings)

    @property
    def all_subreddits(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for names in self.subreddits.values():
            for name in names:
                lower = name.lower()
                if lower not in seen:
                    seen.add(lower)
                    result.append(name)
        return result

    @property
    def subreddit_clusters(self) -> dict[str, list[str]]:
        return dict(self.subreddits)

    @classmethod
    def from_file(cls, path: Path = DEFAULT_CONFIG_PATH) -> MonitorConfig:
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {path}")
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        settings_raw = raw.get("settings", {})
        settings = MonitorSettings(
            posts_per_subreddit=int(settings_raw.get("posts_per_subreddit", 25)),
            top_comments_per_post=int(settings_raw.get("top_comments_per_post", 5)),
            comments_for_top_n=int(settings_raw.get("comments_for_top_n", 5)),
            stealth=bool(settings_raw.get("stealth", False)),
            time_filter=str(settings_raw.get("time_filter", "week")),
            search_sort=str(settings_raw.get("search_sort", "relevance")),
            search_time_filter=str(settings_raw.get("search_time_filter", "week")),
            virality_score_threshold=int(settings_raw.get("virality_score_threshold", 1000)),
            virality_crosspost_min=int(settings_raw.get("virality_crosspost_min", 2)),
        )
        return cls(
            subreddits=raw.get("subreddits", {}),
            keywords=raw.get("keywords", []),
            tracked_threads=raw.get("tracked_threads", []),
            settings=settings,
        )
