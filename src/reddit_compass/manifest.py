"""Run-манифест: прозрачный лог каждого запуска.

Записывает: какие источники обработаны, сколько собрано, ошибки, длительность.
Файл: data/snapshots/YYYY-MM-DD/run-manifest.json
Dashboard читает и показывает панель статуса.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("reddit_compass")


@dataclass
class SourceResult:
    """Результат сбора одного источника."""

    name: str
    status: str  # "ok" | "error" | "empty" | "skipped"
    count: int = 0
    errors: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    note: str = ""


@dataclass
class RunManifest:
    """Манифест одного запуска (или nightly)."""

    run_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_sec: float = 0.0
    sources: list[SourceResult] = field(default_factory=list)
    total_items: int = 0
    status: str = "running"  # "running" | "done" | "partial"

    def add_source(self, result: SourceResult) -> None:
        self.sources.append(result)
        self.total_items += result.count

    def finish(self) -> None:
        self.finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if self.started_at:
            start = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            self.duration_sec = (datetime.now(UTC) - start).total_seconds()
        failed = [s for s in self.sources if s.status == "error"]
        self.status = "done" if not failed else "partial"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_manifest() -> RunManifest:
    """Создаёт новый манифест с текущим временем."""
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return RunManifest(
        run_id=now.replace(":", "").replace("-", "")[:14],
        started_at=now,
    )


def save_manifest(manifest: RunManifest, snap_dir: Path) -> Path:
    """Сохраняет манифест в snapshot-директорию."""
    snap_dir.mkdir(parents=True, exist_ok=True)
    path = snap_dir / "run-manifest.json"
    path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Манифест: %s (%d источников, %d items)",
        manifest.status,
        len(manifest.sources),
        manifest.total_items,
    )
    return path


def load_manifest(snap_dir: Path) -> RunManifest | None:
    """Загружает манифест из snapshot-директории."""
    path = snap_dir / "run-manifest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        sources = [SourceResult(**s) for s in data.get("sources", [])]
        return RunManifest(
            run_id=data.get("run_id", ""),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            duration_sec=data.get("duration_sec", 0),
            sources=sources,
            total_items=data.get("total_items", 0),
            status=data.get("status", "unknown"),
        )
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Не удалось загрузить манифест: %s", exc)
        return None
