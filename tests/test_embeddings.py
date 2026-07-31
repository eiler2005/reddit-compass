"""Deterministic dense retrieval without all-pairs persistence."""

import pytest

from reddit_compass.intelligence.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    DENSE_THRESHOLD_KEYS,
    LEXICAL_HASH_EMBEDDING_MODEL,
    MODEL2VEC_DEFAULT,
    dense_thresholds_for,
    encode_passages,
    encoding_prefix_for,
    is_dense_profile_calibrated,
    top_k_cosine_pairs,
)
from reddit_compass.intelligence.engine import (
    DEFAULT_STORY_PARAMS,
    _story_generation_params,
)


def test_lexical_hash_embeddings_are_dependency_free_and_useful_for_retrieval() -> None:
    vectors = encode_passages(
        [
            "OpenAI cuts 1,500 roles after security restructuring",
            "OpenAI slashes 1500 jobs after security restructure",
            "Brazil wins football final after late goal",
        ],
        model_name=LEXICAL_HASH_EMBEDDING_MODEL,
    )

    pairs = top_k_cosine_pairs(
        {"a": vectors[0], "b": vectors[1], "c": vectors[2]},
        top_k=2,
        min_similarity=0.2,
    )

    assert ("a", "b") in pairs
    assert pairs[("a", "b")] > pairs.get(("a", "c"), 0)


def test_top_k_cosine_pairs_keeps_only_nearest_neighbours() -> None:
    vectors = {
        "a": [1.0, 0.0, 0.0],
        "b": [0.99, 0.1, 0.0],
        "c": [0.0, 1.0, 0.0],
        "d": [0.0, 0.99, 0.1],
    }

    pairs = top_k_cosine_pairs(
        vectors,
        top_k=1,
        min_similarity=0.9,
        chunk_size=2,
    )

    assert set(pairs) == {("a", "b"), ("c", "d")}
    assert all(score >= 0.9 for score in pairs.values())


def test_top_k_cosine_pairs_is_order_independent() -> None:
    vectors = {
        "c": [0.0, 1.0],
        "a": [1.0, 0.0],
        "b": [0.9, 0.1],
    }

    forward = top_k_cosine_pairs(vectors, top_k=2, min_similarity=0.0)
    reverse = top_k_cosine_pairs(
        dict(reversed(list(vectors.items()))),
        top_k=2,
        min_similarity=0.0,
    )

    assert forward == reverse


def test_model2vec_dispatch_fails_clearly_or_encodes() -> None:
    try:
        import model2vec  # noqa: F401
    except ImportError:
        # Пакет не установлен → понятная ошибка, а не падение импорта модуля.
        with pytest.raises(RuntimeError, match="model2vec"):
            encode_passages(["hello world"], model_name=MODEL2VEC_DEFAULT)
    else:
        vectors = encode_passages(["hello world", "another text"], model_name=MODEL2VEC_DEFAULT)
        assert len(vectors) == 2
        assert all(len(v) > 0 for v in vectors)


def test_dense_thresholds_are_a_property_of_the_model() -> None:
    """Пороги плотного сходства нельзя переносить между моделями.

    У E5 медиана косинуса на несвязанных парах ~0.78, у статических векторов ~0.05.
    Один набор констант на обе модели молча отключает слияние на одной из них.
    """
    e5 = dense_thresholds_for(DEFAULT_EMBEDDING_MODEL)
    lexical = dense_thresholds_for(LEXICAL_HASH_EMBEDDING_MODEL)

    assert set(e5) == set(DENSE_THRESHOLD_KEYS)
    assert set(lexical) == set(DENSE_THRESHOLD_KEYS)
    assert lexical["semantic_dedup_threshold"] < e5["semantic_dedup_threshold"]
    assert lexical["dense_auto_threshold"] < e5["dense_auto_threshold"]


def test_unknown_model_gets_conservative_fallback_and_is_flagged() -> None:
    """Незнакомая модель не падает, но и не притворяется откалиброванной."""
    # Все модели, реально используемые движком, должны иметь измеренный профиль.
    for model in (DEFAULT_EMBEDDING_MODEL, LEXICAL_HASH_EMBEDDING_MODEL, MODEL2VEC_DEFAULT):
        assert is_dense_profile_calibrated(model), model

    assert not is_dense_profile_calibrated("some/unknown-model")
    fallback = dense_thresholds_for("some/unknown-model")
    assert fallback == dense_thresholds_for(DEFAULT_EMBEDDING_MODEL)


def test_production_model_profile_is_not_the_e5_fallback() -> None:
    """Регресс, из-за которого слияние схлопнулось: прод-модель на порогах E5.

    `engine cycle` по умолчанию считает на `potion-base-8M`, где косинус несвязанных
    пар ~0.13 против ~0.78 у E5. Пороги 0.88/0.92 оставляли там 63% / 53%
    provenance-позитивов вместо 94% / 86%.
    """
    prod = dense_thresholds_for(MODEL2VEC_DEFAULT)
    e5 = dense_thresholds_for(DEFAULT_EMBEDDING_MODEL)

    assert prod != e5
    assert prod["semantic_dedup_threshold"] < e5["semantic_dedup_threshold"]
    assert prod["dense_auto_threshold"] < e5["dense_auto_threshold"]


def test_story_params_resolve_model_profile_before_hashing() -> None:
    """Профиль модели попадает в params, а явные значения его перебивают.

    Разрешённые пороги обязаны лежать в params до вычисления params_hash, иначе
    изменение таблицы профилей задним числом сломает воспроизводимость релизов.
    """
    lexical = _story_generation_params({"embedding_model": LEXICAL_HASH_EMBEDDING_MODEL})
    for key, value in dense_thresholds_for(LEXICAL_HASH_EMBEDDING_MODEL).items():
        assert lexical[key] == value

    explicit = _story_generation_params(
        {"embedding_model": LEXICAL_HASH_EMBEDDING_MODEL, "dense_auto_threshold": 0.77}
    )
    assert explicit["dense_auto_threshold"] == 0.77


def test_e5_story_params_keep_historical_behaviour() -> None:
    """Смена механизма не должна менять результат для эталонной модели."""
    resolved = _story_generation_params({"embedding_model": DEFAULT_EMBEDDING_MODEL})
    assert resolved == {**DEFAULT_STORY_PARAMS, "embedding_model": DEFAULT_EMBEDDING_MODEL}


def test_encoding_prefix_is_model_specific() -> None:
    """E5 требует passage: префикс; model2vec и lexical-hash — нет.

    Префикс входит в input_hash кэша, чтобы векторы от разных форматов не
    смешивались при смене модели.
    """
    assert encoding_prefix_for(DEFAULT_EMBEDDING_MODEL) == "passage: "
    assert encoding_prefix_for(MODEL2VEC_DEFAULT) == ""
    assert encoding_prefix_for(LEXICAL_HASH_EMBEDDING_MODEL) == ""


def test_input_hash_differs_between_prefix_profiles() -> None:
    """Один текст с разными префиксами даёт разные хэши — кэш не переиспользуется."""
    from reddit_compass.intelligence.engine import _hash_json

    text = "OpenAI releases GPT-5"
    e5_hash = _hash_json({"text": text, "prefix": encoding_prefix_for(DEFAULT_EMBEDDING_MODEL)})
    m2v_hash = _hash_json({"text": text, "prefix": encoding_prefix_for(MODEL2VEC_DEFAULT)})
    assert e5_hash != m2v_hash
