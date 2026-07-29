"""Deterministic dense retrieval without all-pairs persistence."""

from reddit_compass.intelligence.embeddings import top_k_cosine_pairs


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
