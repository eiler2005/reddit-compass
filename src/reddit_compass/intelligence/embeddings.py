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

# Пороги плотного сходства — свойство модели эмбеддингов, а не глобальная константа.
# У разных моделей разное распределение косинуса: порог 0.92, разумный для E5, на
# статических дистиллированных векторах может отсекать почти всё (или, наоборот,
# сливать всё подряд). Профиль подставляется в params story-релиза ДО вычисления
# params_hash, поэтому релиз остаётся воспроизводимым даже если таблица потом изменится.
#
# Значения получают командой ``engine embeddings calibrate`` на замороженном релизе:
# опорные пары даёт provenance (общий URL / near-duplicate fingerprint), разметка не нужна.
DENSE_THRESHOLD_KEYS: tuple[str, ...] = (
    "dense_candidate_threshold",
    "dense_review_threshold",
    "dense_auto_threshold",
    "semantic_dedup_threshold",
)

# Консервативный профиль для незнакомой модели: пороги E5. Он не «правильный» для
# произвольной модели — он лишь воспроизводит историческое поведение, пока модель
# не откалибрована. Незнакомые модели логируются вызывающей стороной.
_FALLBACK_DENSE_PROFILE: dict[str, float] = {
    "dense_candidate_threshold": 0.68,
    "dense_review_threshold": 0.72,
    "dense_auto_threshold": 0.88,
    "semantic_dedup_threshold": 0.92,
}

DENSE_THRESHOLD_PROFILES: dict[str, dict[str, float]] = {
    # Эталон. Откалиброван практикой: на 7-дневном broad даёт compression ~0.70 без
    # overmerge. Измеренное распределение негативов: медиана 0.780, максимум 0.963 —
    # поэтому 0.68 и 0.72 лежат ниже медианы и фильтрами фактически не являются
    # (retrieval ограничен top-K), а работают только два верхних порога.
    DEFAULT_EMBEDDING_MODEL: dict(_FALLBACK_DENSE_PROFILE),
    # Откалибровано переносом квантилей от E5 на релизе 2026-07-23_2026-07-29-broad-r1
    # (207 provenance-позитивов, 150 000 негативов). У статических векторов совсем другая
    # шкала: медиана негативов 0.048, максимум 0.496. Прежние пороги 0.88/0.92 отсекали
    # ~половину заведомо верных пар (recall 0.54 / 0.51); откалиброванные дают 0.94 / 0.90
    # при той же доле ложных срабатываний.
    LEXICAL_HASH_EMBEDDING_MODEL: {
        "dense_candidate_threshold": 0.0,
        "dense_review_threshold": 0.0,
        "dense_auto_threshold": 0.36,
        "semantic_dedup_threshold": 0.45,
    },
    # Прод-модель ночного прогона (`engine cycle`). Откалибровано тем же переносом
    # квантилей на том же релизе: медиана негативов 0.131, p99.9 = 0.651.
    # Прежние E5-пороги 0.88/0.92 оставляли 63% / 53% provenance-позитивов;
    # откалиброванные — 94% / 86% при той же доле ложных срабатываний.
    MODEL2VEC_DEFAULT: {
        "dense_candidate_threshold": 0.0,
        "dense_review_threshold": 0.0,
        "dense_auto_threshold": 0.59,
        # Опора хвоста здесь тонкая (9 негативов из 150k), поэтому порог округлён вверх.
        "semantic_dedup_threshold": 0.74,
    },
}


def dense_thresholds_for(model_name: str) -> dict[str, float]:
    """Пороги плотного сходства для модели эмбеддингов.

    Незнакомая модель получает консервативный профиль E5 — это сохраняет прежнее
    поведение, но не является калибровкой. Проверить, откалиброван ли профиль,
    можно через :func:`is_dense_profile_calibrated`.
    """
    profile = DENSE_THRESHOLD_PROFILES.get(model_name)
    if profile is None:
        return dict(_FALLBACK_DENSE_PROFILE)
    return dict(profile)


def is_dense_profile_calibrated(model_name: str) -> bool:
    """True, если у модели есть собственный профиль порогов, а не фолбэк."""
    return model_name in DENSE_THRESHOLD_PROFILES


def encoding_prefix_for(model_name: str) -> str:
    """Префикс, который модель ожидает перед текстом при энкодинге.

    E5 (sentence-transformers) требует ``passage:`` / ``query:``; model2vec и
    lexical-hash работают с голым текстом. Префикс входит в ``input_hash`` кэша
    эмбеддингов, чтобы векторы от разных форматов не смешивались.
    """
    if model_name == LEXICAL_HASH_EMBEDDING_MODEL:
        return ""
    if model_name.startswith(_MODEL2VEC_PREFIX):
        return ""
    return "passage: "


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

    prefix = encoding_prefix_for(model_name)
    model_class: Any = module.SentenceTransformer
    model = model_class(model_name)
    vectors: Any = model.encode(
        [f"{prefix}{text}" for text in texts],
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
    score_floor: float | None = None,
) -> dict[tuple[str, str], float]:
    """Return deterministic top-K cosine pairs, using chunked NumPy when available.

    ``score_floor`` добавляет к рангу второй, независимый критерий отбора: пара
    попадает в выдачу, если она в top-K хотя бы для одного из items **или** её
    сходство не ниже порога. Инвариант — *пара, которую скоринг признал бы
    auto-merge, не может быть отброшена по рангу*.

    Зачем: у откалиброванных статических моделей ``min_similarity`` равен 0.0
    (см. ``DENSE_THRESHOLD_PROFILES``), поэтому единственным фильтром остаётся ранг —
    ровно K соседей на item независимо от score. Замер на 7-дневном broad
    (4957 items, ``potion-base-8M``): пар с косинусом ≥ 0.59 (собственный
    ``dense_auto_threshold`` модели) — 49 772, из них при ``top_k=12`` retrieval
    доставал 13 686, то есть 27.5%. У 21.6% items больше 12 соседей выше этого порога,
    и пара с косинусом 0.95, оказавшаяся 13-й для обоих items, отбрасывалась молча.

    ``None`` полностью сохраняет прежнее поведение.
    """
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
            score_floor=score_floor,
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
            selected = list(ranked)
            if score_floor is not None:
                selected.extend(
                    int(index)
                    for index in numpy.nonzero(row >= score_floor)[0]
                    if int(index) != source_index
                )
            for target_index in selected:
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
    score_floor: float | None = None,
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
        ordered = sorted(ranked, key=lambda item: (-item[0], item[1]))
        selected = ordered[:top_k]
        if score_floor is not None:
            selected = selected + [pair for pair in ordered[top_k:] if pair[0] >= score_floor]
        for similarity, target_id in selected:
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
