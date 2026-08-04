"""Схема события из заголовка через LLM: ``(актор, действие, объект)``.

Зачем понадобился третий метод. Слой `schema_v2` распознаёт действие лексиконом из
тринадцати рукописных регулярок, и замер на прогоне 3 августа показал, что это узкое
место сразу с двух сторон (`docs/ENGINE_GENERATIONS.md`, «Поколение 5»):

* **recall ≈ 13 %.** В корпусе 9 317 сюжетов событий примерно 1 723, слой видит 365.
  Мимо проходят «Bank of England holds interest rates at 3.75%», «Ransomware attack
  forces Coca-Cola to suspend US production», «Apple Reclaims Title as the World's Most
  Valuable Public Company» — ни одного из тринадцати глаголов в них нет;
* **precision ≈ 63 %.** Из ста пятидесяти видимых сюжетов пятьдесят пять событиями не
  являются: «Rattlesnakes evolved proteins…», «The ban on robot vacuums won't make them
  safer» — мнение, «Mark Zuckerberg says US should not ban Chinese AI» — реплика.

Ни ручка гранулярности, ни типизация акторов, ни фасет объекта этого не чинят: они
надстройки над лексиконом и наследуют обе его беды.

**Действие нормализуется в закрытый словарь, а не остаётся свободным текстом.** Это не
компромисс, а суть ключа: свободные «hikes», «holds», «terminates» дают по группе на
сюжет, и повтор не обнаружится — ровно та причина, по которой в `schema_v2` появился
лексикон. Отличие в том, что модель сопоставляет по смыслу: «forces to suspend
production» уходит в ``shutdown``, чего регулярка не сделает никогда. Доля, упавшая в
``other``, — честная метрика широты словаря, она пишется в метрики релиза.

Воспроизводимость. Модель недетерминирована, а релизы immutable, поэтому в релиз едет не
модель, а кэш: таблица ``story_schemas`` с ключом по хэшу заголовка. Тот же приём, что у
``llm_reviews`` с ``input_hash``. Релиз детерминирован при заданном кэше, а отпечаток кэша
входит в ``params_hash``.

Ограничение, которое нельзя забывать: ``AGENTS.md`` запрещает выдавать разметку ассистента
за human ground truth. Числа выше — оценка по выборке 150+150 с доверительным интервалом
±6 п.п., а не измеренная истина.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

SCHEMA_PROMPT_VERSION = "trend-schema-v3-2026-08-04"
DEFAULT_EXTRACT_MODEL = "qwen3.6-flash"
EXTRACT_BATCH = 10

# Закрытый словарь действий. Широкий настолько, чтобы `other` осталась меньшинством, и
# при этом достаточно грубый, чтобы разные формулировки одного события сходились в один
# ключ. Метка — то, как действие называется в имени тренда.
ACTION_VOCABULARY: tuple[tuple[str, str], ...] = (
    ("launch", "product launches"),
    ("model_release", "model releases"),
    ("acquisition", "acquisitions"),
    ("funding", "funding rounds"),
    ("ipo", "listings and IPOs"),
    ("earnings", "earnings and results"),
    ("forecast", "forecast changes"),
    ("layoffs", "layoffs"),
    ("hiring", "hiring"),
    ("leadership_change", "leadership changes"),
    ("partnership", "partnerships"),
    ("investment", "capital investment"),
    ("price_change", "price changes"),
    ("regulator_fine", "regulatory fines"),
    ("ban", "bans and restrictions"),
    ("regulation", "new regulation"),
    ("lawsuit", "lawsuits"),
    ("ruling", "rulings and verdicts"),
    ("investigation", "investigations"),
    ("export_control", "export controls"),
    ("outage", "outages"),
    ("breach", "breaches and intrusions"),
    ("recall", "product recalls"),
    ("shutdown", "shutdowns and suspensions"),
    ("protest", "protests and strikes"),
    ("infrastructure", "infrastructure buildout"),
    ("milestone", "milestones and records"),
    ("incident", "accidents and incidents"),
)
_ACTION_LABELS = dict(ACTION_VOCABULARY)
ACTION_KEYS = tuple(key for key, _ in ACTION_VOCABULARY)

_PROMPT_HEAD = """You extract event schemas from news headlines.

For EACH numbered headline decide whether it REPORTS A CONCRETE EVENT that happened or \
was formally announced by an identifiable actor.

NOT an event: questions, opinions, predictions, analysis, personal stories, \
self-promotion, how-to guides, discussion prompts, listicles, someone merely *saying* \
or *urging* something.
An event: an actor did or formally announced something specific.

Return JSON only, no prose:
{"results":[{"i":1,"event":true,"actor":"Bank of England","action":"held interest rates",\
"key":"regulation","object":"interest rates"},{"i":2,"event":false}]}

Rules:
- "actor" — the entity that acted, as written. Never the publication or news outlet.
- "action" — short past-tense verb phrase, 1-4 words.
- "object" — what the action was done to, 1-3 words.
- "key" — EXACTLY ONE of: __KEYS__. Use "other" only if none fits.
- If event is false, omit actor/action/key/object.
- One entry per headline, same numbering, nothing else.

Headlines:
"""


def extraction_prompt(titles: Sequence[str]) -> str:
    """Промпт на батч заголовков.

    Подстановка через ``replace``, а не ``format``: в промпте есть JSON-пример с
    фигурными скобками, и ``format`` разбирает их как поля — падает с ``KeyError``.
    """
    numbered = "\n".join(f"{index + 1}. {title}" for index, title in enumerate(titles))
    head = _PROMPT_HEAD.replace("__KEYS__", ", ".join([*ACTION_KEYS, "other"]))
    return head + numbered


def title_key(title: str) -> str:
    """Ключ кэша: хэш нормализованного заголовка плюс версия промпта.

    Версия в ключе обязательна — при смене промпта прежние ответы больше не описывают
    то, что вернула бы модель, и молча выдавать их за текущие нельзя.
    """
    normalized = re.sub(r"\s+", " ", title.strip().lower())
    payload = f"{SCHEMA_PROMPT_VERSION}\x1f{normalized}"
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def action_label(action_key: str) -> str:
    """Метка действия для имени тренда. Пустая строка — ключ вне словаря."""
    return _ACTION_LABELS.get(action_key, "")


def parse_batch(raw: str, titles: Sequence[str]) -> list[dict[str, Any]]:
    """Разбирает ответ модели в записи по числу заголовков.

    Невалидный JSON и пропущенные номера не роняют прогон: такие заголовки остаются без
    схемы, то есть ведут себя как «событие не распознано». Тихо подставлять выдуманное
    нельзя — релиз должен отличать «не событие» от «модель не ответила».
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    results = parsed.get("results") if isinstance(parsed, dict) else parsed
    if not isinstance(results, list):
        return []
    by_index: dict[int, dict[str, Any]] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("i", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(titles):
            by_index[index] = entry

    records: list[dict[str, Any]] = []
    for position, title in enumerate(titles, start=1):
        entry = by_index.get(position)
        if entry is None:
            continue
        if not entry.get("event"):
            records.append({"title": title, "is_event": 0, "actor": "", "action": "", "key": ""})
            continue
        key = str(entry.get("key") or "").strip().lower()
        if key not in _ACTION_LABELS:
            key = "other"
        records.append(
            {
                "title": title,
                "is_event": 1,
                "actor": str(entry.get("actor") or "").strip(),
                "action": str(entry.get("action") or "").strip(),
                "object": str(entry.get("object") or "").strip(),
                "key": key,
            }
        )
    return records


def load_schemas(conn: sqlite3.Connection, titles: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Кэшированные схемы по ключу заголовка. Отсутствие таблицы — пустой кэш."""
    wanted = {title_key(title): title for title in titles}
    if not wanted:
        return {}
    found: dict[str, dict[str, Any]] = {}
    try:
        rows = conn.execute(
            "SELECT title_hash, is_event, actor, action, object, action_key FROM story_schemas"
        ).fetchall()
    except sqlite3.Error:
        return {}
    for row in rows:
        digest = str(row["title_hash"])
        if digest in wanted:
            found[digest] = {
                "is_event": bool(row["is_event"]),
                "actor": str(row["actor"] or ""),
                "action": str(row["action"] or ""),
                "object": str(row["object"] or ""),
                "key": str(row["action_key"] or ""),
            }
    return found


def store_schemas(
    conn: sqlite3.Connection, records: Sequence[dict[str, Any]], *, model: str
) -> int:
    """Кладёт извлечённые схемы в кэш. Повторный прогон по тем же заголовкам бесплатен."""
    written = 0
    with conn:
        for record in records:
            conn.execute(
                """INSERT INTO story_schemas
                   (title_hash, prompt_version, model, is_event, actor, action, object, action_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(title_hash) DO UPDATE SET
                       prompt_version = excluded.prompt_version,
                       model = excluded.model,
                       is_event = excluded.is_event,
                       actor = excluded.actor,
                       action = excluded.action,
                       object = excluded.object,
                       action_key = excluded.action_key""",
                (
                    title_key(str(record["title"])),
                    SCHEMA_PROMPT_VERSION,
                    model,
                    int(record.get("is_event", 0)),
                    str(record.get("actor", "")),
                    str(record.get("action", "")),
                    str(record.get("object", "")),
                    str(record.get("key", "")),
                ),
            )
            written += 1
    return written


def schemas_digest(schemas: dict[str, dict[str, Any]]) -> str:
    """Отпечаток кэша для ``params_hash``: без него релиз не воспроизводим."""
    payload = json.dumps(
        {digest: schemas[digest] for digest in sorted(schemas)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


async def extract_schemas(
    titles: Sequence[str],
    runner: Callable[[str, str], Awaitable[str]],
    *,
    model: str = DEFAULT_EXTRACT_MODEL,
    batch_size: int = EXTRACT_BATCH,
    on_batch: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Извлекает схемы батчами. ``runner`` инъектируется, поэтому тесты модель не трогают."""
    unique: dict[str, str] = {}
    for title in titles:
        digest = title_key(title)
        if digest not in unique:
            unique[digest] = title
    ordered = list(unique.values())
    records: list[dict[str, Any]] = []
    total = (len(ordered) + batch_size - 1) // batch_size
    for number, start in enumerate(range(0, len(ordered), batch_size), start=1):
        chunk = ordered[start : start + batch_size]
        # Промпт строим ДО try: ошибка сборки промпта — дефект кода, а не сбой сети, и
        # маскировать её под «провайдер не ответил» нельзя. Именно так широкий except
        # однажды спрятал KeyError из `format` и превратил его в тихий пустой батч.
        prompt = extraction_prompt(chunk)
        try:
            raw = await runner(prompt, model)
        except Exception:  # Один сорванный вызов не обязан ронять весь прогон.
            raw = ""
        records.extend(parse_batch(raw, chunk))
        if on_batch is not None:
            on_batch(number, total)
    return records
