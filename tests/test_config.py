"""Тесты загрузки конфигурации и отвязки путей."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from reddit_compass.config import MonitorConfig

PROFILES = Path(__file__).resolve().parents[1] / "config" / "profiles"


def test_load_ai_native_profile() -> None:
    cfg = MonitorConfig.from_file(PROFILES / "ai-native.json")
    assert "artificial" in cfg.all_subreddits
    assert cfg.settings.posts_per_subreddit == 25
    assert cfg.subreddit_clusters  # непустые кластеры


def test_load_starter_profile() -> None:
    cfg = MonitorConfig.from_file(PROFILES / "starter.json")
    assert cfg.keywords == []
    assert cfg.tracked_threads == []


def test_all_subreddits_dedup_case_insensitive() -> None:
    cfg = MonitorConfig(subreddits={"a": ["X", "y"], "b": ["x", "Z"]})
    assert cfg.all_subreddits == ["X", "y", "Z"]


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        MonitorConfig.from_file(Path("/nonexistent/config.json"))


def test_env_overrides_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    from reddit_compass import config as cfgmod

    importlib.reload(cfgmod)
    try:
        assert tmp_path / "d" == cfgmod.DEFAULT_DATA_DIR
        assert tmp_path / "d" / "snapshots" == cfgmod.DEFAULT_SNAPSHOTS_DIR
        assert tmp_path / "d" / "harvests" == cfgmod.DEFAULT_HARVESTS_DIR
    finally:
        monkeypatch.delenv("DATA_DIR")
        importlib.reload(cfgmod)  # вернуть дефолты, чтобы не течь в другие тесты
