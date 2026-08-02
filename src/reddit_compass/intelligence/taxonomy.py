"""Stable broad-domain taxonomy for Radar.

This layer is deterministic. LLM markup may refine themes later, but every item
must receive at least one broad domain before it reaches Radar.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

DomainId = Literal[
    "ai_technology",
    "labor_career",
    "business_markets",
    "society_politics",
    "world_geopolitics",
    "culture_media",
    "sports",
    "science_health_education",
    "finance_consumer",
    "climate_energy_infrastructure",
    "security_privacy",
    "other",
]


@dataclass(frozen=True)
class DomainDefinition:
    domain_id: DomainId
    label_ru: str
    label_en: str
    keywords: tuple[str, ...]
    source_hints: tuple[str, ...] = ()


BROAD_DOMAINS: dict[DomainId, DomainDefinition] = {
    "ai_technology": DomainDefinition(
        domain_id="ai_technology",
        label_ru="AI и технологии",
        label_en="AI and technology",
        # Фаза 6: убраны generic-слова (model, product, startup, code, software,
        # developer, agent) — они присваивали домен 97.5% корпуса. Оставлены только
        # специфичные термины.
        keywords=(
            "ai",
            "artificial intelligence",
            "llm",
            "gpt",
            "claude",
            "openai",
            "anthropic",
            "robot",
            "automation",
            "chip",
            "programming",
            "machine learning",
            "neural",
        ),
        # Фаза 6: убраны technology/tech/hackernews — источник сам по себе не должен
        # назначать рубрику (любой item с HN получал домен автоматически).
        source_hints=(
            "artificial",
            "singularity",
            "chatgpt",
            "local llama",
            "localllama",
            "machinelearning",
            "openai",
            "techcrunch",
            "theverge",
            "verge",
            "arstechnica",
            "wired",
        ),
    ),
    "labor_career": DomainDefinition(
        domain_id="labor_career",
        label_ru="Труд и карьера",
        label_en="Labor and careers",
        keywords=(
            "job",
            "jobs",
            "career",
            "hiring",
            "layoff",
            "laid off",
            "worker",
            "employee",
            "workplace",
            "salary",
            "resume",
            "recruit",
            "burnout",
            "union",
        ),
        source_hints=(
            "jobs",
            "careers",
            "cscareerquestions",
            "recruitinghell",
            "antiwork",
            "workreform",
            "humanresources",
        ),
    ),
    "business_markets": DomainDefinition(
        domain_id="business_markets",
        label_ru="Бизнес",
        label_en="Business",
        keywords=(
            "business",
            "company",
            "market",
            "earnings",
            "revenue",
            "profit",
            "funding",
            "startup",
            "venture",
            "ipo",
            "merger",
            "acquisition",
            "ceo",
            "enterprise",
            "strategy",
            "pricing",
        ),
        source_hints=(
            "business",
            "smallbusiness",
            "entrepreneur",
            "startups",
            "saas",
            "venture",
            "markets",
            "ft",
            "foxbusiness",
            "americanbanker",
            "producthunt",
        ),
    ),
    "society_politics": DomainDefinition(
        domain_id="society_politics",
        label_ru="Общество и политика",
        label_en="Society and politics",
        keywords=(
            "politic",
            "election",
            "voter",
            "government",
            "policy",
            "law",
            "court",
            "senate",
            "congress",
            "democrat",
            "republican",
            "protest",
            "public",
            "society",
            "police",
        ),
        source_hints=("politics", "news", "askreddit", "changemyview", "foxnews"),
    ),
    "world_geopolitics": DomainDefinition(
        domain_id="world_geopolitics",
        label_ru="Мировые тренды",
        label_en="World and geopolitics",
        keywords=(
            "world",
            "global",
            "geopolitic",
            "geopolitics",
            "war",
            "conflict",
            "china",
            "russia",
            "ukraine",
            "israel",
            "iran",
            "europe",
            "border",
            "diplomat",
            "sanction",
            "nato",
            "trade war",
        ),
        source_hints=("world", "worldnews", "geopolitics", "international"),
    ),
    "culture_media": DomainDefinition(
        domain_id="culture_media",
        label_ru="Культура",
        label_en="Culture and media",
        keywords=(
            "culture",
            "media",
            "creator",
            "creators",
            "hollywood",
            "platform",
            "film",
            "movie",
            "music",
            "streaming",
            "netflix",
            "tiktok",
            "youtube",
            "celebrity",
            "book",
            "art",
            "fashion",
            "meme",
            "gaming",
            "game",
            "internet",
        ),
        source_hints=(
            "culture",
            "movies",
            "music",
            "television",
            "books",
            "gaming",
            "games",
            "popculture",
            "newyorker",
            "vanityfair",
            "style",
            "arts",
        ),
    ),
    "sports": DomainDefinition(
        domain_id="sports",
        label_ru="Спорт",
        label_en="Sport",
        keywords=(
            "sport",
            "football",
            "soccer",
            "nba",
            "nfl",
            "mlb",
            "nhl",
            "tennis",
            "olympic",
            "fifa",
            "uefa",
            "match",
            "league",
            "coach",
            "athlete",
        ),
        source_hints=("sport", "sports", "nba", "nfl", "soccer", "football", "tennis"),
    ),
    "science_health_education": DomainDefinition(
        domain_id="science_health_education",
        label_ru="Наука, здоровье, образование",
        label_en="Science, health and education",
        keywords=(
            "science",
            "research",
            "study",
            "health",
            "healthcare",
            "medicine",
            "doctor",
            "hospital",
            "school",
            "student",
            "university",
            "education",
            "professor",
            "academic",
        ),
        source_hints=("science", "health", "education", "professors", "askacademia"),
    ),
    "finance_consumer": DomainDefinition(
        domain_id="finance_consumer",
        label_ru="Финансы и потребитель",
        label_en="Finance and consumer",
        keywords=(
            "finance",
            "bank",
            "banking",
            "stock",
            "inflation",
            "rate",
            "mortgage",
            "consumer",
            "shopper",
            "price",
            "pricing",
            "debt",
            "loan",
            "credit",
            "wallet",
        ),
        source_hints=("finance", "economics", "investing", "wallstreetbets", "banker"),
    ),
    "climate_energy_infrastructure": DomainDefinition(
        domain_id="climate_energy_infrastructure",
        label_ru="Климат и инфраструктура",
        label_en="Climate, energy and infrastructure",
        keywords=(
            "climate",
            "energy",
            "power",
            "grid",
            "data center",
            "datacenter",
            "water",
            "electric",
            "oil",
            "gas",
            "solar",
            "nuclear",
            "infrastructure",
            "transport",
        ),
        source_hints=("climate", "environment", "energy", "infrastructure"),
    ),
    "security_privacy": DomainDefinition(
        domain_id="security_privacy",
        label_ru="Безопасность и приватность",
        label_en="Security and privacy",
        keywords=(
            "security",
            "privacy",
            "surveillance",
            "camera",
            "tracking",
            "breach",
            "hack",
            "cyber",
            "malware",
            "encryption",
            "police",
            "facial recognition",
            "data leak",
        ),
        source_hints=("privacy", "netsec", "cybersecurity", "security", "surveillance"),
    ),
    "other": DomainDefinition(
        domain_id="other",
        label_ru="Другое",
        label_en="Other",
        keywords=(),
        source_hints=(),
    ),
}

DOMAIN_ORDER: tuple[DomainId, ...] = tuple(BROAD_DOMAINS.keys())
DOMAIN_LABELS_RU: dict[str, str] = {k: v.label_ru for k, v in BROAD_DOMAINS.items()}

_TOKEN_RE = re.compile(r"[a-zа-я0-9$%.-]+", re.IGNORECASE)


def normalize_domain_ids(domain_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    """Return stable, valid domain ids with fallback to other."""
    if not domain_ids:
        return ["other"]
    valid: list[str] = []
    for domain_id in domain_ids:
        if domain_id in BROAD_DOMAINS and domain_id not in valid:
            valid.append(domain_id)
    return valid or ["other"]


def classify_domains(
    title: str,
    excerpt: str = "",
    provider: str = "",
    source_section: str = "",
    keyword: str = "",
    link_flair: str = "",
    max_domains: int = 3,
) -> list[str]:
    """Assign broad Radar domains from source hints and text keywords."""
    source_text = " ".join([provider, source_section, keyword, link_flair]).lower()
    body_text = " ".join([title, excerpt]).lower()
    source_tokens = set(_TOKEN_RE.findall(source_text))
    all_tokens = _TOKEN_RE.findall(f"{source_text} {body_text}")
    token_set = set(all_tokens)
    token_set.update(
        token[:-1]
        for token in all_tokens
        if token.endswith("s") and len(token) > 4 and not token.endswith(("ss", "us", "is", "news"))
    )
    token_text = " ".join(all_tokens)
    scores: dict[str, int] = {}

    for domain_id, domain in BROAD_DOMAINS.items():
        if domain_id == "other":
            continue
        keyword_score = 0
        for kw in domain.keywords:
            normalized_kw = kw.lower()
            if normalized_kw and (
                normalized_kw in token_set
                if " " not in normalized_kw
                else normalized_kw in token_text
            ):
                keyword_score += 2 if " " in kw else 1
        # Фаза 6: источник сам по себе не назначает рубрику. source_hints усиливают
        # домен, только когда есть хотя бы одно текстовое совпадение.
        source_score = 0
        if keyword_score > 0:
            for hint in domain.source_hints:
                normalized_hint = hint.lower()
                if normalized_hint and (
                    normalized_hint in source_tokens
                    if " " not in normalized_hint
                    else normalized_hint in source_text
                ):
                    source_score += 5
        score = keyword_score + source_score
        if domain_id == "sports" and any(
            term in token_text
            for term in ("nba", "nfl", "mlb", "nhl", "fifa", "uefa", "soccer", "tennis")
        ):
            score += 3
        if score:
            scores[domain_id] = score

    if not scores:
        return ["other"]

    ordered = sorted(
        scores.items(),
        key=lambda item: (-item[1], DOMAIN_ORDER.index(item[0])),
    )
    return [domain_id for domain_id, _ in ordered[:max_domains]]


def compute_project_scores(
    domain_ids: list[str],
    title: str = "",
    excerpt: str = "",
) -> dict[str, int]:
    """Compute deterministic Book/RBC/business relevance before LLM refinement."""
    domains = set(normalize_domain_ids(domain_ids))
    text = f"{title} {excerpt}".lower()

    book = 20
    rbc = 20
    business = 20

    if domains & {"ai_technology", "labor_career", "society_politics", "security_privacy"}:
        book += 35
    if domains & {"business_markets", "finance_consumer", "ai_technology", "labor_career"}:
        rbc += 35
        business += 35
    if domains & {"world_geopolitics", "society_politics"}:
        rbc += 20
    if domains & {"culture_media", "sports"}:
        rbc += 10
    if domains & {"climate_energy_infrastructure", "science_health_education"}:
        book += 15
        rbc += 10

    if any(token in text for token in ("layoff", "job", "worker", "career", "automation")):
        book += 15
        rbc += 10
    if any(token in text for token in ("earnings", "market", "revenue", "funding", "debt")):
        rbc += 20
        business += 20
    if any(token in text for token in ("agent", "llm", "openai", "claude", "model")):
        book += 15
        business += 10

    return {
        "book": min(book, 100),
        "rbc": min(rbc, 100),
        "business": min(business, 100),
    }


def stable_hash_id(prefix: str, value: str, length: int = 20) -> str:
    """Build stable ids without Python's process-randomized hash()."""
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:length]}"


# --- Фаза 6: двухуровневая таксономия, квоты ленты, рутина ---


@dataclass(frozen=True)
class Rubric:
    """Верхний уровень рубрикатора — читаемые блоки для /today и навигации."""

    rubric_id: str
    label_ru: str
    emoji: str
    domain_ids: tuple[str, ...]


# Один источник истины по рубрикам выдачи. Второй уровень — 12 тем профиля
# (config/profiles/*.json) как фасетный фильтр внутри рубрики.
RUBRICS: tuple[Rubric, ...] = (
    Rubric("ai_tech", "AI и технологии", "🤖", ("ai_technology",)),
    Rubric("surveillance", "Слежка и приватность", "👁", ("security_privacy",)),
    Rubric("labor", "Труд и карьера", "💼", ("labor_career",)),
    Rubric("business", "Бизнес и рынки", "🏪", ("business_markets", "finance_consumer")),
    Rubric("society", "Общество и политика", "🌍", ("society_politics",)),
    Rubric("world", "Мир и геополитика", "🗺", ("world_geopolitics",)),
    Rubric("culture", "Культура и медиа", "🎭", ("culture_media", "sports")),
    Rubric(
        "science_climate",
        "Наука, здоровье, климат",
        "🔬",
        ("science_health_education", "climate_energy_infrastructure"),
    ),
)

_DOMAIN_TO_RUBRIC: dict[str, str] = {
    domain_id: rubric.rubric_id for rubric in RUBRICS for domain_id in rubric.domain_ids
}


def rubric_for_domains(domain_ids: list[str] | tuple[str, ...] | None) -> str:
    """Верхняя рубрика по приоритету RUBRICS среди доменов item'а."""

    for domain_id in normalize_domain_ids(domain_ids):
        rubric_id = _DOMAIN_TO_RUBRIC.get(domain_id)
        if rubric_id:
            return rubric_id
    return "other"


def apply_reddit_quota(
    items: list[Any],
    *,
    is_reddit: Callable[[Any], bool],
    max_share: float = 0.3,
) -> list[Any]:
    """Квота доли Reddit в ленте «Мир» (Фаза 6).

    Сохраняет порядок, держит долю Reddit ≤ max_share, не отбрасывая не-Reddit.
    Если не-Reddit нет (блок «Нерв Reddit»), квота не применяется.
    """

    if not 0 < max_share < 1:
        raise ValueError("max_share must be in (0, 1)")
    total_non_reddit = sum(1 for item in items if not is_reddit(item))
    if total_non_reddit == 0:
        return list(items)
    allowed_reddit = int(total_non_reddit * max_share / (1.0 - max_share))
    result: list[Any] = []
    reddit_kept = 0
    for item in items:
        if is_reddit(item):
            if reddit_kept < allowed_reddit:
                result.append(item)
                reddit_kept += 1
        else:
            result.append(item)
    return result


_ROUTINE_PATTERNS = re.compile(
    r"\b(injury report|injury update|waiver|waivers|depth chart|roster|lineup|"
    r"starting lineup|box score|final score|game recap|match recap|press release|"
    r"earnings calendar|schedule announced|transactions?|trade rumor|trade rumors|"
    r"dept chart|projection|projections)\b"
    # Результат матча счётом по сетам: «… def (2) E. Svitolina — 6-3, 6-4».
    # Ни один шаблон выше такой заголовок не ловил, и пять теннисных результатов
    # одного провайдера сливались в сюжет — на 2026-08-01 это давало overmerge_ge5.
    # Форма достаточно специфична, чтобы не задевать обычные заголовки с числами.
    r"|\b[0-7]-[0-7],\s*[0-7]-[0-7]\b"
    r"|\b(?:WTA|ATP)\s+\d{2,4}\b",
    re.IGNORECASE,
)
_ROUTINE_SECTIONS = frozenset(
    {"scoreboard", "scores", "standings", "schedule", "transactions", "boxscores", "odds"}
)


def is_routine_beat(title: str, source_section: str = "") -> bool:
    """Детерминированный признак рутинного материала (Фаза 6).

    Рутина (счёта, травмы, депт-чарты, календари, пресс-релизы) остаётся в /news,
    но исключается из candidate generation и trend discovery.
    """

    if source_section.strip().lower() in _ROUTINE_SECTIONS:
        return True
    return bool(_ROUTINE_PATTERNS.search(title or ""))
