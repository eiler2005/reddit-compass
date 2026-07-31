"""Обучаемый скоринг слияния историй (Фаза 3).

Заменяет подобранные вручную веса серой зоны на логистическую модель, обученную
на признаках из ``story_candidate_pairs.features_json``. Разметка — детерминированная
и воспроизводимая: метки ставятся правилами высокого доверия (provenance-якоря →
``same_story``, жёсткие конфликты → ``different_story``), человек не требуется.
Человеческие метки из ``engine_labels`` всегда имеют приоритет над авто-метками.

Модель иммутабельна: веса, порог и хэш сохраняются в ``metrics_json`` StoryRelease,
поэтому релиз остаётся воспроизводимым. Жёсткие правила (URL-match, hard conflicts)
остаются детерминированными — модель решает только серую зону.

Модуль намеренно dependency-light (numpy): проект избегает тяжёлых ML-зависимостей.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

import numpy as np

# Порядок признаков фиксируется и участвует в хэше модели. Признаки извлекаются из
# features_json пары; отсутствующие значения заменяются нейтральными.
FEATURE_KEYS: tuple[str, ...] = (
    "title_score",
    "token_jaccard",
    "entity_score",
    "dense_similarity",
    "date_proximity",
    "source_independent",
    "action_match",
    "shared_entity_count",
    "shared_action_token_count",
    "number_conflict",
    "location_conflict",
    "person_conflict",
)

# Причины auto_merge, которые считаются достоверным same_story без человека.
_PROVENANCE_MERGE_REASONS = frozenset(
    {
        "shared canonical/target URL",
        "near-duplicate title fingerprint",
        "cross-source event title/entity match",
        "shared HuggingFace model release URL",
        "exact event title match without hard conflict",
        "high event similarity with shared entity/number provenance",
    }
)


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    return default


def _as_bool(value: object) -> bool:
    return bool(value)


def _as_count(value: object) -> float:
    if isinstance(value, list):
        return float(len(value))
    return _as_float(value)


def _date_proximity(features: dict[str, object]) -> float:
    days = _as_float(features.get("date_distance_days"), default=99.0)
    if days <= 2:
        return 1.0
    if days <= 7:
        return 0.7
    return 0.3


def extract_feature_vector(features: dict[str, object]) -> np.ndarray:
    """Собирает фиксированный вектор признаков из features_json пары."""

    values: dict[str, float] = {
        "title_score": _as_float(features.get("title_score")),
        "token_jaccard": _as_float(features.get("token_jaccard")),
        "entity_score": _as_float(features.get("entity_score")),
        "dense_similarity": _as_float(features.get("dense_similarity")),
        "date_proximity": _date_proximity(features),
        "source_independent": 1.0 if _as_bool(features.get("source_independent")) else 0.0,
        "action_match": 1.0 if _as_bool(features.get("action_match")) else 0.0,
        "shared_entity_count": _as_count(features.get("shared_entities")),
        "shared_action_token_count": _as_count(features.get("shared_action_tokens")),
        "number_conflict": 1.0 if _as_bool(features.get("number_conflict")) else 0.0,
        "location_conflict": 1.0 if _as_bool(features.get("location_conflict")) else 0.0,
        "person_conflict": 1.0 if _as_bool(features.get("person_conflict")) else 0.0,
    }
    return np.array([values[key] for key in FEATURE_KEYS], dtype=float)


def auto_label_pair(
    decision: str,
    reason: str,
    features: dict[str, object],
) -> str | None:
    """Детерминированная метка высокого доверия для пары.

    Возвращает ``same_story`` / ``different_story`` либо ``None`` для серой зоны,
    где достоверной автоматической метки нет.
    """

    title_score = _as_float(features.get("title_score"))
    token_jaccard = _as_float(features.get("token_jaccard"))
    shared_entities = _as_count(features.get("shared_entities"))
    number_conflict = _as_bool(features.get("number_conflict"))
    location_conflict = _as_bool(features.get("location_conflict"))
    person_conflict = _as_bool(features.get("person_conflict"))

    if decision == "auto_merge" and reason in _PROVENANCE_MERGE_REASONS:
        return "same_story"
    if decision == "reject" or number_conflict or location_conflict or person_conflict:
        return "different_story"
    # Очень уверенное совпадение: почти идентичный заголовок + общая сущность.
    if title_score >= 0.95 and shared_entities >= 1:
        return "same_story"
    # Очевидно разные: нет ни лексического, ни сущностного пересечения.
    if title_score < 0.4 and token_jaccard < 0.15 and shared_entities == 0:
        return "different_story"
    return None


@dataclass(frozen=True)
class MergeModel:
    """Логистическая модель слияния историй."""

    weights: tuple[float, ...]
    bias: float
    threshold: float
    feature_keys: tuple[str, ...] = FEATURE_KEYS
    trained_on: int = 0
    precision_at_threshold: float = 0.0
    recall_at_threshold: float = 0.0
    model_hash: str = ""
    label_source: str = "auto_label"
    meta: dict[str, object] = field(default_factory=dict)

    def score(self, features: dict[str, object]) -> float:
        vector = extract_feature_vector(features)
        weights = np.array(self.weights, dtype=float)
        logit = float(np.dot(weights, vector) + self.bias)
        return 1.0 / (1.0 + math.exp(-logit))

    def predict(self, features: dict[str, object]) -> bool:
        return self.score(features) >= self.threshold

    def to_params(self) -> dict[str, object]:
        return {
            "weights": [round(float(w), 6) for w in self.weights],
            "bias": round(float(self.bias), 6),
            "threshold": round(float(self.threshold), 6),
            "feature_keys": list(self.feature_keys),
            "trained_on": self.trained_on,
            "precision_at_threshold": round(self.precision_at_threshold, 4),
            "recall_at_threshold": round(self.recall_at_threshold, 4),
            "model_hash": self.model_hash,
            "label_source": self.label_source,
        }

    @classmethod
    def from_params(cls, params: dict[str, object]) -> MergeModel:
        weights_raw = params.get("weights", [])
        weights = tuple(_as_float(w) for w in weights_raw) if isinstance(weights_raw, list) else ()
        keys_raw = params.get("feature_keys", FEATURE_KEYS)
        feature_keys = (
            tuple(str(k) for k in keys_raw) if isinstance(keys_raw, list) else FEATURE_KEYS
        )
        return cls(
            weights=weights,
            bias=_as_float(params.get("bias")),
            threshold=_as_float(params.get("threshold"), default=0.5),
            feature_keys=feature_keys,
            trained_on=int(_as_float(params.get("trained_on"))),
            precision_at_threshold=_as_float(params.get("precision_at_threshold")),
            recall_at_threshold=_as_float(params.get("recall_at_threshold")),
            model_hash=str(params.get("model_hash", "")),
            label_source=str(params.get("label_source", "auto_label")),
        )


def _model_hash(feature_keys: tuple[str, ...], weights: np.ndarray, bias: float) -> str:
    payload = json.dumps(
        {
            "feature_keys": list(feature_keys),
            "weights": [round(float(w), 6) for w in weights],
            "bias": round(float(bias), 6),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sigmoid(matrix: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-matrix))


def train_merge_model(
    vectors: list[np.ndarray],
    labels: list[bool],
    *,
    target_precision: float = 0.95,
    learning_rate: float = 0.1,
    iterations: int = 2000,
    l2: float = 0.01,
    label_source: str = "auto_label",
) -> MergeModel:
    """Обучает логистическую регрессию и калибрует порог под target_precision."""

    if not vectors or len(vectors) != len(labels):
        raise ValueError("vectors and labels must be non-empty and equal length")
    positive = sum(1 for label in labels if label)
    if positive == 0 or positive == len(labels):
        raise ValueError("training set must contain both positive and negative labels")

    x = np.vstack(vectors)
    y = np.array([1.0 if label else 0.0 for label in labels], dtype=float)
    n_features = x.shape[1]
    weights = np.zeros(n_features, dtype=float)
    bias = 0.0
    n = x.shape[0]
    for _ in range(iterations):
        predictions = _sigmoid(x @ weights + bias)
        error = predictions - y
        grad_w = (x.T @ error) / n + l2 * weights
        grad_b = float(error.mean())
        weights -= learning_rate * grad_w
        bias -= learning_rate * grad_b

    scores = _sigmoid(x @ weights + bias)
    threshold, precision, recall = calibrate_threshold(
        scores.tolist(), labels, target_precision=target_precision
    )
    return MergeModel(
        weights=tuple(float(w) for w in weights),
        bias=float(bias),
        threshold=threshold,
        trained_on=n,
        precision_at_threshold=precision,
        recall_at_threshold=recall,
        model_hash=_model_hash(FEATURE_KEYS, weights, bias),
        label_source=label_source,
    )


def calibrate_threshold(
    scores: list[float],
    labels: list[bool],
    *,
    target_precision: float = 0.95,
) -> tuple[float, float, float]:
    """Наименьший порог с precision ≥ target; иначе порог с лучшей precision."""

    if len(scores) != len(labels) or not scores:
        raise ValueError("scores and labels must be non-empty and equal length")
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    best: tuple[float, float, float] | None = None
    tp = 0
    fp = 0
    total_positive = sum(1 for label in labels if label)
    for rank, index in enumerate(order, start=1):
        if labels[index]:
            tp += 1
        else:
            fp += 1
        precision = tp / rank
        recall = tp / total_positive if total_positive else 0.0
        candidate = (scores[index], precision, recall)
        if precision >= target_precision:
            # Идём сверху вниз: последний подходящий кандидат — наименьший порог среди
            # тех, что ещё держат нужную precision (precision монотонно падает вниз).
            best = candidate
    if best is not None:
        return best
    # Ни один порог не дал нужной precision — берём самый строгий (максимальный score).
    top = order[0]
    precision = 1.0 if labels[top] else 0.0
    recall = (1.0 / total_positive) if (labels[top] and total_positive) else 0.0
    return scores[top], precision, recall
