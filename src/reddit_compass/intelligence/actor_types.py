"""Тип актора для схемного слоя Trends: zero-shot NER без обучения на собранном контенте.

Зачем это вообще нужно. Гранулярность тренда задаётся числом компонентов ключа схемы.
Сегодня их два — ``(действие, домен)`` — и ключ работает на уровне *theme*: `product
launches in AI` собирает 37 сюжетов и читается как рубрика, а не как паттерн. Третьим
компонентом **нельзя** взять самого актора: тогда каждая группа окажется одноакторной по
построению, а условие ``min_distinct_actors >= 2`` — само определение тренда — не
выполнится никогда, и трендов не останется. Третьим компонентом может быть только **тип**
актора: внутри типа остаются разные конкретные акторы, и условие живёт.

Замер POC на релизе из 466 заголовков с распознанным действием: 10 секунд на CPU,
сущность найдена в 396, пережило 45 трендов из 53. Потерянные восемь — все ровно по три
сюжета, все по одной причине: сущности нашлись, но разных типов, поэтому ни один тип не
набрал двух.

Отрицательный результат, повторять не нужно: признак «имя собственное = заглавное не в
начале предложения» разваливается о Title Case («I **Got** Long: AI Agents»). Формально
он давал «48 из 53», но списки акторов оставались мусорными.

Зависимость опциональная и в прод-образ не входит: типизация считается на Mac после
цикла, а в релиз едет готовой таблицей (``scripts/fetch-and-sync.sh``). Отсутствие
таблицы — штатное состояние, слой обязан деградировать до глубины 2, а не падать.

Ориентир: GLiNER (github.com/urchade/GLiNER) — извлечение спанов сущностей по
произвольным меткам. Ранее отклонён как сигнал для *слияния* материалов (там отклонение
верное), здесь используется по прямому назначению.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from ..config import DEFAULT_DATA_DIR, PROJECT_ROOT

DEFAULT_ACTOR_MODEL = "gliner-community/gliner_small-v2.5"
DEFAULT_ACTOR_THRESHOLD = 0.5
ACTOR_LABELS: tuple[str, ...] = ("company", "government agency", "person", "country")
ACTOR_TYPES_FILENAME = "actor_types.json"
ACTOR_TYPES_VERSION = 1

_WHITESPACE = re.compile(r"\s+")


class _Extractor(Protocol):
    def batch_predict_entities(
        self, texts: list[str], labels: list[str], threshold: float = ..., **kwargs: Any
    ) -> list[list[dict[str, Any]]]: ...


def normalize_title_key(title: str) -> str:
    """Ключ таблицы типов.

    По заголовку, а не по ``story_id``: идентификатор сюжета выводится из медоида
    (``_stable_id("story", medoid.item_id)``) и перезаписывается при inheritance/split,
    то есть меняется между релизами. Заголовок — нет, поэтому таблица переживает
    пересборку сюжетов и её не нужно строить заново каждую ночь.
    """
    return _WHITESPACE.sub(" ", title.strip().lower())


def load_actor_types(path: Path | None = None) -> dict[str, tuple[str, str]]:
    """Таблица ``ключ заголовка → (текст актора, тип)``.

    Пустой словарь, если файла нет или он повреждён: отсутствие таблицы — штатное
    состояние (Mac мог не отработать), и слой обязан деградировать до глубины 2.
    """
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return {}
    table: dict[str, tuple[str, str]] = {}
    for key, value in entries.items():
        if isinstance(value, list) and len(value) == 2:
            table[str(key)] = (str(value[0]), str(value[1]))
    return table


def resolve_actor_types_path(explicit: str | Path | None = None) -> Path | None:
    """Явный путь → ``$DATA_DIR/actor_types.json`` → ``config/actor_types.json``.

    Файл держим в data-каталоге (в проде это docker-volume), а не в ``config/``: его
    ключи — нормализованные заголовки собранного контента, и в трекаемых файлах им не
    место. ``config/`` остаётся необязательным последним звеном для закреплённой копии.
    """
    if explicit:
        return Path(explicit).expanduser()
    for candidate in (
        DEFAULT_DATA_DIR / ACTOR_TYPES_FILENAME,
        PROJECT_ROOT / "config" / ACTOR_TYPES_FILENAME,
    ):
        if candidate.exists():
            return candidate
    return None


def actor_types_digest(table: dict[str, tuple[str, str]]) -> str:
    """Отпечаток таблицы для ``params_hash`` релиза.

    Без него два релиза с одинаковым ``params_hash`` могли бы иметь разное содержание,
    если файл изменился под ними, — и релиз перестал бы быть воспроизводимым.
    """
    payload = json.dumps(
        {key: list(value) for key, value in sorted(table.items())},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def dump_actor_types(
    table: dict[str, tuple[str, str]],
    *,
    model_id: str = DEFAULT_ACTOR_MODEL,
    threshold: float = DEFAULT_ACTOR_THRESHOLD,
    built_at: str = "",
) -> str:
    """Сериализация таблицы в формат, который читает ``load_actor_types``."""
    payload = {
        "version": ACTOR_TYPES_VERSION,
        "model": model_id,
        "threshold": threshold,
        "labels": list(ACTOR_LABELS),
        "built_at": built_at,
        "entries": {key: list(value) for key, value in sorted(table.items())},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def load_extractor(model_id: str = DEFAULT_ACTOR_MODEL) -> _Extractor:
    """Грузит GLiNER, внятно падая при отсутствии опциональной зависимости."""
    try:
        module = importlib.import_module("gliner")
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise RuntimeError(
            "Actor typing requires the optional dependency: install reddit-compass[actors]"
        ) from exc
    extractor: _Extractor = module.GLiNER.from_pretrained(model_id)
    return extractor


def type_titles(
    titles: Sequence[str],
    *,
    model_id: str = DEFAULT_ACTOR_MODEL,
    threshold: float = DEFAULT_ACTOR_THRESHOLD,
    batch_size: int = 32,
    extractor: _Extractor | None = None,
) -> dict[str, tuple[str, str]]:
    """Ведущая сущность каждого заголовка и её тип.

    Заголовки без сущности выше порога в таблицу не попадают: отсутствие ключа —
    полноценное состояние «типа нет», и оно отличается от «тип неверный». В POC так
    оказалось 70 заголовков из 466.

    Берём сущность с максимальным ``score``, а не первую по позиции: первая по позиции —
    это ровно тот признак «заглавное в начале», который разваливается о Title Case.
    """
    unique: dict[str, str] = {}
    for title in titles:
        key = normalize_title_key(title)
        if key and key not in unique:
            unique[key] = title
    if not unique:
        return {}
    model = extractor if extractor is not None else load_extractor(model_id)
    keys = list(unique)
    table: dict[str, tuple[str, str]] = {}
    for start in range(0, len(keys), batch_size):
        chunk = keys[start : start + batch_size]
        predictions = model.batch_predict_entities(
            [unique[key] for key in chunk], list(ACTOR_LABELS), threshold=threshold
        )
        for key, entities in zip(chunk, predictions, strict=False):
            best = _best_entity(entities)
            if best is not None:
                table[key] = best
    return table


def _best_entity(entities: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Сущность с наибольшей уверенностью; ``None`` — ни одна не прошла порог."""
    ranked = [
        (float(entity.get("score") or 0.0), str(entity.get("text") or "").strip(), label)
        for entity in entities
        if (label := str(entity.get("label") or "")) in ACTOR_LABELS
    ]
    ranked = [entry for entry in ranked if entry[1]]
    if not ranked:
        return None
    # Тай-брейк по тексту, а не по порядку модели: релиз обязан быть воспроизводимым.
    _, text, label = max(ranked, key=lambda entry: (entry[0], entry[1]))
    return text, label
