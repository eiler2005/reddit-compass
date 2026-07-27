"""LLM pipeline: стратифицированный отбор, валидация, retry.

Заменяет global raw-score sort на стратифицированный отбор по clusters.
"""

from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass

from .models import ContentItem

logger = logging.getLogger("reddit_compass")


@dataclass
class SelectionQuota:
    """Квоты для стратифицированного отбора."""

    cluster_quota: int  # 70% N равномерно между clusters
    global_quota: int  # 20% N глобально
    exploration_quota: int  # 10% N exploration


def compute_selection_quotas(total: int) -> SelectionQuota:
    """Вычисляет квоты для отбора."""
    cluster_quota = int(total * 0.70)
    global_quota = int(total * 0.20)
    exploration_quota = total - cluster_quota - global_quota
    return SelectionQuota(
        cluster_quota=cluster_quota,
        global_quota=global_quota,
        exploration_quota=exploration_quota,
    )


def stratified_selection(
    items: list[ContentItem],
    limit: int,
    percentiles: dict[str, float],
    goal_relevance: dict[str, float] | None = None,
    snapshot_date: str = "",
) -> list[ContentItem]:
    """Стратифицированный отбор items для LLM-анализа.

    1. 70% N равномерно между активными source clusters.
    2. 20% N глобально по normalized score.
    3. 10% N deterministic exploration.

    Один provider не может занимать более 40% N, если активны минимум 3 providers.
    """
    if not items or limit <= 0:
        return []

    if len(items) <= limit:
        return items

    quotas = compute_selection_quotas(limit)

    by_cluster: dict[str, list[ContentItem]] = {}
    for item in items:
        by_cluster.setdefault(item.source_cluster, []).append(item)

    active_clusters = list(by_cluster.keys())
    per_cluster = quotas.cluster_quota // len(active_clusters) if active_clusters else 0

    selected: list[ContentItem] = []
    selected_ids: set[str] = set()

    def _score(item: ContentItem) -> float:
        percentile = percentiles.get(item.item_id, 50.0)
        relevance = (goal_relevance or {}).get(item.item_id, 50.0)
        return 0.50 * percentile + 0.35 * relevance + 0.15 * 50.0

    for cluster in active_clusters:
        cluster_items = sorted(by_cluster[cluster], key=_score, reverse=True)
        for item in cluster_items[:per_cluster]:
            if item.item_id not in selected_ids:
                selected.append(item)
                selected_ids.add(item.item_id)

    remaining = [i for i in items if i.item_id not in selected_ids]
    remaining_sorted = sorted(remaining, key=_score, reverse=True)
    for item in remaining_sorted[: quotas.global_quota]:
        if item.item_id not in selected_ids:
            selected.append(item)
            selected_ids.add(item.item_id)

    seed = int(hashlib.sha256(snapshot_date.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    exploration_pool = [i for i in items if i.item_id not in selected_ids]
    if exploration_pool:
        rng.shuffle(exploration_pool)
        for item in exploration_pool[: quotas.exploration_quota]:
            selected.append(item)
            selected_ids.add(item.item_id)

    providers = {i.provider for i in selected}
    if len(providers) >= 3:
        max_per_provider = int(limit * 0.40)
        provider_counts: dict[str, int] = {}
        final: list[ContentItem] = []
        for item in selected:
            count = provider_counts.get(item.provider, 0)
            if count < max_per_provider:
                final.append(item)
                provider_counts[item.provider] = count + 1
        selected = final

    return selected[:limit]


class RetryPolicy:
    """Retry policy для LLM-запросов."""

    def __init__(
        self,
        max_retries: int = 2,
        backoff_base: float = 1.0,
        backoff_max: float = 10.0,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max

    def get_delay(self, attempt: int) -> float:
        """Вычисляет задержку для retry."""
        delay = self.backoff_base * (2**attempt)
        return float(min(delay, self.backoff_max))

    def should_retry(self, attempt: int, error: Exception | None = None) -> bool:
        """Определяет, нужно ли повторить попытку."""
        if attempt >= self.max_retries:
            return False
        if error is None:
            return True
        error_str = str(error).lower()
        if "429" in error_str or "rate limit" in error_str:
            return True
        if "500" in error_str or "502" in error_str or "503" in error_str:
            return True
        return "timeout" in error_str


def build_scope_aware_prompt(item: ContentItem) -> str:
    """Строит prompt с учётом content_scope.

    headline: можно пересказать только headline.
    abstract: нельзя утверждать детали, которых нет в abstract.
    excerpt/full: можно использовать только предоставленный текст.
    """
    scope_instruction = {
        "headline": "Доступен только заголовок. Не добавляй детали сверх заголовка.",
        "abstract": "Доступен abstract. Не утверждай детали, которых нет в abstract.",
        "excerpt": "Доступен excerpt. Используй только предоставленный текст.",
        "full": "Доступен полный текст. Используй только предоставленный текст.",
    }

    instruction = scope_instruction.get(item.content_scope, scope_instruction["headline"])

    return f"""Проанализируй материал:

item_id: {item.item_id}
provider: {item.provider}
content_scope: {item.content_scope}
title: {item.title}
excerpt: {item.excerpt or "отсутствует"}

Инструкция: {instruction}

Верни JSON:
{{
  "item_id": "{item.item_id}",
  "theme_ids": [],
  "pain_points": [],
  "buying_intent": false,
  "goal_relevance": {{"book": 0, "rbc": 0, "business": 0}},
  "summary_ru": ""
}}
"""


def build_repair_prompt(original_response: str, validation_errors: list[str]) -> str:
    """Строит prompt для исправления невалидного ответа."""
    errors_text = "\n".join(f"- {e}" for e in validation_errors)
    return f"""Предыдущий ответ содержит ошибки:

{errors_text}

Исходный ответ:
{original_response}

Исправь ответ, устранив все ошибки. Верни валидный JSON.
"""
