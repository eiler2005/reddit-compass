"""Тесты обучаемого скоринга слияния историй (Фаза 3)."""

from __future__ import annotations

import numpy as np
import pytest

from reddit_compass.intelligence.story_scoring import (
    FEATURE_KEYS,
    MergeModel,
    auto_label_pair,
    calibrate_threshold,
    extract_feature_vector,
    train_merge_model,
)


def _features(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title_score": 0.5,
        "token_jaccard": 0.3,
        "entity_score": 0.0,
        "dense_similarity": None,
        "date_distance_days": 1,
        "source_independent": True,
        "action_match": False,
        "shared_entities": [],
        "shared_action_tokens": [],
        "number_conflict": False,
        "location_conflict": False,
        "person_conflict": False,
    }
    base.update(overrides)
    return base


def test_extract_feature_vector_has_fixed_shape_and_order() -> None:
    vector = extract_feature_vector(
        _features(
            title_score=0.9,
            shared_entities=["openai", "gpt"],
            shared_action_tokens=["launch"],
            number_conflict=True,
        )
    )
    assert vector.shape == (len(FEATURE_KEYS),)
    by_key = dict(zip(FEATURE_KEYS, vector.tolist(), strict=True))
    assert by_key["title_score"] == pytest.approx(0.9)
    assert by_key["shared_entity_count"] == 2.0
    assert by_key["shared_action_token_count"] == 1.0
    assert by_key["number_conflict"] == 1.0
    # Отсутствующий dense_similarity заменяется нейтральным нулём.
    assert by_key["dense_similarity"] == 0.0
    assert by_key["date_proximity"] == 1.0


def test_auto_label_provenance_merge_is_same_story() -> None:
    label = auto_label_pair(
        "auto_merge",
        "near-duplicate title fingerprint",
        _features(title_score=0.8),
    )
    assert label == "same_story"


def test_auto_label_hard_conflict_is_different_story() -> None:
    label = auto_label_pair(
        "review",
        "ambiguous event similarity; LLM/manual review required",
        _features(number_conflict=True),
    )
    assert label == "different_story"
    assert auto_label_pair("reject", "number/date event conflict", _features()) == "different_story"


def test_auto_label_high_title_overlap_with_entity_is_same_story() -> None:
    label = auto_label_pair(
        "review",
        "ambiguous event similarity; LLM/manual review required",
        _features(title_score=0.97, shared_entities=["anthropic"]),
    )
    assert label == "same_story"


def test_auto_label_low_overlap_is_different_story() -> None:
    label = auto_label_pair(
        "review",
        "ambiguous event similarity; LLM/manual review required",
        _features(title_score=0.2, token_jaccard=0.05, shared_entities=[]),
    )
    assert label == "different_story"


def test_auto_label_gray_zone_is_unlabeled() -> None:
    label = auto_label_pair(
        "review",
        "ambiguous event similarity; LLM/manual review required",
        _features(title_score=0.7, token_jaccard=0.4, shared_entities=[]),
    )
    assert label is None


def _separable_dataset() -> tuple[list[np.ndarray], list[bool]]:
    vectors: list[np.ndarray] = []
    labels: list[bool] = []
    # Положительные: высокий title/token, общие сущности, без конфликтов.
    for _ in range(20):
        vectors.append(
            extract_feature_vector(
                _features(
                    title_score=0.92,
                    token_jaccard=0.7,
                    entity_score=0.8,
                    shared_entities=["openai"],
                )
            )
        )
        labels.append(True)
    # Отрицательные: низкое совпадение, конфликты.
    for _ in range(20):
        vectors.append(
            extract_feature_vector(
                _features(
                    title_score=0.25,
                    token_jaccard=0.1,
                    entity_score=0.0,
                    number_conflict=True,
                )
            )
        )
        labels.append(False)
    return vectors, labels


def test_train_merge_model_learns_separable_data() -> None:
    vectors, labels = _separable_dataset()
    model = train_merge_model(vectors, labels, target_precision=0.95)
    assert model.trained_on == 40
    assert model.precision_at_threshold >= 0.95
    positive = _features(title_score=0.95, token_jaccard=0.8, shared_entities=["openai"])
    negative = _features(title_score=0.2, token_jaccard=0.05, number_conflict=True)
    assert model.predict(positive)
    assert not model.predict(negative)
    assert model.model_hash


def test_merge_model_params_roundtrip_preserves_predictions() -> None:
    vectors, labels = _separable_dataset()
    model = train_merge_model(vectors, labels)
    restored = MergeModel.from_params(model.to_params())
    sample = _features(title_score=0.9, token_jaccard=0.6, shared_entities=["x"])
    assert restored.predict(sample) == model.predict(sample)
    assert restored.model_hash == model.model_hash
    assert restored.feature_keys == FEATURE_KEYS


def test_train_merge_model_rejects_single_class() -> None:
    vectors, _ = _separable_dataset()
    with pytest.raises(ValueError, match="both positive and negative"):
        train_merge_model(vectors, [True] * len(vectors))


def test_calibrate_threshold_meets_target_precision_when_possible() -> None:
    scores = [0.95, 0.9, 0.8, 0.4, 0.2]
    labels = [True, True, False, False, False]
    threshold, precision, _ = calibrate_threshold(scores, labels, target_precision=1.0)
    assert precision >= 1.0
    assert threshold == pytest.approx(0.9)


def test_calibrate_threshold_falls_back_to_strictest() -> None:
    # Ни один порог не даёт precision 1.0 — возвращается самый строгий.
    scores = [0.9, 0.5, 0.1]
    labels = [False, True, True]
    threshold, _, _ = calibrate_threshold(scores, labels, target_precision=1.0)
    assert threshold == pytest.approx(0.9)
