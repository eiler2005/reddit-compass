"""Разбор серой зоны Stories готовым cross-encoder'ом.

Зачем: детерминированная лестница решений даёт ``auto_merge`` только по provenance и
сильным лексическим признакам, а всё остальное отправляет в ``review``. Разбирать эту
очередь по одному LLM-вызову на пару не получается: на боевом релизе она содержит
~6 000 пар, а ночной цикл успевал 80 за 18 минут — очередь не сходится в принципе.

Готовый ранжировщик закрывает ровно этот разрыв: 5 989 пар за 59 секунд на CPU.
Это **не** модель event-coreference, а MS MARCO passage-ranker, поэтому он работает
только внутри жёстких ограничений и никогда не переопределяет детерминированные запреты:

* ``reject`` и hard conflicts (number/location/person) слиянием стать не могут;
* provenance-``auto_merge`` сохраняются нетронутыми;
* порог применяется только к парам с решением ``review``.

Порог выбирается на отложенной половине размеченного набора при precision ≥ 0.95 и
переносится без пересчёта. Замер на ``2026-07-26_2026-08-01-broad-r2`` (9 816 items):

    порог   слияний   multi/1k   cross/1k   compression   overmerge ≥5/≥8
    0.8739    2146       90.1       41.2      0.8353          0 / 0
    0.95      1582       80.8       36.8      0.8567          0 / 0
    0.97      1271       77.1       35.1      0.8681          0 / 0

Дефолт 0.95 — precision-first точка с запасом по всем полам, а не край фронтира.
"""

from __future__ import annotations

import importlib
import logging
import math
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from .engine import FrozenItem, PairCandidate

logger = logging.getLogger("reddit_compass")

DEFAULT_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
DEFAULT_CROSS_ENCODER_THRESHOLD = 0.95
_MAX_EXCERPT_CHARS = 1_600
_HARD_CONFLICT_FEATURES = ("number_conflict", "location_conflict", "person_conflict")


class _Scorer(Protocol):
    def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> Any: ...


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def pair_text(title: str, excerpt: str) -> str:
    """Вход ранжировщика: заголовок плюс ограниченный кусок frozen excerpt."""
    compact = " ".join((excerpt or "").split())[:_MAX_EXCERPT_CHARS]
    return f"Headline: {title}\nExcerpt: {compact}" if compact else f"Headline: {title}"


def load_scorer(model_id: str = DEFAULT_CROSS_ENCODER_MODEL) -> _Scorer:
    """Грузит cross-encoder, внятно падая при отсутствии опциональной зависимости."""
    try:
        module = importlib.import_module("sentence_transformers")
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise RuntimeError(
            "Cross-encoder adjudication requires the optional dependency: "
            "install reddit-compass[adjudicate]"
        ) from exc
    scorer: _Scorer = module.CrossEncoder(model_id, max_length=512, device="cpu")
    return scorer


def is_adjudicable(candidate: PairCandidate) -> bool:
    """Пара, которую ранжировщику вообще позволено рассматривать."""
    if candidate.decision != "review":
        return False
    return not any(bool(candidate.features.get(key)) for key in _HARD_CONFLICT_FEATURES)


# Пар в одном проходе, между которыми пишется строка прогресса. 512 — примерно
# полминуты работы на боевом хосте: достаточно редко, чтобы не засорять журнал, и
# достаточно часто, чтобы зависание было видно в пределах минуты.
_PROGRESS_CHUNK = 512


def _predict_with_progress(
    engine: _Scorer, pairs: list[tuple[str, str]], batch_size: int, label: str
) -> list[float]:
    """Скоринг кусками с отметками прогресса вместо одного молчаливого вызова.

    Раньше здесь был один `predict` на весь массив с `show_progress_bar=False`, то есть
    на боевом объёме (~6000 пар в каждую сторону) журнал молчал десятки минут. Когда
    6 августа хост перестал отвечать посреди этой стадии, по логу нельзя было понять
    ни где именно всё встало, ни двигался ли процесс вообще — последняя строка была
    «загрузил модель».

    Результат не меняется: те же пары в том же порядке, разбиение только по вызовам.
    """
    scores: list[float] = []
    total = len(pairs)
    for start in range(0, total, _PROGRESS_CHUNK):
        chunk = pairs[start : start + _PROGRESS_CHUNK]
        scores.extend(
            float(value)
            for value in engine.predict(chunk, batch_size=batch_size, show_progress_bar=False)
        )
        logger.info(
            "cross-encoder %s: %d/%d пар", label, min(start + _PROGRESS_CHUNK, total), total
        )
    return scores


def adjudicate_story_pairs(
    candidates: list[PairCandidate],
    items: list[FrozenItem],
    *,
    model_id: str = DEFAULT_CROSS_ENCODER_MODEL,
    threshold: float = DEFAULT_CROSS_ENCODER_THRESHOLD,
    batch_size: int = 64,
    scorer: _Scorer | None = None,
) -> list[PairCandidate]:
    """Промотирует пары серой зоны выше порога, остальные закрывает как ``reject``.

    Симметричный скор: пара оценивается в обе стороны, sigmoid усредняется — иначе
    результат зависел бы от порядка item_id, а релиз обязан быть воспроизводимым.
    """
    from .engine import PairCandidate as Candidate

    item_by_id = {item.item_id: item for item in items}
    targets = [
        candidate
        for candidate in candidates
        if is_adjudicable(candidate)
        and candidate.item_id_a in item_by_id
        and candidate.item_id_b in item_by_id
    ]
    if not targets:
        return list(candidates)

    texts = {
        item_id: pair_text(item_by_id[item_id].title, item_by_id[item_id].excerpt)
        for candidate in targets
        for item_id in (candidate.item_id_a, candidate.item_id_b)
    }
    forward = [(texts[c.item_id_a], texts[c.item_id_b]) for c in targets]
    backward = [(right, left) for left, right in forward]
    engine = scorer if scorer is not None else load_scorer(model_id)
    forward_scores = _predict_with_progress(engine, forward, batch_size, "прямой проход")
    backward_scores = _predict_with_progress(engine, backward, batch_size, "обратный проход")
    scores = {
        candidate.item_id_a + "|" + candidate.item_id_b: (
            _sigmoid(float(left)) + _sigmoid(float(right))
        )
        / 2
        for candidate, left, right in zip(targets, forward_scores, backward_scores, strict=True)
    }

    resolved: list[PairCandidate] = []
    for candidate in candidates:
        score = scores.get(candidate.item_id_a + "|" + candidate.item_id_b)
        if score is None:
            resolved.append(candidate)
            continue
        merged = score >= threshold
        resolved.append(
            Candidate(
                item_id_a=candidate.item_id_a,
                item_id_b=candidate.item_id_b,
                score=max(candidate.score, score) if merged else candidate.score,
                decision="auto_merge" if merged else "reject",
                reason=(
                    "cross-encoder adjudication above threshold"
                    if merged
                    else "cross-encoder adjudication below threshold"
                ),
                features={**candidate.features, "cross_encoder_score": round(score, 4)},
            )
        )
    return resolved
