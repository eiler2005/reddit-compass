"""Реестр версий: что развёрнуто и на каких данных работает.

Версий в проекте несколько, и они отвечают на разные вопросы:

* **app** — версия кода и GUI. На VPS исходники лежат копией без git, поэтому SHA
  туда попадает файлом ``BUILD_INFO``, который пишет ``deploy.sh``. Без него рантайм
  честно скажет ``unknown`` вместо того, чтобы выдать SHA машины разработчика.
* **assets** — версия статики. Считается как хэш содержимого ``static/``: раньше
  cache-buster правился руками в шаблоне и разъезжался с реальным файлом.
* **corpus** — что собрано: последний raw run в ``compass.db``.
* **data** — что видит UI: опубликованный Data/Story/Trend release.
* **schema** — версии схем обеих БД (``PRAGMA user_version``).

Первые четыре пишутся в таблицу ``runtime_versions`` в момент события (деплой, сбор,
публикация), а не вычисляются задним числом: после деплоя узнать, какой SHA собран,
уже неоткуда.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "api" / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent / "api" / "templates"


def _build_info_path() -> Path | None:
    """Где искать ``BUILD_INFO``.

    В контейнере пакет установлен в site-packages, поэтому ``parents[2]``
    указывает куда угодно, только не в ``/app``, куда deploy.sh монтирует файл.
    Из-за этого ``/version`` на проде отвечал ``git_sha: unknown`` при живом
    и правильно скопированном BUILD_INFO.
    """
    candidates = [
        Path(os.environ["RC_BUILD_INFO"]) if os.environ.get("RC_BUILD_INFO") else None,
        PROJECT_ROOT / "BUILD_INFO",
        Path("/app/BUILD_INFO"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            return candidate
    return None


APP_COMPONENT = "app"
ASSETS_COMPONENT = "assets"
CORPUS_COMPONENT = "corpus"
DATA_COMPONENT = "data"


def app_version() -> str:
    """Версия пакета из метаданных установки."""
    try:
        from importlib.metadata import version

        return version("reddit-compass")
    except Exception:  # версия не должна ронять рантайм
        return "0.0.0"


def git_sha() -> str:
    """SHA рабочего дерева. На VPS git отсутствует — там источник ``BUILD_INFO``."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build_info() -> dict[str, str]:
    """Что было собрано при деплое: SHA, время, версия пакета.

    ``BUILD_INFO`` пишет ``deploy.sh`` на машине разработчика, где git есть.
    Локально файла обычно нет, поэтому SHA берётся из git напрямую.
    """
    info: dict[str, str] = {}
    path = _build_info_path()
    if path is not None:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip():
                info[key.strip()] = value.strip()
    info.setdefault("git_sha", git_sha() or "unknown")
    info.setdefault("version", app_version())
    return info


@lru_cache(maxsize=1)
def assets_version() -> str:
    """Хэш содержимого статики — автоматический cache-buster.

    Ручной ``?v=20260731.3`` в шаблоне приходилось помнить бампнуть при каждой правке
    скрипта; забытый бамп означает, что браузер продолжает крутить старый JS против
    нового HTML. Хэш меняется ровно тогда, когда меняется файл.
    """
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def schema_versions(
    corpus_conn: sqlite3.Connection | None = None,
    engine_conn: sqlite3.Connection | None = None,
) -> dict[str, int | None]:
    """``PRAGMA user_version`` обеих БД. ``None`` — соединение не передано."""

    def _read(conn: sqlite3.Connection | None) -> int | None:
        if conn is None:
            return None
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
        except sqlite3.Error:
            return None
        return int(row[0]) if row else None

    return {"compass_db": _read(corpus_conn), "trend_engine_db": _read(engine_conn)}


def version_report(
    engine_conn: sqlite3.Connection | None = None,
    corpus_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Сводка для ``/version`` и CLI: код, статика, схемы и записанный реестр."""
    from .intelligence.engine import load_runtime_versions

    build = build_info()
    report: dict[str, Any] = {
        "service": "reddit-compass",
        "app": {
            "version": build.get("version", app_version()),
            "git_sha": build.get("git_sha", "unknown"),
            "built_at": build.get("built_at", ""),
        },
        "assets": {"version": assets_version()},
        "schema": schema_versions(corpus_conn, engine_conn),
        "registry": load_runtime_versions(engine_conn) if engine_conn is not None else {},
    }
    return report
