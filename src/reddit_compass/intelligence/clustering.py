"""Story clustering: объединение связанных материалов в сюжеты.

Нормализация заголовков, matching по URL/crosspost/title,
story_id generation.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .models import ContentItem, Story

_STOPWORDS_EN = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
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
    "must",
    "can",
    "this",
    "that",
    "these",
    "those",
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "what",
    "which",
    "who",
    "whom",
    "when",
    "where",
    "why",
    "how",
    "not",
    "no",
    "nor",
    "as",
    "if",
    "then",
    "than",
    "too",
    "very",
    "just",
    "about",
    "above",
    "after",
    "again",
    "all",
    "also",
    "am",
    "any",
    "because",
    "before",
    "between",
    "both",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "only",
    "own",
    "same",
    "so",
    "into",
    "over",
    "under",
    "until",
    "up",
    "out",
    "off",
    "down",
    "here",
    "there",
    "once",
    "during",
    "while",
    "its",
}

_STOPWORDS_RU = {
    "и",
    "в",
    "во",
    "не",
    "что",
    "он",
    "на",
    "я",
    "с",
    "со",
    "как",
    "а",
    "то",
    "все",
    "она",
    "так",
    "его",
    "но",
    "да",
    "ты",
    "к",
    "у",
    "же",
    "вы",
    "за",
    "бы",
    "по",
    "ее",
    "мне",
    "было",
    "вот",
    "от",
    "меня",
    "еще",
    "нет",
    "о",
    "из",
    "ему",
    "теперь",
    "когда",
    "даже",
    "ну",
    "вдруг",
    "ли",
    "если",
    "уже",
    "или",
    "ни",
    "быть",
    "был",
    "него",
    "до",
    "вас",
    "нибудь",
    "опять",
    "уж",
    "вам",
    "ведь",
    "там",
    "потом",
    "себя",
    "ничего",
    "ей",
    "может",
    "они",
    "тут",
    "где",
    "есть",
    "надо",
    "ней",
    "для",
    "мы",
    "тебя",
    "их",
    "чем",
    "была",
    "сам",
    "чтоб",
    "без",
    "будто",
    "чего",
    "раз",
    "тоже",
    "себе",
    "под",
    "будет",
    "тогда",
    "кто",
    "этот",
    "того",
    "потому",
    "этого",
    "какой",
    "ним",
    "этом",
    "мой",
    "тем",
    "чтобы",
    "нее",
    "были",
    "куда",
    "зачем",
    "всех",
    "можно",
    "при",
    "об",
    "этой",
    "перед",
    "иногда",
    "лучше",
    "чуть",
    "том",
    "нельзя",
    "такой",
    "им",
    "более",
    "всегда",
    "конечно",
    "всю",
    "между",
}

_ENTITY_PATTERN = re.compile(
    r"\b("
    r"[A-Z][a-z]+(?:[A-Z][a-z]+)*"  # CamelCase names
    r"|\b\d+(?:\.\d+)?(?:%|bn|mn|m|b|k)?\b"  # Numbers
    r"|\$[\d,.]+(?:bn|mn|m|b)?\b"  # Currency
    r"|\b[A-Z]{2,}\b"  # Acronyms
    r")"
)


def normalize_title(title: str, provider: str = "") -> str:
    """Нормализует заголовок для сравнения.

    1. Unicode NFKC.
    2. Lowercase.
    3. Удалить punctuation и повторные spaces.
    4. Удалить URL и publisher suffix после |.
    5. Токены короче 3 символов исключить, кроме ai.
    6. Удалить stopword set.
    7. Числа, суммы и имена компаний сохранить.
    """
    text = unicodedata.normalize("NFKC", title)
    text = text.lower()

    if "|" in text:
        parts = text.split("|")
        suffix = parts[-1].strip()
        if provider and provider.lower() in suffix:
            text = "|".join(parts[:-1])
        elif len(parts) > 1:
            text = parts[0]

    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    tokens = text.split()
    filtered = []
    for token in tokens:
        if len(token) < 3 and token != "ai":
            continue
        if token in _STOPWORDS_EN or token in _STOPWORDS_RU:
            continue
        filtered.append(token)

    return " ".join(filtered)


def extract_tokens(normalized: str) -> set[str]:
    """Извлекает токены из нормализованного заголовка."""
    return set(normalized.split())


def extract_entities(title: str) -> set[str]:
    """Извлекает entity-like токены (имена, числа, валюты, акронимы)."""
    return {m.group().lower() for m in _ENTITY_PATTERN.finditer(title)}


def token_jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity между двумя наборами токенов."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def title_similarity(
    title_a: str, title_b: str, provider_a: str = "", provider_b: str = ""
) -> float:
    """Вычисляет similarity между двумя заголовками.

    similarity = 0.6 * token_jaccard + 0.4 * fuzzy_ratio
    """
    norm_a = normalize_title(title_a, provider_a)
    norm_b = normalize_title(title_b, provider_b)

    tokens_a = extract_tokens(norm_a)
    tokens_b = extract_tokens(norm_b)

    jaccard = token_jaccard(tokens_a, tokens_b)
    fuzzy = fuzz.token_set_ratio(norm_a, norm_b) / 100.0

    return 0.6 * jaccard + 0.4 * fuzzy


def _canonical_key_from_tokens(tokens: list[str]) -> str:
    """Строит canonical key из 5 наиболее информативных токенов."""
    informative = [
        t for t in tokens if len(t) >= 3 and t not in _STOPWORDS_EN and t not in _STOPWORDS_RU
    ]
    key_tokens = informative[:5] if len(informative) >= 5 else tokens[:5]
    return " ".join(sorted(key_tokens))


def generate_story_id(canonical_key: str) -> str:
    """Генерирует стабильный story_id из canonical key."""
    return "story_" + hashlib.sha256(canonical_key.encode()).hexdigest()[:20]


@dataclass
class StoryCluster:
    """Внутреннее представление кластера в процессе кластеризации."""

    story_id: str
    canonical_key: str
    title: str
    item_ids: list[str] = field(default_factory=list)
    canonical_urls: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)
    entities: set[str] = field(default_factory=set)
    first_seen: str = ""
    last_seen: str = ""


class StoryClusterer:
    """Кластеризует items в stories.

    Порядок matching:
    1. Exact canonical URL.
    2. Cross-post canonical target.
    3. Title match (similarity >= 0.72 или >= 0.62 + entity match).
    """

    SIMILARITY_THRESHOLD = 0.72
    SIMILARITY_THRESHOLD_WITH_ENTITY = 0.62
    AMBIGUITY_MARGIN = 0.03
    TITLE_MATCH_WINDOW_DAYS = 14

    def __init__(self) -> None:
        self._clusters: dict[str, StoryCluster] = {}
        self._url_to_story: dict[str, str] = {}
        self._ambiguity_count = 0

    @property
    def ambiguity_count(self) -> int:
        return self._ambiguity_count

    def add_item(self, item: ContentItem) -> str:
        """Добавляет item, возвращает story_id."""
        story_id = self._find_matching_story(item)

        if story_id:
            self._add_to_cluster(story_id, item)
            return story_id

        return self._create_new_story(item)

    def _find_matching_story(self, item: ContentItem) -> str | None:
        if item.canonical_url and item.canonical_url in self._url_to_story:
            return self._url_to_story[item.canonical_url]

        candidates: list[tuple[str, float]] = []
        item_entities = extract_entities(item.title)

        for story_id, cluster in self._clusters.items():
            if item.canonical_url and item.canonical_url in cluster.canonical_urls:
                return story_id

            similarity = title_similarity(item.title, cluster.title, item.provider)
            matches_threshold = similarity >= self.SIMILARITY_THRESHOLD
            matches_with_entity = (
                similarity >= self.SIMILARITY_THRESHOLD_WITH_ENTITY
                and item_entities & cluster.entities
            )
            if matches_threshold or matches_with_entity:
                candidates.append((story_id, similarity))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[1], reverse=True)

        if len(candidates) >= 2:
            best_score = candidates[0][1]
            second_score = candidates[1][1]
            if best_score - second_score < self.AMBIGUITY_MARGIN:
                self._ambiguity_count += 1
                return None

        return candidates[0][0]

    def _add_to_cluster(self, story_id: str, item: ContentItem) -> None:
        cluster = self._clusters[story_id]
        if item.item_id not in cluster.item_ids:
            cluster.item_ids.append(item.item_id)
        if item.canonical_url:
            cluster.canonical_urls.add(item.canonical_url)
        cluster.tokens.update(extract_tokens(normalize_title(item.title, item.provider)))
        cluster.entities.update(extract_entities(item.title))
        if item.snapshot_date:
            if not cluster.first_seen or item.snapshot_date < cluster.first_seen:
                cluster.first_seen = item.snapshot_date
            if not cluster.last_seen or item.snapshot_date > cluster.last_seen:
                cluster.last_seen = item.snapshot_date

    def _create_new_story(self, item: ContentItem) -> str:
        normalized = normalize_title(item.title, item.provider)
        tokens = list(extract_tokens(normalized))
        canonical_key = _canonical_key_from_tokens(tokens)
        story_id = generate_story_id(canonical_key)

        if story_id in self._clusters:
            self._add_to_cluster(story_id, item)
            return story_id

        cluster = StoryCluster(
            story_id=story_id,
            canonical_key=canonical_key,
            title=item.title,
            item_ids=[item.item_id],
            canonical_urls={item.canonical_url} if item.canonical_url else set(),
            tokens=extract_tokens(normalized),
            entities=extract_entities(item.title),
            first_seen=item.snapshot_date,
            last_seen=item.snapshot_date,
        )
        self._clusters[story_id] = cluster

        if item.canonical_url:
            self._url_to_story[item.canonical_url] = story_id

        return story_id

    def get_stories(self) -> list[Story]:
        """Возвращает все stories."""
        return [
            Story(
                story_id=c.story_id,
                canonical_key=c.canonical_key,
                title=c.title,
                item_ids=list(c.item_ids),
                first_seen=c.first_seen,
                last_seen=c.last_seen,
            )
            for c in self._clusters.values()
        ]


def cluster_items(items: list[ContentItem]) -> tuple[list[Story], int]:
    """Кластеризует список items. Возвращает (stories, ambiguity_count)."""
    clusterer = StoryClusterer()
    for item in items:
        clusterer.add_item(item)
    return clusterer.get_stories(), clusterer.ambiguity_count
