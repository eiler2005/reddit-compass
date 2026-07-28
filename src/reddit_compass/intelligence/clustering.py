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
from .taxonomy import compute_project_scores, normalize_domain_ids, stable_hash_id

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


_GENERIC_PREFIXES = {
    "opinion",
    "analysis",
    "live",
    "live updates",
    "latest news",
    "top stories",
    "morning briefing",
    "newsletter",
    "sign up",
    "methodology",
    "tech life",
    "tech now",
    "podcast",
    "breaking",
    "breaking news",
    "updated",
    "update",
    "exclusive",
}

_PUBLISHER_SUFFIXES = {
    "nytimes": ["the new york times", "new york times", "nyt"],
    "washingtonpost": ["the washington post", "washington post", "wapo"],
    "theverge": ["the verge", "verge"],
    "bbc": ["bbc"],
    "guardian": ["the guardian", "guardian"],
    "reuters": ["reuters"],
    "ft": ["financial times", "ft"],
    "techcrunch": ["techcrunch", "tech crunch"],
    "arstechnica": ["ars technica"],
    "wired": ["wired"],
    "usatoday": ["usa today"],
    "foxbusiness": ["fox business"],
    "americanbanker": ["american banker"],
    "medium": ["medium"],
    "time": ["time"],
    "vanityfair": ["vanity fair"],
    "newyorker": ["the new yorker", "new yorker"],
    "foxnews": ["fox news"],
}


def _strip_publisher_suffix(text: str, provider: str) -> str:
    """Удаляет trailing publisher suffix вида '- The New York Times'."""
    suffixes = _PUBLISHER_SUFFIXES.get(provider, [])
    # Also try generic suffixes from all providers
    all_suffixes = set()
    for s_list in _PUBLISHER_SUFFIXES.values():
        all_suffixes.update(s_list)
    all_suffixes.update(suffixes)

    for suffix in sorted(all_suffixes, key=len, reverse=True):
        # Match "- suffix" or "| suffix" at end
        pattern = rf"[\s\-–—|]\s*{re.escape(suffix)}\s*$"
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text.strip()


def normalize_title(title: str, provider: str = "") -> str:
    """Нормализует заголовок для сравнения.

    1. Unicode NFKC.
    2. Lowercase.
    3. Удалить trailing publisher suffix.
    4. Обработать | separator: generic prefix → use right; publisher suffix → use left.
    5. Удалить URL и punctuation.
    6. Токены короче 3 символов исключить, кроме ai.
    7. Удалить stopword set.
    """
    text = unicodedata.normalize("NFKC", title)
    text = text.lower()

    # Strip trailing publisher suffix first
    if provider:
        text = _strip_publisher_suffix(text, provider)

    # Handle | separator
    if "|" in text:
        parts = [p.strip() for p in text.split("|")]
        left = parts[0] if parts else ""
        right = parts[1] if len(parts) > 1 else ""

        left_is_generic = left in _GENERIC_PREFIXES
        right_is_publisher = any(
            right == s or right.endswith(s)
            for s_list in _PUBLISHER_SUFFIXES.values()
            for s in s_list
        )

        if left_is_generic and right:
            text = right
        elif (right_is_publisher or (provider and provider.lower() in right)) and left:
            text = left
        elif left and right:
            # Both meaningful — keep both but prefer left
            text = left
        elif left:
            text = left

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


_LOW_SIGNAL_PATTERNS = [
    r"^sign\s+up",
    r"^subscribe",
    r"^newsletter",
    r"^methodology",
    r"^tech\s+life$",
    r"^tech\s+now$",
    r"^morning\s+briefing",
    r"^podcast",
    r"^live\s+updates?$",
    r"^latest\s+news$",
    r"^top\s+stories$",
    r"^breaking\s+news?$",
]
_LOW_SIGNAL_RE = re.compile("|".join(_LOW_SIGNAL_PATTERNS), re.IGNORECASE)


def is_generic_title(normalized_title: str) -> bool:
    """True если заголовок слишком общий для title-only merge."""
    return normalized_title in _GENERIC_PREFIXES or len(normalized_title.split()) < 3


def is_low_signal_title(title: str) -> bool:
    """True если материал — newsletter/methodology/listing/podcast."""
    return bool(_LOW_SIGNAL_RE.search(title.strip()))


def is_radar_ready(
    source_count: int,
    item_count: int,
    trend_score: float,
    confidence: str,
    title: str,
) -> bool:
    """True если story достаточно значим для Radar top секций."""
    return (
        source_count >= 2
        or item_count >= 2
        or (trend_score >= 60 and confidence != "low" and not is_low_signal_title(title))
    )


def extract_tokens(normalized: str) -> set[str]:
    """Извлекает токены из нормализованного заголовка."""
    return set(normalized.split())


def extract_ordered_tokens(normalized: str) -> list[str]:
    """Извлекает токены в порядке следования (детерминированно)."""
    return normalized.split()


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
    """Строит canonical key из первых 5 информативных токенов (ordered)."""
    informative = [
        t for t in tokens if len(t) >= 3 and t not in _STOPWORDS_EN and t not in _STOPWORDS_RU
    ]
    key_tokens = informative[:5] if len(informative) >= 5 else tokens[:5]
    return " ".join(key_tokens)


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
    domain_ids: set[str] = field(default_factory=set)
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
        self._token_to_clusters: dict[str, set[str]] = {}
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
        # URL matching always allowed
        for url in self._match_urls(item):
            if url in self._url_to_story:
                return self._url_to_story[url]

        # Generic/low-signal titles: no title-only merge
        normalized = normalize_title(item.title, item.provider)
        if is_generic_title(normalized) or is_low_signal_title(item.title):
            return None

        # Minimum meaningful tokens guard
        token_count = len(normalized.split())
        item_entities = extract_entities(item.title)
        item_tokens = extract_tokens(normalized)

        # Inverted index: only check clusters sharing at least one token
        candidate_cluster_ids: set[str] = set()
        for token in item_tokens:
            candidate_cluster_ids.update(self._token_to_clusters.get(token, set()))

        candidates: list[tuple[str, float]] = []

        for story_id in candidate_cluster_ids:
            cluster = self._clusters.get(story_id)
            if cluster is None:
                continue
            # URL matching always allowed
            if cluster.canonical_urls & self._match_urls(item):
                return story_id

            # Cluster-side generic guard: don't merge by title into generic clusters
            cluster_normalized = normalize_title(cluster.title)
            if is_generic_title(cluster_normalized) or is_low_signal_title(cluster.title):
                continue

            similarity = title_similarity(item.title, cluster.title, item.provider)
            has_entity_overlap = bool(item_entities & cluster.entities)

            # Short titles without entity overlap: require higher threshold
            if token_count < 4 and not has_entity_overlap and similarity < 0.85:
                continue

            matches_threshold = similarity >= self.SIMILARITY_THRESHOLD
            matches_with_entity = (
                similarity >= self.SIMILARITY_THRESHOLD_WITH_ENTITY and has_entity_overlap
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
        cluster.canonical_urls.update(self._match_urls(item))
        cluster.domain_ids.update(normalize_domain_ids(item.domain_ids))
        new_tokens = extract_tokens(normalize_title(item.title, item.provider))
        cluster.tokens.update(new_tokens)
        for token in new_tokens:
            self._token_to_clusters.setdefault(token, set()).add(story_id)
        cluster.entities.update(extract_entities(item.title))
        if item.snapshot_date:
            if not cluster.first_seen or item.snapshot_date < cluster.first_seen:
                cluster.first_seen = item.snapshot_date
            if not cluster.last_seen or item.snapshot_date > cluster.last_seen:
                cluster.last_seen = item.snapshot_date

    def _create_new_story(self, item: ContentItem) -> str:
        normalized = normalize_title(item.title, item.provider)

        # Generic/low-signal titles: use URL-based ID to prevent false merges
        if is_generic_title(normalized) or is_low_signal_title(item.title):
            url = item.canonical_url or item.item_id
            canonical_key = f"url:{url}"
            story_id = generate_story_id(canonical_key)
        else:
            tokens = extract_ordered_tokens(normalized)
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
            canonical_urls=self._match_urls(item),
            domain_ids=set(normalize_domain_ids(item.domain_ids)),
            tokens=extract_tokens(normalized),
            entities=extract_entities(item.title),
            first_seen=item.snapshot_date,
            last_seen=item.snapshot_date,
        )
        self._clusters[story_id] = cluster

        # Update inverted index
        for token in cluster.tokens:
            self._token_to_clusters.setdefault(token, set()).add(story_id)

        for url in self._match_urls(item):
            self._url_to_story[url] = story_id

        return story_id

    def _match_urls(self, item: ContentItem) -> set[str]:
        urls = {
            url
            for url in (item.canonical_url, item.target_url)
            if url and not url.startswith("https://www.reddit.com/")
        }
        if not urls and item.canonical_url:
            urls.add(item.canonical_url)
        return urls

    def get_stories(self) -> list[Story]:
        """Возвращает все stories."""
        return [
            Story(
                story_id=c.story_id,
                canonical_key=c.canonical_key,
                title=c.title,
                domain_ids=normalize_domain_ids(list(c.domain_ids)),
                trend_id=stable_hash_id("trend", c.canonical_key),
                lifecycle="new",
                project_scores=compute_project_scores(list(c.domain_ids), c.title),
                item_ids=list(c.item_ids),
                first_seen=c.first_seen,
                last_seen=c.last_seen,
            )
            for c in self._clusters.values()
        ]

    def seed_from_stories(self, stories: list[Story]) -> None:
        """Загружает существующие stories для cross-date matching."""
        for story in stories:
            tokens = extract_tokens(normalize_title(story.title))
            entities = extract_entities(story.title)
            cluster = StoryCluster(
                story_id=story.story_id,
                canonical_key=story.canonical_key,
                title=story.title,
                item_ids=list(story.item_ids),
                canonical_urls=set(),
                domain_ids=set(normalize_domain_ids(story.domain_ids)),
                tokens=tokens,
                entities=entities,
                first_seen=story.first_seen,
                last_seen=story.last_seen,
            )
            self._clusters[story.story_id] = cluster
            # Update inverted index
            for token in tokens:
                self._token_to_clusters.setdefault(token, set()).add(story.story_id)


def cluster_items(items: list[ContentItem]) -> tuple[list[Story], int]:
    """Кластеризует список items. Возвращает (stories, ambiguity_count)."""
    clusterer = StoryClusterer()
    for item in items:
        clusterer.add_item(item)
    return clusterer.get_stories(), clusterer.ambiguity_count


def cluster_items_with_history(
    items: list[ContentItem],
    existing_stories: list[Story],
) -> tuple[list[Story], int]:
    """Кластеризует items с учётом истории stories из предыдущих runs."""
    clusterer = StoryClusterer()
    if existing_stories:
        clusterer.seed_from_stories(existing_stories)
    for item in items:
        clusterer.add_item(item)
    return clusterer.get_stories(), clusterer.ambiguity_count
