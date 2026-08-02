"""Слой Trends v2: кластеризация эмбеддингов историй (Фаза 5).

Заменяет граф feature-ключей (``_discover_trends_graph``), который выдавал тренды вида
«Паттерн: fall» и дубли «Боль: regulatory friction». Здесь:

- истории кластеризуются по векторной близости (эмбеддинг или хэш-вектор заголовка);
- имя строится через c-TF-IDF по заголовкам историй кластера — многословный
  различающий терм вместо одного глагола;
- дедупликация кластеров по пересечению множеств историй (Jaccard ≥ 0.5);
- обязательная производная: тренд должен расти по дням, иначе это рубрика;
- confidence раскладывается на компоненты (объём, кросс-источники, разброс дат);
- ``source_scope`` — обязательное поле (cross_source / community_only / mainstream_only).

Модуль самодостаточен (stdlib + numpy) и не импортирует ``engine`` — это шаг к
разрезанию монолита (Фаза 8).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from .clustering import _PUBLISHER_ENTITIES

_VECTOR_DIM = 256

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'_-]*", re.IGNORECASE)

# Высокочастотные слова, не несущие тренд-смысла.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "while",
        "with",
        "without",
        "for",
        "from",
        "into",
        "onto",
        "over",
        "under",
        "after",
        "before",
        "amid",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
        "will",
        "would",
        "can",
        "could",
        "should",
        "may",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "as",
        "at",
        "by",
        "on",
        "in",
        "of",
        "to",
        "up",
        "out",
        "no",
        "not",
        "new",
        "more",
        "most",
        "than",
        "then",
        "they",
        "them",
        "their",
        "we",
        "our",
        "you",
        "your",
        "he",
        "she",
        "his",
        "her",
        "who",
        "what",
        "when",
        "where",
        "why",
        "how",
        "which",
        "says",
        "said",
        "just",
        "like",
        "about",
        "year",
        "years",
        "day",
        "days",
        "week",
        "weeks",
        "report",
        "reports",
        "following",
        "according",
        "get",
        "got",
        "make",
        "made",
    }
)

# Голые глаголы и одиночные токены не являются трендом (дефект «Паттерн: fall»).
_BARE_VERBS = frozenset(
    {
        "fall",
        "falls",
        "rise",
        "rises",
        "raise",
        "raises",
        "drop",
        "drops",
        "warn",
        "warns",
        "reject",
        "rejects",
        "grow",
        "grows",
        "cut",
        "cuts",
        "launch",
        "launches",
        "release",
        "releases",
        "ban",
        "bans",
        "sue",
        "sues",
        "buy",
        "buys",
        "sell",
        "sells",
        "hire",
        "hiring",
        "delay",
        "delays",
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
        "president trump",
    }
)


def _tokenize(title: str) -> list[str]:
    return [
        token.lower()
        for token in _TOKEN_RE.findall(title)
        if len(token) > 1 and token.lower() not in _STOPWORDS and not token.isdigit()
    ]


def _normalize_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 5:
        return f"{token[:-3]}y"
    if token.endswith("s") and len(token) > 4 and not token.endswith(("ss", "us", "is", "news")):
        return token[:-1]
    return token


def _hashed_vector(tokens: Sequence[str]) -> np.ndarray:
    vector = np.zeros(_VECTOR_DIM, dtype=float)
    for token in tokens:
        digest = hashlib.sha256(_normalize_token(token).encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % _VECTOR_DIM
        vector[index] += 1.0
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _story_vector(
    story: dict[str, Any],
    item_ids: list[str],
    vectors_by_item: Mapping[str, Sequence[float]] | None,
) -> np.ndarray:
    if vectors_by_item:
        member_vectors = [
            np.asarray(vectors_by_item[item_id], dtype=float)
            for item_id in item_ids
            if item_id in vectors_by_item
        ]
        if member_vectors:
            mean = np.mean(np.vstack(member_vectors), axis=0)
            norm = float(np.linalg.norm(mean))
            return mean / norm if norm > 0 else mean
    return _hashed_vector(_tokenize(str(story.get("title") or "")))


def _date_key(value: str) -> str:
    return str(value or "")[:10]


def _ctfidf_name(cluster_titles: list[str], corpus_titles: list[list[str]]) -> str:
    """Имя кластера через c-TF-IDF: самые различающие термы + биграммы."""
    stop = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "because",
        "but",
        "and",
        "or",
        "if",
        "while",
        "about",
        "against",
        "up",
        "down",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "they",
        "them",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "new",
        "one",
        "like",
        "get",
        "got",
        "make",
        "made",
        "say",
        "said",
    }
    # Названия изданий — не паттерн, а подпись источника: «The New York Times» и
    # «The Athletic» в заголовках давали тренду имя «york time … athletic york».
    # Список общий с кросс-источниковым слиянием, чтобы он был один на репозиторий.
    stop |= _PUBLISHER_ENTITIES
    stop |= {"york", "times", "athletic", "post", "journal", "news", "daily", "weekly"}
    cluster_tokens: Counter[str] = Counter()
    cluster_bigrams: Counter[str] = Counter()
    for title in cluster_titles:
        tokens = [
            _normalize_token(t)
            for t in _tokenize(title)
            if _normalize_token(t) not in stop and len(_normalize_token(t)) > 2
        ]
        cluster_tokens.update(tokens)
        for i in range(len(tokens) - 1):
            cluster_bigrams[f"{tokens[i]} {tokens[i + 1]}"] += 1
    if not cluster_tokens:
        return ""
    total = sum(cluster_tokens.values())
    n_clusters = max(len(corpus_titles), 1)
    doc_freq: Counter[str] = Counter()
    doc_freq_bi: Counter[str] = Counter()
    for tokens in corpus_titles:
        clean = [
            _normalize_token(t)
            for t in tokens
            if _normalize_token(t) not in stop and len(_normalize_token(t)) > 2
        ]
        doc_freq.update(set(clean))
        for i in range(len(clean) - 1):
            doc_freq_bi[f"{clean[i]} {clean[i + 1]}"] += 1
    scored: list[tuple[float, str]] = []
    for term, count in cluster_tokens.items():
        tf = count / total
        idf = math.log((1 + n_clusters) / (1 + doc_freq.get(term, 0))) + 1.0
        scored.append((tf * idf, term))
    for term, count in cluster_bigrams.items():
        tf = count / total
        idf = math.log((1 + n_clusters) / (1 + doc_freq_bi.get(term, 0))) + 1.0
        scored.append((tf * idf * 1.3, term))  # bigram bonus
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    # Униграммы и биграммы соревнуются в одном списке, поэтому «york time», «york» и
    # «time» попадали в топ одновременно и склеивались в «york time york time athletic
    # york» — на 2026-08-01 два таких имени дали ``trends_duplicate_name_count``.
    # Берём терм только если он приносит хотя бы один новый токен.
    terms: list[str] = []
    used: set[str] = set()
    for _, term in scored:
        tokens = term.split()
        if any(token in used for token in tokens):
            continue
        terms.append(term)
        used.update(tokens)
        if len(terms) >= 4:
            break
    return " ".join(terms)


def _is_specific_name(name: str) -> bool:
    tokens = name.split()
    if not tokens:
        return False
    if len(tokens) == 1 and (tokens[0] in _BARE_VERBS or len(tokens[0]) <= 3):
        return False
    if name in _GENERIC_PHRASES:
        return False
    return not all(token in _BARE_VERBS for token in tokens)


def _source_scope(providers: list[str]) -> str:
    unique = set(providers)
    if not unique:
        return "community_only"
    reddit_only = unique == {"reddit"}
    if reddit_only:
        return "community_only"
    if "reddit" not in unique:
        return "mainstream_only"
    return "cross_source"


def _lifecycle(early: int, late: int) -> str:
    if late > early * 2:
        return "emerging"
    if late > early:
        return "growing"
    if late < early:
        return "peaked"
    return "steady"


def discover_trends(
    stories: list[dict[str, Any]],
    item_ids_by_story: dict[str, list[str]],
    provider_by_item: dict[str, str],
    *,
    vectors_by_item: Mapping[str, Sequence[float]] | None = None,
    min_stories: int = 3,
    min_dates: int = 2,
    cluster_threshold: float = 0.55,
    max_cluster_ratio: float = 0.25,
    max_cluster_abs: int = 100,
) -> list[dict[str, Any]]:
    """Строит тренды поверх историй. Каждый элемент — тренд + список memberships."""

    if not stories:
        return []
    vectors = {
        str(story["story_id"]): _story_vector(
            story, item_ids_by_story.get(str(story["story_id"]), []), vectors_by_item
        )
        for story in stories
    }
    corpus_titles = [_tokenize(str(story.get("title") or "")) for story in stories]

    # Жадная агломерация: история идёт в первый кластер с центроидом ≥ порога.
    clusters: list[list[int]] = []
    centroids: list[np.ndarray] = []
    for index, story in enumerate(stories):
        vector = vectors[str(story["story_id"])]
        placed = False
        for cluster_index, centroid in enumerate(centroids):
            if _cosine(vector, centroid) >= cluster_threshold:
                clusters[cluster_index].append(index)
                size = len(clusters[cluster_index])
                centroids[cluster_index] = (centroid * (size - 1) + vector) / size
                placed = True
                break
        if not placed:
            clusters.append([index])
            centroids.append(vector)

    # Дедупликация кластеров по пересечению множеств историй (Jaccard ≥ 0.5).
    clusters = _dedupe_clusters(clusters)

    # Тренд ≠ весь корпус: кластер считается темой/«blob» и отбрасывается, только если
    # он одновременно велик по доле корпуса И по абсолютному размеру (иначе в маленьком
    # корпусе «весь корпус = одна тема» ложно выглядел бы как blob). Защищает и от
    # вырожденного хэш-fallback без плотных эмбеддингов.
    ratio_cap = max(int(len(stories) * max_cluster_ratio), min_stories)

    trends: list[dict[str, Any]] = []
    for cluster in clusters:
        if len(cluster) < min_stories:
            continue
        if len(cluster) > ratio_cap and len(cluster) > max_cluster_abs:
            continue
        member_stories = [stories[i] for i in cluster]
        dates = sorted({_date_key(str(s.get("first_seen") or "")) for s in member_stories})
        dates = [d for d in dates if d]
        if len(dates) < min_dates:
            continue
        # Производная: во второй половине окна историй не меньше, чем в первой.
        midpoint = dates[len(dates) // 2]
        story_dates = [_date_key(str(s.get("first_seen") or "")) for s in member_stories]
        early = sum(1 for seen in story_dates if seen < midpoint)
        late = len(member_stories) - early
        if late < early or late < 1:
            continue
        titles = [str(s.get("title") or "") for s in member_stories]
        name = _ctfidf_name(titles, corpus_titles)
        if not _is_specific_name(name):
            continue
        item_ids = [
            item_id
            for story in member_stories
            for item_id in item_ids_by_story.get(str(story["story_id"]), [])
        ]
        providers = [provider_by_item.get(item_id, "") for item_id in item_ids]
        providers = [p for p in providers if p]
        source_scope = _source_scope(providers)
        confidence, components = _confidence(
            story_count=len(member_stories),
            distinct_providers=len(set(providers)),
            distinct_dates=len(dates),
        )
        domain_ids = sorted(
            {domain for story in member_stories for domain in _story_domains(story)}
        ) or ["other"]
        project_scores = _merge_project_scores(member_stories)
        story_ids = [str(s["story_id"]) for s in member_stories]
        trend_id = _stable_id("trend", name, *sorted(story_ids))
        memberships = [
            (story_id, round(confidence, 4), "embedding_cluster") for story_id in story_ids
        ]
        trends.append(
            {
                "trend_id": trend_id,
                "name_ru": name,
                "pattern": name,
                "domain_ids": domain_ids,
                "confidence": round(confidence, 4),
                "confidence_components": components,
                "lifecycle": _lifecycle(early, late),
                "source_scope": source_scope,
                "first_seen": dates[0],
                "last_seen": dates[-1],
                "story_count": len(member_stories),
                "source_count": len(set(providers)),
                "project_scores": project_scores,
                "evidence_story_ids": story_ids,
                "counterpoints": [],
                "review_status": "pending",
                "review_id": "",
                "memberships": memberships,
            }
        )
    trends.sort(key=lambda trend: (-trend["confidence"], -trend["story_count"]))
    return trends


def _dedupe_clusters(clusters: list[list[int]]) -> list[list[int]]:
    """Сливает кластеры с Jaccard ≥ 0.5 по индексам историй (больший поглощает)."""

    sets = [set(cluster) for cluster in clusters]
    merged: list[set[int]] = []
    for current in sets:
        absorbed = False
        for existing in merged:
            intersection = len(current & existing)
            union = len(current | existing)
            if union and intersection / union >= 0.5:
                existing.update(current)
                absorbed = True
                break
        if not absorbed:
            merged.append(set(current))
    return [sorted(cluster) for cluster in merged]


def _confidence(
    *,
    story_count: int,
    distinct_providers: int,
    distinct_dates: int,
) -> tuple[float, dict[str, float]]:
    volume = min(1.0, story_count / 10.0)
    cross_source = min(1.0, distinct_providers / 3.0)
    day_spread = min(1.0, distinct_dates / 5.0)
    combined = 0.4 * volume + 0.3 * cross_source + 0.3 * day_spread
    components = {
        "volume": round(volume, 4),
        "cross_source": round(cross_source, 4),
        "day_spread": round(day_spread, 4),
    }
    return round(min(0.99, combined), 4), components


def _story_domains(story: Mapping[str, Any]) -> list[str]:
    """Рубрики истории из строки ``engine_stories``.

    Истории приходят сырыми строками SQLite, где ``domain_ids`` — JSON-текст.
    Итерация по нему давала не рубрики, а отдельные символы: у тренда
    ``my ai job me`` в чипах оказались ``"``, ``,``, ``[``, ``_``, ``a``, ``b``…
    Граф-путь этой ошибки не допускал, потому что везде разбирал JSON явно.
    """
    value = story.get("domain_ids")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [str(domain) for domain in value if isinstance(domain, str) and domain]


def _merge_project_scores(stories: list[dict[str, Any]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for story in stories:
        scores = story.get("project_scores") or {}
        # Тот же сырой JSON, что и у domain_ids: без разбора оценки проектов
        # молча терялись — тренд получал пустой project_scores.
        if isinstance(scores, str):
            try:
                scores = json.loads(scores)
            except (TypeError, ValueError):
                continue
        if not isinstance(scores, dict):
            continue
        for project, score in scores.items():
            try:
                merged[str(project)] = max(merged.get(str(project), 0), int(score))
            except (TypeError, ValueError):
                continue
    return merged


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"
