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


def test_build_info_is_found_where_deploy_mounts_it(tmp_path, monkeypatch) -> None:
    """В контейнере пакет лежит в site-packages, а BUILD_INFO — в /app.

    Путь считался от ``versioning.py`` вверх на два уровня, что в проде
    указывало мимо: /version отвечал ``git_sha: unknown`` при живом файле.
    """
    from reddit_compass import versioning

    build_info = tmp_path / "BUILD_INFO"
    build_info.write_text("git_sha=abc1234\nbuilt_at=2026-08-01T00:00:00Z\nversion=0.1.0\n")
    monkeypatch.setenv("RC_BUILD_INFO", str(build_info))

    info = versioning.build_info()

    assert info["git_sha"] == "abc1234"
    assert info["built_at"] == "2026-08-01T00:00:00Z"


def test_build_info_falls_back_to_git_when_file_is_absent(tmp_path, monkeypatch) -> None:
    from reddit_compass import versioning

    monkeypatch.setenv("RC_BUILD_INFO", str(tmp_path / "нет-такого"))
    monkeypatch.setattr(versioning, "PROJECT_ROOT", tmp_path)

    info = versioning.build_info()

    assert "git_sha" in info
    assert "version" in info


def test_unreadable_build_info_degrades_instead_of_crashing(tmp_path, monkeypatch) -> None:
    """`version --record` падал на проде с PermissionError.

    mktemp создаёт файл с правами 0600, scp их сохраняет, а контейнер работает
    не от root. Версия обязана деградировать до «unknown», а не до трассировки.
    """
    from reddit_compass import versioning

    build_info = tmp_path / "BUILD_INFO"
    build_info.write_text("git_sha=abc1234\n")
    build_info.chmod(0o000)
    monkeypatch.setenv("RC_BUILD_INFO", str(build_info))
    monkeypatch.setattr(versioning, "PROJECT_ROOT", tmp_path / "нет-корня")

    try:
        info = versioning.build_info()
    finally:
        build_info.chmod(0o644)

    assert info["git_sha"]
    assert info["version"]
