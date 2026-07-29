"""Optional local embeddings for Story/Trend candidate retrieval.

The engine remains usable without this extra. When ``sentence-transformers`` is
installed, vectors are generated locally and persisted by the engine. Candidate
retrieval keeps only top-K neighbours per item; it never materializes every pair.
"""

from __future__ import annotations

import importlib
import math
from collections.abc import Sequence
from typing import Any

DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"


def encode_passages(
    texts: list[str],
    *,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = 32,
) -> list[list[float]]:
    """Encode passages locally, failing clearly when the optional extra is absent."""
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
