"""Quality gates for the Story / Trend / Reddit-Pulse layers (Фаза «100%» / шаг 4).

Зачем: при изменении настроек слияния/таксономии/трендов нужен способ *количественно*
понять, стало ли хуже, и при необходимости откатить изменение. Этот модуль считает
набор метрик по immutable-релизу и сравнивает их с двумя вещами:

* **QUALITY_FLOORS** — абсолютный «допустимый уровень качества» (например, overmerge
  одно-провайдерных историй == 0, ни одной пустой рубрики, ни одного тренда-дубля).
  Релиз либо проходит полы, либо нет — это сигнал «у нас проблема».
* **baseline snapshot** — снимок метрик эталонного релиза (``config/quality_baselines.json``);
  ``check`` дополнительно ловит *регрессии* относительно снимка (метрика ухудшилась
  сильнее допуска). Это позволяет «вернуть тесты назад»: прогнал изменение →
  ``engine quality check`` красный → откат.

Метрики считаются по замороженному релизу, таксономия пересчитывается по новой схеме
прямо из текстов item'ов (stored domain_ids отражают старую таксономию на момент freeze).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .taxonomy import RUBRICS, classify_domains, rubric_for_domains

# Голые глаголы / generic-фразы, которые НЕ являются валидным именем тренда.
_BARE_VERBS = frozenset(
    {
        "fall",
        "rise",
        "raise",
        "warn",
        "approve",
        "launch",
        "layoff",
        "sue",
        "ban",
        "drop",
        "grow",
        "cut",
        "release",
        "buy",
        "sell",
        "hire",
        "delay",
        "reject",
        "block",
        "leak",
        "strike",
        "tumble",
        "crash",
    }
)
_GENERIC_PHRASES = frozenset(
    {
        "ai agent",
        "artificial intelligence",
        "machine learning",
        "open source",
        "social media",
        "united states",
        "white house",
        "regulatory friction",
        "legal risk",
        "security breach",
    }
)


def _trend_value(name: str) -> str:
    # graph-имена имеют вид "Паттерн: X" / "Боль: X"; v2-имена — сырые фразы.
    for prefix in ("Паттерн: ", "Боль: ", "Тема: "):
        if name.startswith(prefix):
            return name[len(prefix) :].strip().lower()
    return name.strip().lower()


# Служебные слова делятся на два класса, и это различие несёт всю работу.
#
# `agent built actually tool` и `regulatory fines in business` неотличимы по форме:
# четыре токена, одно служебное, три знаменательных. Считать их одинаково — значит либо
# пропустить мешок, либо отсеять нормальное имя. Отличается **тип** связки: `in` строит
# отношение «действие в домене», `actually` не строит ничего и появляется в имени только
# потому, что часто встречалось в текстах кластера.
_CONNECTIVE_WORDS = frozenset(
    {
        "and",
        "or",
        "in",
        "of",
        "for",
        "on",
        "to",
        "with",
        "without",
        "from",
        "at",
        "by",
        "after",
        "before",
        "over",
        "under",
        "against",
        "across",
        "between",
        "worldwide",
        "amid",
        "as",
    }
)

# Слова разговорной речи и оценки. В названии события их не бывает: они попадают туда
# только из частотного разбора реплик. Одного достаточно, чтобы признать имя мешком.
_FILLER_WORDS = frozenset(
    {
        "actually",
        "really",
        "very",
        "too",
        "also",
        "still",
        "even",
        "just",
        "quite",
        "first",
        "next",
        "keep",
        "see",
        "get",
        "got",
        "make",
        "made",
        "want",
        "need",
        "think",
        "feel",
        "point",
        "thing",
        "things",
        "stuff",
        "way",
        "ways",
        "lot",
        "much",
        "many",
        "more",
        "most",
        "some",
        "any",
        "all",
        "good",
        "bad",
        "best",
        "worst",
        "help",
        "advice",
        "question",
        "opinion",
        "guy",
        "guys",
        "people",
    }
)

# Артикли, местоимения и вспомогательные глаголы: не мусор сами по себе, но и не связка —
# их наличие не спасает имя от признания мешком.
_NEUTRAL_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "would",
        "can",
        "could",
        "should",
        "may",
        "might",
        "must",
        "not",
        "no",
        "it",
        "its",
        "he",
        "she",
        "they",
        "them",
        "his",
        "her",
        "their",
        "my",
        "your",
        "we",
        "us",
        "i",
        "you",
        "me",
        "this",
        "that",
        "these",
        "those",
        "so",
        "such",
        "if",
        "then",
        "than",
        "up",
        "down",
        "out",
        "off",
        "about",
        "into",
        "onto",
        "but",
    }
)

# Токены, по которым имя опознаётся как мусор вёрстки, а не как событие.
_BOILERPLATE_TOKENS = frozenset(
    {"sitemap", "rss", "subscribe", "newsletter", "cookie", "privacy", "advertisement"}
)


def _has_proper_noun(name: str) -> bool:
    """Есть ли в имени имя собственное — актор события.

    Любая заглавная считается сигналом, включая первое слово. Компромисс осознанный:
    «Amazon warehouse tracking penalty» и «Credit retirement money house» по регистру
    неразличимы, и выбирать приходится, какая ошибка дороже. Пол блокирует релиз при
    count > 0, поэтому ложное срабатывание останавливает нормальный релиз и подрывает
    доверие к гейту, а пропуск — оставляет одно плохое имя, которое поймает ручная
    проверка. Все 54 имени боевого `trends_5a880292319845b46bf3` — в нижнем регистре
    (embedding_v2 собирает их из частотных токенов), поэтому sentence-cased мешок
    остаётся гипотезой, а актор в начале имени — обычное дело. Мешки с заглавной, если
    появятся, ловятся правилами про filler и boilerplate.
    """
    raw = name
    for prefix in ("Паттерн: ", "Боль: ", "Тема: "):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    tokens = [token for token in re.split(r"[^\w]+", raw.strip()) if token]
    return any(token[:1].isupper() or any(c.isupper() for c in token[1:]) for token in tokens)


def trend_name_defect(name: str) -> str:
    """Причина, по которой имя тренда непригодно; пустая строка — имя приемлемо.

    Прежняя проверка ловила только три формы: пустое, односложное и фразу из словаря
    generic. Мимо неё проходил самый частый в боевом релизе дефект — «мешок токенов»:
    имя, собранное из частотных слов кластера, а не описывающее событие. В
    `trends_5a880292319845b46bf3` такими были `actually promote saas first`,
    `credit retirement money house`, `job see point pay feel`, а также
    `yorker keep truckin sitemap april sporting s` — и все они формально проходили гейт,
    потому что не односложные и не из словаря.

    Признак мешка — не длина и не редкость слов, а отсутствие связи: доля служебных слов
    высока, знаменательного ядра нет, либо в имя попала вёрстка источника.
    """
    value = _trend_value(name)
    if not value:
        return "empty"
    tokens = [token for token in re.split(r"[^\w]+", value) if token]
    if not tokens:
        return "empty"
    if len(tokens) == 1:
        # Порядок важен для точности причины: голый глагол — тоже один токен, но
        # оператору нужно знать, что имя не «слишком короткое», а «это глагол».
        # Раньше проверка длины стояла первой и делала ветку `bare_verb` недостижимой.
        if value in _BARE_VERBS:
            return "bare_verb"
        if len(value) <= 3:
            return "single_token"
    if value in _GENERIC_PHRASES:
        return "generic_phrase"
    if _BOILERPLATE_TOKENS & set(tokens):
        return "boilerplate_token"
    # Одиночные буквы («april sporting s») — след обрезанного токена, а не слово.
    if len(tokens) >= 4 and any(len(token) == 1 for token in tokens):
        return "token_bag"
    # Разговорное слово в имени события не появляется иначе как из частотного разбора.
    if _FILLER_WORDS & set(tokens):
        return "filler_word"
    connectives = [token for token in tokens if token in _CONNECTIVE_WORDS]
    content = [
        token for token in tokens if token not in _CONNECTIVE_WORDS and token not in _NEUTRAL_WORDS
    ]
    # Четыре и больше знаменательных слов без единой связки — перечисление токенов
    # кластера, а не фраза: «credit retirement money house», «stock investor nervous trap».
    #
    # Исключение — имя собственное. `OpenAI quantum agent platform` имеет ту же форму
    # (четыре знаменательных слова, ни одной связки), но называет конкретного актора, а
    # мешок собирается из частотных нарицательных и актора не содержит по построению.
    # Сигнал теряется при приведении к нижнему регистру, поэтому регистр проверяется по
    # исходной строке.
    if len(content) >= 4 and not connectives and not _has_proper_noun(name):
        return "token_bag"
    return ""


def is_bad_trend_name(name: str) -> bool:
    """Имя тренда неприемлемо. Причину даёт `trend_name_defect`."""
    return bool(trend_name_defect(name))


# Издания, без которых релиз не имеет смысла публиковать в broad.
#
# Владелец сформулировал правило так: блокируют «условный reddit и 5-6 новостных
# сайтов», всё остальное — предупреждение. Пятёрка выбрана по вкладу в боевой релиз
# `2026-07-30_2026-08-05-broad-r2` (10 927 материалов): reddit 6875, nytimes 685,
# guardian 392, reuters 358, washingtonpost 321, bbc 247. Дальше идёт резкий обрыв
# (ft 187, foxnews 160), поэтому граница проведена здесь, а не на глаз.
#
# Отвалившееся на ночь издание вне этого набора релиз не блокирует: такое случается по
# причинам издателя, и превращать это в остановку публикации значило бы блокировать
# чаще, чем есть настоящая проблема.
CRITICAL_PROVIDERS = frozenset(
    {"reddit", "nytimes", "guardian", "reuters", "washingtonpost", "bbc"}
)

# Доля изданий, ниже которой корпус перестаёт быть представительным. 70% от 21 — это
# 15 изданий; при меньшем числе «срез мира» держится на нескольких источниках, и
# кросс-source подтверждение, ради которого весь слой Stories и существует, слабеет.
MIN_PROVIDER_SHARE = 70.0

# Абсолютный допустимый уровень качества. ``op`` = "max" (value <= floor) / "min".
QUALITY_FLOORS: dict[str, dict[str, Any]] = {
    "stories_overmerge_ge5": {
        "op": "max",
        "value": 0,
        "desc": "single-provider stories with >=5 items",
    },
    "stories_overmerge_ge8": {
        "op": "max",
        "value": 0,
        "desc": "single-provider stories with >=8 items",
    },
    "taxonomy_ai_tech_share": {
        "op": "max",
        "value": 50.0,
        "desc": "ai_technology share of items, %",
    },
    "taxonomy_other_share": {"op": "max", "value": 40.0, "desc": "unclassified (other) share, %"},
    "taxonomy_max_rubric_share": {
        "op": "max",
        "value": 50.0,
        "desc": "largest top-level rubric share, %",
    },
    "taxonomy_empty_rubrics": {
        "op": "max",
        "value": 0,
        "desc": "top-level rubrics with zero items",
    },
    "trends_bad_name_count": {
        "op": "max",
        "value": 0,
        "desc": "trends with single-token/bare-verb/generic/token-bag name",
    },
    "trends_duplicate_name_count": {"op": "max", "value": 0, "desc": "duplicate trend names"},
    "collection_provider_share": {
        "op": "min",
        "value": MIN_PROVIDER_SHARE,
        "desc": "share of expected publishers present in the release, %",
    },
    "collection_critical_missing": {
        "op": "max",
        "value": 0,
        "desc": "critical publishers (reddit + top news) missing from the release",
    },
    # На слой Trends смотрели только две проверки имён, поэтому кластер размером
    # в четверть корпуса проходил гейт незамеченным: на 2026-07-26_2026-08-01-broad-r2
    # крупнейший «тренд» держал 2077 сюжетов из 8409 (24.7%) под именем
    # «job agent actually work». Тренд — повторяющийся паттерн, а не корзина.
    # Порог 10% при измеренных 2.6% на рабочем пороге кластеризации 0.75.
    "trends_max_story_share": {
        "op": "max",
        "value": 10.0,
        "desc": "largest trend share of stories, %",
    },
    "pulse_other_share": {
        "op": "max",
        "value": 35.0,
        "desc": "Pulse signals classified as other, %",
    },
    # Полы полноты: нормированы на 1000 items, чтобы релизы разного размера были сравнимы.
    #
    # Прежние значения (90 / 35 / 0.85) выведены из «состояния до схлопывания»
    # (122 multi/1k, 45 cross/1k), чей overmerge никогда не измеряли. Замер фронтира
    # на 2026-07-26_2026-08-01-broad-r2 показал, что они стоят на потолке достижимого:
    #
    #   состояние                    multi/1k   cross/1k   compression   overmerge
    #   схлопнутое (без слияний)      42–52      12–20     0.931–0.950     0 / 0
    #   рабочая полоса (CE 0.87–0.97) 77–90      35–41     0.835–0.868     0 / 0
    #
    # При нулевом переслиянии пол 90 брала ровно одна точка порога, а +0.03 к нему уже
    # давало провал. Это не планка качества, а совпадение.
    #
    # Новые значения — середина пустого зазора между режимами. Любая измеренная рабочая
    # конфигурация проходит, любое схлопывание падает, запас максимален с обеих сторон.
    # Исходное назначение полов — ловить вырождение слоя Stories — сохранено полностью.
    "stories_multi_per_1k": {
        "op": "min",
        "value": 65,  # зазор 51.9 → 77.1
        "desc": "multi-item stories per 1000 items",
    },
    "stories_cross_source_per_1k": {
        "op": "min",
        "value": 27,  # зазор 19.8 → 35.1
        "desc": "cross-source stories per 1000 items",
    },
    "stories_compression": {
        "op": "max",
        "value": 0.90,  # зазор 0.8681 → 0.9306
        "desc": "story/item ratio (lower = more merging)",
    },
}


@dataclass(frozen=True)
class FloorResult:
    metric: str
    value: float
    floor: float
    op: str
    passed: bool
    desc: str


def compute_quality(
    conn: sqlite3.Connection,
    *,
    data_release_id: str,
    story_release_id: str,
    trend_release_id: str,
    signal_release_id: str | None = None,
) -> dict[str, Any]:
    """Считает метрики качества по immutable-релизу."""

    def q(sql: str, *args: Any) -> list[sqlite3.Row]:
        return cast(list[sqlite3.Row], conn.execute(sql, args).fetchall())

    stories = q(
        "SELECT source_count, item_count FROM engine_stories WHERE story_release_id = ?",
        story_release_id,
    )
    total = len(stories)
    single = sum(1 for s in stories if s["item_count"] == 1)
    multi = sum(1 for s in stories if s["item_count"] > 1)
    cross = sum(1 for s in stories if s["source_count"] > 1)
    overmerge_ge5 = sum(1 for s in stories if s["source_count"] == 1 and s["item_count"] >= 5)
    overmerge_ge8 = sum(1 for s in stories if s["source_count"] == 1 and s["item_count"] >= 8)

    items = q(
        "SELECT title, excerpt, provider, source_section FROM release_items WHERE release_id = ?",
        data_release_id,
    )
    # Покрытие изданий по самому релизу, а не по дневному source_health: публикуется
    # именно релиз, и вопрос «представителен ли он» решается его составом. Дневное
    # покрытие остаётся операционным сигналом и релиз не блокирует.
    from ..collector import expected_providers

    expected = expected_providers()
    present = {str(r["provider"]) for r in items if r["provider"]}
    provider_share = round(100 * len(present & expected) / len(expected), 2) if expected else 0.0
    missing_critical = sorted(CRITICAL_PROVIDERS - present)
    n = len(items)
    dom: Counter[str] = Counter()
    rub: Counter[str] = Counter()
    ai_only = 0
    for r in items:
        d = classify_domains(r["title"], r["excerpt"] or "", r["provider"], r["source_section"])
        dom.update(d)
        rub[rubric_for_domains(d)] += 1
        if d == ["ai_technology"]:
            ai_only += 1
    # Полы по рубрикам считаем по каноническому набору RUBRICS (catch-all "other" — отдельно).
    rubric_ids = [rb.rubric_id for rb in RUBRICS]
    max_rubric = max((rub.get(rid, 0) for rid in rubric_ids), default=0)
    empty_rubrics = sum(1 for rid in rubric_ids if rub.get(rid, 0) == 0)

    trends = q(
        "SELECT name_ru, story_count FROM engine_trends WHERE trend_release_id = ?",
        trend_release_id,
    )
    names = [r["name_ru"] for r in trends]
    # Не только счётчик: «41 плохое имя» не говорит оператору, что чинить. Примеры с
    # причиной превращают провал пола в конкретную задачу.
    name_defects = [(nm, trend_name_defect(nm)) for nm in names]
    bad_name_examples = [{"name": nm, "defect": defect} for nm, defect in name_defects if defect][
        :10
    ]
    bad_name_reasons = dict(Counter(defect for _, defect in name_defects if defect))
    bad_names = sum(1 for _, defect in name_defects if defect)
    dup_names = sum(cnt - 1 for cnt in Counter(names).values() if cnt > 1)
    max_trend_stories = max((int(r["story_count"] or 0) for r in trends), default=0)

    metrics: dict[str, Any] = {
        "data_release_id": data_release_id,
        "story_release_id": story_release_id,
        "trend_release_id": trend_release_id,
        "signal_release_id": signal_release_id or "",
        "stories_total": total,
        "stories_single": single,
        "stories_multi": multi,
        "stories_cross_source": cross,
        "stories_overmerge_ge5": overmerge_ge5,
        "stories_overmerge_ge8": overmerge_ge8,
        "stories_compression": round(total / n, 4) if n else 0.0,
        "stories_multi_per_1k": round(1000 * multi / n, 1) if n else 0.0,
        "stories_cross_source_per_1k": round(1000 * cross / n, 1) if n else 0.0,
        "taxonomy_items": n,
        "taxonomy_ai_tech_share": round(100 * dom.get("ai_technology", 0) / n, 2) if n else 0.0,
        "taxonomy_other_share": round(100 * dom.get("other", 0) / n, 2) if n else 0.0,
        "taxonomy_ai_only_share": round(100 * ai_only / n, 2) if n else 0.0,
        "taxonomy_max_rubric_share": round(100 * max_rubric / n, 2) if n else 0.0,
        "taxonomy_empty_rubrics": empty_rubrics,
        "taxonomy_rubric_dist": dict(rub.most_common()),
        "trends_count": len(names),
        "collection_provider_count": len(present & expected),
        "collection_provider_expected": len(expected),
        "collection_provider_share": provider_share,
        "collection_missing_providers": sorted(expected - present),
        "collection_critical_missing": len(missing_critical),
        "collection_missing_critical_providers": missing_critical,
        "trends_bad_name_count": bad_names,
        "trends_bad_name_reasons": bad_name_reasons,
        "trends_bad_name_examples": bad_name_examples,
        "trends_duplicate_name_count": dup_names,
        "trends_max_story_share": round(100 * max_trend_stories / total, 2) if total else 0.0,
    }

    if signal_release_id:
        tot = q(
            "SELECT COUNT(*) x FROM community_signals WHERE signal_release_id = ?",
            signal_release_id,
        )[0]["x"]
        if tot:
            oth = q(
                "SELECT COUNT(*) x FROM community_signals "
                "WHERE signal_release_id = ? AND signal_type = 'other'",
                signal_release_id,
            )[0]["x"]
            gnz = q(
                "SELECT COUNT(*) x FROM community_signals "
                "WHERE signal_release_id = ? AND perspective_gap > 0",
                signal_release_id,
            )[0]["x"]
            mc = q(
                "SELECT COUNT(*) x FROM community_signals "
                "WHERE signal_release_id = ? AND mainstream_coverage_count > 0",
                signal_release_id,
            )[0]["x"]
            mrow = q(
                "SELECT metrics_json FROM signal_releases WHERE signal_release_id = ?",
                signal_release_id,
            )[0]
            sm = json.loads(mrow["metrics_json"])
            metrics.update(
                {
                    "pulse_total": tot,
                    "pulse_other_share": round(100 * oth / tot, 2),
                    "pulse_gap_nonzero_share": round(100 * gnz / tot, 2),
                    "pulse_mainstream_covered_share": round(100 * mc / tot, 2),
                    "pulse_gap_available": bool(sm.get("perspective_gap_available")),
                }
            )
    return metrics


def evaluate_floors(metrics: dict[str, Any]) -> list[FloorResult]:
    """Проверяет метрики против абсолютных полов качества."""

    results: list[FloorResult] = []
    for metric, spec in QUALITY_FLOORS.items():
        if metric not in metrics:
            continue  # напр. pulse_* отсутствуют, если нет signal_release
        value = float(metrics[metric])
        floor = float(spec["value"])
        passed = value <= floor if spec["op"] == "max" else value >= floor
        results.append(
            FloorResult(
                metric=metric,
                value=value,
                floor=floor,
                op=spec["op"],
                passed=passed,
                desc=spec["desc"],
            )
        )
    return results


# Метрики, по которым ловим регрессию относительно baseline-снимка, и допуск (в единицах метрики).
REGRESSION_METRICS: dict[str, float] = {
    "stories_overmerge_ge5": 0,
    "stories_overmerge_ge8": 0,
    "stories_cross_source": 10,  # падение кросс-источниковых историй сверх допуска
    "stories_multi_per_1k": 20,  # падение multi-item/1k сверх допуска
    "stories_cross_source_per_1k": 10,  # падение cross-source/1k сверх допуска
    "taxonomy_ai_tech_share": 5,
    "taxonomy_other_share": 5,
    "trends_bad_name_count": 0,
    "trends_duplicate_name_count": 0,
}


def evaluate_regressions(metrics: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """Сравнивает метрики со снимком: для 'меньше=лучше' рост сверх допуска = регрессия;
    для cross_source и per-1k полноты — падение сверх допуска."""

    _lower_is_regression = frozenset(
        {"stories_cross_source", "stories_multi_per_1k", "stories_cross_source_per_1k"}
    )
    out: list[dict[str, Any]] = []
    for metric, tol in REGRESSION_METRICS.items():
        if metric not in metrics or metric not in baseline:
            continue
        cur = float(metrics[metric])
        base = float(baseline[metric])
        regressed = cur < base - tol if metric in _lower_is_regression else cur > base + tol
        delta = round(cur - base, 3)
        out.append(
            {
                "metric": metric,
                "current": cur,
                "baseline": base,
                "delta": delta,
                "regressed": regressed,
            }
        )
    return out


def load_baseline(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save_baseline(path: Path, metrics: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
