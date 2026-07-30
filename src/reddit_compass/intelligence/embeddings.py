"""Optional local embeddings for Story/Trend candidate retrieval.

The engine remains usable without this extra. When ``sentence-transformers`` is
installed, vectors are generated locally and persisted by the engine. Candidate
retrieval keeps only top-K neighbours per item; it never materializes every pair.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import re
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
LEXICAL_HASH_EMBEDDING_MODEL = "lexical-hash-v1"
# Лёгкий torch-free бэкенд (статические дистиллированные эмбеддинги). Модели model2vec
# лежат на HF под префиксом ``minishlab/``; используем их для embedding_v2 без torch.
MODEL2VEC_DEFAULT = "minishlab/potion-base-8M"
_MODEL2VEC_PREFIX = "minishlab/"
_LEXICAL_DIMENSIONS = 384
_TOKEN_PATTERN = re.compile(r"[\w$€£%.-]+", re.UNICODE)


def encode_passages(
    texts: list[str],
    *,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = 32,
) -> list[list[float]]:
    """Encode passages locally, failing clearly when the optional extra is absent."""
    if model_name == LEXICAL_HASH_EMBEDDING_MODEL:
        return [_lexical_hash_vector(text) for text in texts]

    if model_name.startswith(_MODEL2VEC_PREFIX):
        try:
            m2v = importlib.import_module("model2vec")
        except ImportError as exc:
            raise RuntimeError(
                "model2vec embeddings require the optional dependency: "
                "install reddit-compass[embed] (or `pip install model2vec`)"
            ) from exc
        model = m2v.StaticModel.from_pretrained(model_name)
        return [[float(value) for value in vector] for vector in model.encode(texts)]

    try:
        module = importlib.import_module("sentence_transformers")
    except ImportError as exc:
        raise RuntimeError(
            "Local embeddings require the optional engine dependencies: "
            "install reddit-compass[engine]"
        ) from exc

    model_class: Any = module.SentenceTransformer
    model = model_class(model_name)
    vectors: Any = model.encode(
        [f"passage: {text}" for text in texts],
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [[float(value) for value in vector] for vector in vectors]


def _lexical_hash_vector(text: str, dimensions: int = _LEXICAL_DIMENSIONS) -> list[float]:
    """Dependency-free hashed lexical vector for production-safe candidate retrieval.

    This is not a replacement for multilingual semantic embeddings. It is a cheap
    always-available retrieval layer that gives the Story Engine dense-like top-K
    candidates when the heavy ``sentence-transformers`` extra is unavailable.
    """
    vector = [0.0] * dimensions
    normalized = " ".join(text.lower().split())
    tokens = [token.strip(".,:;!?()[]{}'\"") for token in _TOKEN_PATTERN.findall(normalized)]
    tokens = [token for token in tokens if len(token) >= 2]
    features: list[tuple[str, float]] = []
    features.extend((f"tok:{token}", 1.0) for token in tokens)
    features.extend(
        (f"bi:{left}_{right}", 1.25) for left, right in pairwise(tokens) if left != right
    )
    features.extend(
        (f"tri:{normalized[index : index + 3]}", 0.35)
        for index in range(max(len(normalized) - 2, 0))
        if " " not in normalized[index : index + 3]
    )
    for feature, weight in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign * weight
    return _normalize(vector)


def top_k_cosine_pairs(
    vectors: dict[str, list[float]],
    *,
    top_k: int = 12,
    min_similarity: float = 0.68,
    chunk_size: int = 256,
) -> dict[tuple[str, str], float]:
    """Return only deterministic top-K cosine pairs, using chunked NumPy when available."""
    if len(vectors) < 2:
        return {}
    item_ids = sorted(vectors)
    dimensions = {len(vectors[item_id]) for item_id in item_ids}
    if len(dimensions) != 1 or 0 in dimensions:
        raise ValueError("Embedding vectors must have one non-zero dimension")

    try:
        numpy = importlib.import_module("numpy")
    except ImportError:
        return _python_top_k_pairs(
            vectors,
            item_ids=item_ids,
            top_k=top_k,
            min_similarity=min_similarity,
        )

    matrix: Any = numpy.asarray([vectors[item_id] for item_id in item_ids], dtype="float32")
    norms: Any = numpy.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / numpy.maximum(norms, 1e-12)
    result: dict[tuple[str, str], float] = {}
    neighbour_count = min(top_k + 1, len(item_ids))
    for start in range(0, len(item_ids), max(1, chunk_size)):
        stop = min(start + max(1, chunk_size), len(item_ids))
        similarities: Any = matrix[start:stop] @ matrix.T
        for offset, row in enumerate(similarities):
            source_index = start + offset
            candidate_indexes: Any = numpy.argpartition(
                row,
                -neighbour_count,
            )[-neighbour_count:]
            ranked = sorted(
                (int(index) for index in candidate_indexes if int(index) != source_index),
                key=lambda index: (-float(row[index]), item_ids[index]),
            )[:top_k]
            for target_index in ranked:
                similarity = float(row[target_index])
                if similarity < min_similarity:
                    continue
                key = _pair_key(item_ids[source_index], item_ids[target_index])
                result[key] = max(result.get(key, -1.0), round(similarity, 6))
    return dict(sorted(result.items()))


def _python_top_k_pairs(
    vectors: dict[str, list[float]],
    *,
    item_ids: list[str],
    top_k: int,
    min_similarity: float,
) -> dict[tuple[str, str], float]:
    normalized = {item_id: _normalize(vectors[item_id]) for item_id in item_ids}
    result: dict[tuple[str, str], float] = {}
    for source_id in item_ids:
        ranked: list[tuple[float, str]] = []
        source = normalized[source_id]
        for target_id in item_ids:
            if source_id == target_id:
                continue
            similarity = sum(
                left * right for left, right in zip(source, normalized[target_id], strict=True)
            )
            if similarity >= min_similarity:
                ranked.append((similarity, target_id))
        for similarity, target_id in sorted(
            ranked,
            key=lambda item: (-item[0], item[1]),
        )[:top_k]:
            key = _pair_key(source_id, target_id)
            result[key] = max(result.get(key, -1.0), round(similarity, 6))
    return dict(sorted(result.items()))


def _normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        return [0.0 for _ in vector]
    return [value / norm for value in vector]


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)
