"""Типизация акторов: инварианты вокруг модели, а не сама модель.

GLiNER здесь не загружается. Важно другое: слой обязан работать без установленной
опциональной зависимости и деградировать до глубины 2, когда таблицы типов нет.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from reddit_compass.intelligence.actor_types import (
    actor_types_digest,
    dump_actor_types,
    load_actor_types,
    load_extractor,
    normalize_title_key,
    resolve_actor_types_path,
    type_titles,
)


class _FixedExtractor:
    """Отдаёт заданные сущности по заголовку, считая обращения."""

    def __init__(self, by_title: dict[str, list[dict[str, Any]]]) -> None:
        self.by_title = by_title
        self.calls = 0

    def batch_predict_entities(
        self, texts: list[str], labels: list[str], threshold: float = 0.5, **_: Any
    ) -> list[list[dict[str, Any]]]:
        self.calls += 1
        return [self.by_title.get(text, []) for text in texts]


def test_missing_table_degrades_to_empty_not_error() -> None:
    """Отсутствие таблицы — штатное состояние: Mac мог не отработать."""
    assert load_actor_types(None) == {}
    assert load_actor_types(Path("/nonexistent/actor_types.json")) == {}


def test_corrupt_table_degrades_to_empty(tmp_path: Path) -> None:
    """Битый файл не имеет права уронить ночной цикл."""
    broken = tmp_path / "actor_types.json"
    broken.write_text("{not json", encoding="utf-8")

    assert load_actor_types(broken) == {}


def test_table_survives_a_dump_load_round_trip(tmp_path: Path) -> None:
    table = {"openai launches a new model": ("OpenAI", "company")}
    path = tmp_path / "actor_types.json"
    path.write_text(dump_actor_types(table, built_at="2026-08-03T00:00:00Z"), encoding="utf-8")

    assert load_actor_types(path) == table


def test_key_is_the_normalized_title_not_the_story_id() -> None:
    """story_id выводится из медоида и меняется между релизами, заголовок — нет."""
    assert normalize_title_key("  Amazon   Lays Off\n14000 Managers ") == (
        "amazon lays off 14000 managers"
    )


def test_digest_ignores_ordering_but_tracks_content() -> None:
    """Отпечаток идёт в params_hash: одинаковое содержание — одинаковый релиз."""
    first = {"a": ("OpenAI", "company"), "b": ("EU", "government agency")}
    second = {"b": ("EU", "government agency"), "a": ("OpenAI", "company")}

    assert actor_types_digest(first) == actor_types_digest(second)
    assert actor_types_digest(first) != actor_types_digest({"a": ("OpenAI", "person")})


def test_explicit_path_wins_over_discovery(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.json"

    assert resolve_actor_types_path(explicit) == explicit


def test_type_titles_keeps_the_most_confident_entity() -> None:
    """Не первая по позиции: первая по позиции — тот самый признак, что ломает Title Case."""
    extractor = _FixedExtractor(
        {
            "I Got Long: OpenAI Ships a Model": [
                {"text": "Got", "label": "company", "score": 0.51},
                {"text": "OpenAI", "label": "company", "score": 0.93},
            ]
        }
    )

    table = type_titles(["I Got Long: OpenAI Ships a Model"], extractor=extractor)

    assert table == {"i got long: openai ships a model": ("OpenAI", "company")}


def test_titles_without_an_entity_stay_out_of_the_table() -> None:
    """Отсутствие ключа — полноценное «типа нет», это не то же, что «тип неверный»."""
    extractor = _FixedExtractor({"A quiet afternoon in the park": []})

    assert type_titles(["A quiet afternoon in the park"], extractor=extractor) == {}


def test_duplicate_titles_are_typed_once() -> None:
    extractor = _FixedExtractor(
        {"Amazon lays off staff": [{"text": "Amazon", "label": "company", "score": 0.9}]}
    )

    table = type_titles(["Amazon lays off staff"] * 5, extractor=extractor)

    assert table == {"amazon lays off staff": ("Amazon", "company")}
    assert extractor.calls == 1


def test_labels_outside_the_closed_set_are_ignored() -> None:
    """Ключ схемы строится из четырёх меток; чужая метка не имеет права в него попасть."""
    extractor = _FixedExtractor(
        {"Some headline": [{"text": "Tuesday", "label": "date", "score": 0.99}]}
    )

    assert type_titles(["Some headline"], extractor=extractor) == {}


def test_missing_extra_names_itself_in_the_error() -> None:
    """Без установленного gliner ошибка обязана называть extra, а не ImportError."""
    if importlib.util.find_spec("gliner") is not None:  # pragma: no cover - зависит от окружения
        pytest.skip("gliner установлен: ветка отсутствия зависимости недостижима")

    with pytest.raises(RuntimeError, match=r"reddit-compass\[actors\]"):
        load_extractor()


def test_depth_downgrade_says_so_out_loud(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Глубина 3 без таблицы — это глубина 2, и релиз обязан об этом сказать.

    Регрессия: у `schema_v3` стояла своя копия этой ветки, и она молчала — ночной
    прогон каждую ночь публиковал глубину 2 под именем глубины 3.
    """
    from reddit_compass.intelligence.engine import _resolve_actor_typing

    with caplog.at_level("WARNING"):
        actor_types, effective = _resolve_actor_typing(3, tmp_path / "absent.json")

    assert (actor_types, effective) == ({}, 2)
    assert "ACTOR TYPING FALLBACK" in caplog.text


def test_depth_two_does_not_touch_the_table(tmp_path: Path) -> None:
    """Глубина 2 таблицу не читает: её отсутствие для неё не событие."""
    from reddit_compass.intelligence.engine import _resolve_actor_typing

    assert _resolve_actor_typing(2, tmp_path / "absent.json") == ({}, 2)
