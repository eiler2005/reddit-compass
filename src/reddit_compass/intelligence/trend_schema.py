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

# Метки типов актора для имён трендов глубины 3. Словарь замкнут: типы приходят только из
# `actor_types.ACTOR_LABELS`, поэтому суффикс имени берётся из четырёх значений и не может
# случайно совпасть с доменной меткой выше.
_ACTOR_TYPE_LABELS: dict[str, str] = {
    "company": "companies",
    "government agency": "government agencies",
    "person": "individuals",
    "country": "countries",
}

# Тип актора обязан быть уместен действию. Замер на боевых данных: `product launches in
# business` с типом `person` дал акторов «I», Mamdani, Rep. Russell Fry — человек не
# запускает продукт, и это неверное содержание, а не грязный список. Таблица режет такую
# пару до того, как из неё соберётся дочерний тренд.
#
# `country` — это то, чем GLiNER размечает правительство страны: «US bans Chinese open
# source AI models» даёт именно `country`. Поэтому `ban` и `regulator_fine` обязаны его
# допускать, иначе теряют самого частого своего актора.
_ACTION_ACTOR_TYPES: dict[str, frozenset[str]] = {
    "layoffs": frozenset({"company"}),
    "hiring_freeze": frozenset({"company"}),
    # Истец-физлицо — настоящий участник события («author sues Meta»).
    "lawsuit": frozenset({"company", "government agency", "person"}),
    "regulator_fine": frozenset({"government agency", "country", "company"}),
    "investigation": frozenset({"government agency", "company"}),
    "ban": frozenset({"government agency", "country", "company"}),
    "acquisition": frozenset({"company"}),
    "funding": frozenset({"company"}),
    "launch": frozenset({"company"}),
    "outage": frozenset({"company"}),
    # Адресат бойкота — тоже участник, поэтому не только люди.
    "protest": frozenset({"person", "company"}),
    "price_change": frozenset({"company"}),
    "datacenter": frozenset({"company", "country"}),
}

# GLiNER размечает категорию как организацию и не отличает «Anthropic» от «AI firms».
# Таблица типов этого не чинит: для `outage` тип `company` допустим, поэтому «AI firms»
# прошло бы в акторы. Стоп-лист засеян только тем, что встретилось в замере 53 трендов —
# выдумывать записи сюда нельзя, иначе он начнёт резать настоящих акторов.
_NON_ACTOR_STRINGS: frozenset[str] = frozenset(
    {
        "i",
        "ai",
        "ai firms",
        "chatgpt",
        "grocery stores",
        "tech",
        "startups",
        # Из замера глубины 3 на 4 746 сюжетах: «Artist» попал в `lawsuits in AI by
        # individuals`, «Open Source» — в `regulatory fines in AI by companies`.
        "artist",
        "artists",
        "open source",
    }
)


def allowed_actor_types(action_key: str) -> frozenset[str]:
    """Типы акторов, уместные для действия. Пустое множество — действие незнакомо."""
    return _ACTION_ACTOR_TYPES.get(action_key, frozenset())


def actor_type_label(actor_type: str) -> str:
    """Человекочитаемая метка типа для имени тренда. Пустая строка — тип незнаком."""
    return _ACTOR_TYPE_LABELS.get(actor_type, "")


def is_non_actor(actor: str) -> bool:
    """Категория вместо участника: «AI firms», «grocery stores», «I»."""
    return actor.strip().lower() in _NON_ACTOR_STRINGS


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
        # Замер глубины 3: заголовки приходят с суффиксом источника «— Fox Business»,
        # GLiNER читает его как сущность, и издание попадало в акторы тренда
        # `regulatory fines in business by companies`.
        "fox",
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


def is_publisher(candidate: str) -> bool:
    """Издание, а не участник события.

    Проверка нужна обоим путям извлечения актора. У регулярочного она была с самого
    начала, а типизированный её обходил — и в замере глубины 3 тренд `regulatory fines
    in business by companies` получил акторов «Financial Times» и «Fox Business».
    """
    tokens = [token.lower().strip(".") for token in candidate.split()]
    return any(token in _PUBLISHER_TOKENS for token in tokens)


def extract_actor(title: str) -> str:
    """Ведущий актор события. Пустая строка, если опознать некого."""
    cleaned = _LEADING_NOISE.sub("", title.strip())
    for match in _ACTOR_RE.finditer(cleaned):
        candidate = match.group(1).strip()
        if is_publisher(candidate):
            continue
        if len(candidate) < 3:
            continue
        return candidate
    return ""


# Ключи обязаны быть идентификаторами доменов из `taxonomy.DomainId`, а не рубрик. Три из
# восьми прежних ключей ими не были: `world` и `science_climate` — идентификаторы рубрик
# (`taxonomy._RUBRICS`), а `surveillance_privacy` — опечатка вместо `security_privacy`.
# Поэтому пять доменов из одиннадцати уходили в фолбэк ниже и назывались
# «layoffs in climate energy infrastructure». Домен `other` сюда не нужен: `_story_domain`
# его отбрасывает.
_DOMAIN_LABELS = {
    "ai_technology": "in AI",
    "business_markets": "in business",
    "labor_career": "in the labour market",
    "society_politics": "in politics",
    "world_geopolitics": "worldwide",
    "culture_media": "in media",
    "sports": "in sport",
    "science_health_education": "in science and health",
    "finance_consumer": "in finance",
    "climate_energy_infrastructure": "in energy and climate",
    "security_privacy": "in security and privacy",
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
    # Незнакомый домен обязан давать своё имя, а не пустое: иначе ключи
    # layoffs|finance_consumer и layoffs|climate_energy дают одинаковое имя «layoffs»
    # и релиз падает на поле trends_duplicate_name_count. Словарь покрывает только
    # частые домены, поэтому для остальных берём сам идентификатор.
    domain_label = _DOMAIN_LABELS.get(domain, f"in {domain.replace('_', ' ')}" if domain else "")
    key = f"{action_key}|{domain}" if domain else action_key
    name = f"{label} {domain_label}".strip() if domain_label else label
    return key, name, extract_actor(title)


_Member = tuple[dict[str, Any], str, str]


def _make_trend(
    schema_key: str,
    name: str,
    members: list[_Member],
    *,
    actors: set[str],
    parent_schema_key: str,
    actor_type: str,
    depth: int,
    min_stories: int,
    min_dates: int,
    min_distinct_actors: int,
) -> dict[str, Any] | None:
    """Группа как тренд, либо ``None``, если не взяла хотя бы один порог."""
    if len(members) < min_stories:
        return None
    if len(actors) < min_distinct_actors:
        return None
    dates = sorted(
        {
            str(story.get("first_seen") or "")[:10]
            for story, _, _ in members
            if story.get("first_seen")
        }
    )
    if len(dates) < min_dates:
        return None
    return {
        "schema_key": schema_key,
        "parent_schema_key": parent_schema_key,
        "actor_type": actor_type,
        "depth": depth,
        "name_ru": name,
        "pattern": name,
        "story_ids": [str(story["story_id"]) for story, _, _ in members],
        "distinct_actors": sorted(actors),
        "first_seen": dates[0],
        "last_seen": dates[-1],
        "story_count": len(members),
    }


def _split_by_actor_type(
    schema_key: str,
    root_name: str,
    members: list[_Member],
    actor_types: dict[str, tuple[str, str]],
    *,
    min_stories: int,
    min_dates: int,
    min_distinct_actors: int,
) -> list[dict[str, Any]]:
    """Дети группы: тот же паттерн, разложенный по типу актора.

    Сюжет без типа и сюжет с типом, неуместным действию, остаются в родителе и ни в
    какого ребёнка не попадают. Недопустимый тип — свидетельство, что ошиблась
    типизация, а не сюжет: ``extract_action`` уже прошёл вето спорта и военных, этому
    сигналу доверия больше, чем метке NER второй ступени. Выбрасывать такие сюжета
    нельзя ещё и потому, что покрытие слоя ~5 %, а инвариант строгого уточнения
    (состав родителя ⊇ объединение составов детей) держится только так.
    """
    from .actor_types import normalize_title_key

    action_key, _, domain = schema_key.partition("|")
    allowed = allowed_actor_types(action_key)
    if not allowed:
        return []
    by_type: dict[str, list[_Member]] = defaultdict(list)
    for story, label, _ in members:
        typed = actor_types.get(normalize_title_key(str(story.get("title") or "")))
        if typed is None:
            continue
        actor_text, actor_type = typed
        if actor_type not in allowed or is_non_actor(actor_text) or is_publisher(actor_text):
            continue
        by_type[actor_type].append((story, label, actor_text))

    children: list[dict[str, Any]] = []
    for actor_type, sub_members in sorted(by_type.items()):
        type_label = actor_type_label(actor_type)
        if not type_label:
            continue
        child = _make_trend(
            # Ключ всегда в три слота, даже когда домена нет: иначе сюжет без домена дал
            # бы `layoffs|company` и столкнулся с доменом по имени «company».
            f"{action_key}|{domain}|{actor_type}",
            f"{root_name} by {type_label}",
            sub_members,
            actors={actor for _, _, actor in sub_members if actor},
            parent_schema_key=schema_key,
            actor_type=actor_type,
            depth=3,
            min_stories=min_stories,
            min_dates=min_dates,
            min_distinct_actors=min_distinct_actors,
        )
        if child is not None:
            children.append(child)
    return children


def discover_schema_trends(
    stories: list[dict[str, Any]],
    *,
    min_stories: int = 3,
    min_dates: int = 2,
    min_distinct_actors: int = 2,
    depth: int = 2,
    actor_types: dict[str, tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Группирует сюжеты по схеме события.

    ``min_distinct_actors`` — то самое условие, которое отличает тренд от одной сюжетной
    линии. Без него «OpenAI сделала A, потом B, потом C» выглядит как повторяющийся
    паттерн, хотя это один актор.

    ``depth`` — сколько компонентов схемы входит в ключ, то есть ручка гранулярности:

        2  (действие, домен)             `product launches in AI`      — уровень *theme*
        3  (действие, домен, тип актора) `… by companies`              — уровень *key event*

    Третьим компонентом идёт **тип** актора, а не сам актор: по самому актору каждая
    группа окажется одноакторной по построению, ``min_distinct_actors >= 2`` не
    выполнится никогда и трендов не останется вообще.

    Глубина 3 только дробит и никогда не удаляет: строки родителей байт-в-байт равны
    выводу глубины 2, а состав родителя — надмножество объединения составов детей.
    Поэтому ``depth=3`` без таблицы типов тождественно ``depth=2``, а откат на глубину 2
    — честное надмножество, а не другой релиз.
    """
    by_action: dict[str, list[_Member]] = defaultdict(list)
    for story in stories:
        schema = story_schema(story)
        if schema is None:
            continue
        action_key, label, actor = schema
        by_action[action_key].append((story, label, actor))

    trends: list[dict[str, Any]] = []
    for schema_key, members in sorted(by_action.items()):
        root_name = members[0][1]
        root = _make_trend(
            schema_key,
            root_name,
            members,
            # Акторы родителя — прежние регулярочные: если считать их по GLiNER, родитель
            # может потерять `min_distinct_actors >= 2` там, где модель корректно схлопнет
            # «Got»/«Just»/«Laid» в одного реального актора, и глубина 3 начнёт удалять
            # тренды, существующие на глубине 2.
            actors={actor for _, _, actor in members if actor},
            parent_schema_key="",
            actor_type="",
            depth=2,
            min_stories=min_stories,
            min_dates=min_dates,
            min_distinct_actors=min_distinct_actors,
        )
        if root is None:
            continue
        trends.append(root)
        if depth < 3 or not actor_types:
            continue
        children = _split_by_actor_type(
            schema_key,
            root_name,
            members,
            actor_types,
            min_stories=min_stories,
            min_dates=min_dates,
            min_distinct_actors=min_distinct_actors,
        )
        # Один выживший ребёнок означает, что типизация ничего не разделила, а его состав
        # — строгое подмножество родительского. Публиковать его значит молча потерять
        # нетипизированные сюжеты, поэтому схлопываем к родителю.
        if len(children) >= 2:
            trends.extend(children)
    return sorted(
        trends,
        key=lambda trend: (
            int(trend["depth"]),
            -int(trend["story_count"]),
            str(trend["schema_key"]),
        ),
    )
