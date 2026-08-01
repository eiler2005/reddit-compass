"""Реестр версий: что развёрнуто и на каких данных работает."""

from __future__ import annotations

from pathlib import Path

from reddit_compass.intelligence.engine import (
    engine_db,
    load_runtime_versions,
    record_runtime_version,
)
from reddit_compass.versioning import build_info, schema_versions, version_report


def test_assets_version_tracks_static_content(tmp_path: Path, monkeypatch) -> None:
    """Версия статики обязана меняться вместе с файлами.

    Раньше cache-buster правился руками в шаблоне: забытый бамп означал, что браузер
    крутит старый JS против новой разметки — расхождение, невидимое в тестах.
    """
    import reddit_compass.versioning as versioning

    static = tmp_path / "static"
    static.mkdir()
    (static / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(versioning, "STATIC_DIR", static)

    versioning.assets_version.cache_clear()
    before = versioning.assets_version()

    (static / "app.js").write_text("console.log(2)", encoding="utf-8")
    versioning.assets_version.cache_clear()
    after = versioning.assets_version()

    assert before != after, "правка статики обязана менять её версию"
    assert len(after) == 12


def test_registry_records_and_overwrites_component(tmp_path: Path) -> None:
    """Компонент хранит одну текущую версию, а не историю: реестр отвечает «что сейчас»."""
    conn = engine_db(tmp_path / "trend_engine.db")

    record_runtime_version(conn, "app", "0.1.0", {"git_sha": "aaa"})
    record_runtime_version(conn, "data", "publication_1", {"channel": "broad"})
    record_runtime_version(conn, "app", "0.2.0", {"git_sha": "bbb"})

    registry = load_runtime_versions(conn)

    assert registry["app"]["version"] == "0.2.0"
    assert registry["app"]["detail"]["git_sha"] == "bbb"
    assert registry["data"]["version"] == "publication_1"
    assert registry["app"]["recorded_at"], "без времени записи реестр бесполезен"


def test_version_report_survives_db_without_registry(tmp_path: Path) -> None:
    """Старый файл БД без таблицы не должен ронять /version."""
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA user_version = 3")
    conn.commit()

    report = version_report(engine_conn=conn)

    assert report["registry"] == {}
    assert report["schema"]["trend_engine_db"] == 3
    assert report["app"]["version"]


def test_schema_versions_report_both_databases(tmp_path: Path) -> None:
    """Расхождение схемы с кодом должно быть видно, а не выясняться по падению."""
    import sqlite3

    corpus = sqlite3.connect(tmp_path / "compass.db")
    corpus.execute("PRAGMA user_version = 3")
    corpus.commit()
    engine = engine_db(tmp_path / "trend_engine.db")

    versions = schema_versions(corpus_conn=corpus, engine_conn=engine)

    assert versions["compass_db"] == 3
    assert versions["trend_engine_db"] >= 7, "движок должен применять актуальную схему"


def test_build_info_falls_back_when_file_absent() -> None:
    """Без BUILD_INFO версия кода не выдумывается, а помечается как неизвестная."""
    info = build_info()

    assert "git_sha" in info
    assert info["git_sha"], "пустой SHA хуже явного unknown"
    assert "version" in info
