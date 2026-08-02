"""Тренды как повторяющаяся схема события, а не как кластер похожих слов.

Задача имеет имя: **event schema induction**. Её нельзя решить кластеризацией по
векторной близости — та группирует «похоже написанное», а контракт продукта
(`docs/NEWS_STORIES_TRENDS.md`) требует другого: «повторяющийся паттерн across several
distinct events». Разница видна на боевом примере: кластер `job agent actually work`
собрал 2 077 сюжетов просто потому, что они близки векторно, а тренд
`trend_29bf4f7b8d6ca091` смешал OpenAI и Китай.

Схема — это тройка ``(актор, действие, объект)``, снятая с сюжета. Тренд — одна и та же
схема, встреченная в нескольких сюжетах **с разными акторами**. Именно последнее условие
отличает тренд от продолжения одного сюжета, и именно его в прежнем слое не было:

    Amazon сокращает 14 000 → Salesforce сокращает 4 000 → Intel объявляет раунд
    схема (—, сокращает, штат), три разных актора  ⇒ тренд

    OpenAI выпустила модель → OpenAI ответила регулятору → OpenAI наняла главу
    один актор  ⇒ не тренд, а сюжетная линия одного актора

Имя тренда собирается из схемы, а не из частотных слов, поэтому `york time york time
athletic york` как класс ошибок исчезает.

Ориентиры: Open-Domain Hierarchical Event Schema Induction by Incremental Prompting and
Verification (ACL 2023, aclanthology.org/2023.acl-long.312), Zero-Shot On-the-Fly Event
Schema Induction (arxiv 2210.06254), Harvesting Event Schemas from LLMs (arxiv 2305.07280).
Здесь реализован детерминированный вариант без модели: он дешёв, воспроизводим и служит
базой, относительно которой будет измеряться zero-shot типизация акторов.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

# Нормализованные действия: разные формулировки одного действия обязаны давать один ключ,
# иначе «cuts jobs» и «lays off» разойдутся в разные тренды и повтор не обнаружится.
_ACTION_LEXICON: tuple[tuple[str, str, str], ...] = (
    # (ключ, регулярка, шаблон имени)
    (
        "layoffs",
        r"\b(lay(s|ing)? off|laid off|layoffs?|job cuts?|cuts? \d*\s*jobs?|"
        r"slash(es|ed)? (its )?(staff|workforce|headcount)|downsiz\w*)\b",
        "layoffs",
    ),
    (
        "hiring_freeze",
        r"\b(hiring freeze|freezes? hiring|pauses? hiring|halts? hiring)\b",
        "hiring freezes",
    ),
    (
        "lawsuit",
        r"\b(sues?|sued|lawsuit|files? suit|legal action|takes? .* to court)\b",
        "lawsuits",
    ),
    ("regulator_fine", r"\b(fines?|fined|penalt(y|ies)|sanctions?)\b", "regulatory fines"),
    (
        "investigation",
        r"\b(investigat\w+|probe[sd]?|inquiry|scrutin\w+|antitrust)\b",
        "investigations",
    ),
    (
        "ban",
        r"\b(bans?|banned|prohibit\w*|outlaw\w*|blocks? (the )?(use|sale|export))\b",
        "bans and restrictions",
    ),
    (
        "acquisition",
        r"\b(acquires?|acquisition|buys? out|takeover|merger|merges? with)\b",
        "acquisitions",
    ),
    (
        "funding",
        r"\b(raises? \$?[\d.]+|funding round|series [a-e]\b|valuation|ipo)\b",
        "funding rounds",
    ),
    (
        "launch",
        r"\b(launch\w*|releases?|unveil\w*|announces? (a |the )?(new )?"
        r"(model|product|tool|service)|ships?|rolls? out)\b",
        "product launches",
    ),
    (
        "outage",
        r"\b(outage|goes? down|downtime|service disruption|breach|hacked|leak\w*)\b",
        "outages and breaches",
    ),
    (
        "protest",
        r"\b(protest\w*|backlash|petition|boycott|walkout|sabotag\w*|"
        r"vandal\w*)\b|\b(workers?|staff|union|labou?r) strikes?\b|\bon strike\b",
        "protests and backlash",
    ),
    (
        "price_change",
        r"\b(raises? prices?|price (hike|increase|cut)|cheaper|more expensive|"
        r"cuts? prices?)\b",
        "price changes",
    ),
    (
        "datacenter",
        r"\b(data ?cent(er|re)|power grid|electricity demand|water us\w+)\b",
        "data centre buildout",
    ),
)
_COMPILED_ACTIONS = tuple(
    (key, re.compile(pattern, re.IGNORECASE), label) for key, pattern, label in _ACTION_LEXICON
)

# Актор — ведущая именованная сущность заголовка. Издания акторами не являются: они
# подпись источника, а не участник события (та же причина, что и в `_ctfidf_name`).
# Отклоняем кандидата, если в нём есть ЛЮБОЙ характерный издательский токен: требовать
# издательскими *все* токены нельзя — «The New York Times» содержит обычное «new» и
# проходил как актор. Цена — изредка пропущенный актор вроде «New York City»; он просто
# не засчитается в distinct_actors и тренда не испортит.
_PUBLISHER_TOKENS = frozenset(
    {
        "reuters",
        "bbc",
        "guardian",
        "nytimes",
        "times",
        "wapo",
        "york",
        "wired",
        "techcrunch",
        "verge",
        "technica",
        "usatoday",
        "cnn",
        "bloomberg",
        "athletic",
        "axios",
        "politico",
        "cnbc",
        "engadget",
        "gizmodo",
        "mashable",
    }
)
_ACTOR_RE = re.compile(r"\b([A-Z][a-zA-Z0-9&.\-]{2,}(?:\s+[A-Z][a-zA-Z0-9&.\-]{2,}){0,2})")
_LEADING_NOISE = re.compile(
    r"^(the|a|an|new|breaking|exclusive|opinion|analysis)\s+", re.IGNORECASE
)


# Глаголы лексикона многозначны, и вне своего домена они дают ложную схему. Проверка
# по первому замеру 58 трендов:
#   «penalties» — и санкции регулятора, и пенальти Хэмилтона в Формуле-1;
#   «ban»       — и запрет экспорта, и допинговая дисквалификация Мудрика;
#   «launch»    — и запуск продукта, и «Islamists have launched» атаку в Сеуте;
#   «strikes»   — и забастовка, и удары по Ирану.
# Это тот же класс ошибки, что был в Pulse со словом «beat»: поверхностный глагол без
# привязки к смыслу. Дешевле и надёжнее не чинить каждый глагол, а увести из слоя целые
# домены, где эти слова значат другое.
_SPORTS_MARKERS = re.compile(
    r"\b(nba|nfl|mlb|nhl|uefa|fifa|premier league|la liga|serie a|bundesliga|"
    r"f1|formula ?1|grand prix|tennis|atp|wta|golf|cricket|rugby|"
    r"playoffs?|world cup|super bowl|striker|midfielder|goalkeeper|quarterback|"
    r"touchdown|doping|coach|dugout|transfer window|tournament|championship|"
    r"season \d|matchday|fixtures?|football|soccer|basketball|baseball|hockey|"
    r"athletes?|youth academy|substitutes?|penalty kick)\b",
    re.IGNORECASE,
)
_MILITARY_MARKERS = re.compile(
    r"\b(missile|air ?strikes?|troops|militar\w+|offensive|warplane|drone strike|"
    r"shelling|ceasefire|militants?|insurgents?|attack(s|ed|ing)?|assault|invasion|"
    r"bombing|warhead|artillery)\b",
    re.IGNORECASE,
)


def is_out_of_scope(title: str) -> bool:
    """Домены, где глаголы лексикона значат не то: спорт и военные действия."""
    return bool(_SPORTS_MARKERS.search(title) or _MILITARY_MARKERS.search(title))


def extract_action(title: str) -> tuple[str, str] | None:
    """Нормализованное действие заголовка: ``(ключ, человекочитаемая метка)``."""
    if is_out_of_scope(title):
        return None
    for key, pattern, label in _COMPILED_ACTIONS:
        if pattern.search(title):
            return key, label
    return None


def extract_actor(title: str) -> str:
    """Ведущий актор события. Пустая строка, если опознать некого."""
    cleaned = _LEADING_NOISE.sub("", title.strip())
    for match in _ACTOR_RE.finditer(cleaned):
        candidate = match.group(1).strip()
        tokens = [token.lower().strip(".") for token in candidate.split()]
        if any(token in _PUBLISHER_TOKENS for token in tokens):
            continue
        if len(candidate) < 3:
            continue
        return candidate
    return ""


_DOMAIN_LABELS = {
    "ai_technology": "in AI",
    "business_markets": "in business",
    "labor_career": "in the labour market",
    "society_politics": "in politics",
    "surveillance_privacy": "in surveillance",
    "science_climate": "in science",
    "culture_media": "in media",
    "world": "worldwide",
}


def _story_domain(story: dict[str, Any]) -> str:
    raw = story.get("domain_ids")
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except ValueError:
            raw = []
    domains = [str(value) for value in (raw or []) if str(value) != "other"]
    return domains[0] if domains else ""


def story_schema(story: dict[str, Any]) -> tuple[str, str, str] | None:
    """Схема сюжета: ``(ключ_схемы, метка, актор)``. ``None`` — схемы нет.

    Ключ — действие **и** домен, а не одно действие: иначе «запуски продуктов» собирают
    112 сюжетов от 90 акторов и получается рубрика, а не паттерн. Контракт требует
    именно связки — «AI capex becomes a balance-sheet concern across different companies».
    """
    title = str(story.get("title") or "")
    action = extract_action(title)
    if action is None:
        return None
    action_key, label = action
    domain = _story_domain(story)
    domain_label = _DOMAIN_LABELS.get(domain, "")
    key = f"{action_key}|{domain}" if domain else action_key
    name = f"{label} {domain_label}".strip() if domain_label else label
    return key, name, extract_actor(title)


def discover_schema_trends(
    stories: list[dict[str, Any]],
    *,
    min_stories: int = 3,
    min_dates: int = 2,
    min_distinct_actors: int = 2,
) -> list[dict[str, Any]]:
    """Группирует сюжеты по схеме события.

    ``min_distinct_actors`` — то самое условие, которое отличает тренд от одной сюжетной
    линии. Без него «OpenAI сделала A, потом B, потом C» выглядит как повторяющийся
    паттерн, хотя это один актор.
    """
    by_action: dict[str, list[tuple[dict[str, Any], str, str]]] = defaultdict(list)
    for story in stories:
        schema = story_schema(story)
        if schema is None:
            continue
        action_key, label, actor = schema
        by_action[action_key].append((story, label, actor))

    trends: list[dict[str, Any]] = []
    for action_key, members in sorted(by_action.items()):
        if len(members) < min_stories:
            continue
        actors = {actor for _, _, actor in members if actor}
        if len(actors) < min_distinct_actors:
            continue
        dates = sorted(
            {
                str(story.get("first_seen") or "")[:10]
                for story, _, _ in members
                if story.get("first_seen")
            }
        )
        if len(dates) < min_dates:
            continue
        label = members[0][1]
        trends.append(
            {
                "schema_key": action_key,
                "name_ru": label,
                "pattern": label,
                "story_ids": [str(story["story_id"]) for story, _, _ in members],
                "distinct_actors": sorted(actors),
                "first_seen": dates[0],
                "last_seen": dates[-1],
                "story_count": len(members),
            }
        )
    return sorted(trends, key=lambda trend: (-int(trend["story_count"]), str(trend["schema_key"])))
