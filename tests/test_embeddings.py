"""Deterministic dense retrieval without all-pairs persistence."""

import pytest

from reddit_compass.intelligence.embeddings import (
    LEXICAL_HASH_EMBEDDING_MODEL,
    MODEL2VEC_DEFAULT,
    encode_passages,
    top_k_cosine_pairs,
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
