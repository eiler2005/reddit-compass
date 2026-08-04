"""Тренд как повторяющаяся схема события, а не как кластер похожих слов."""

from __future__ import annotations

from typing import Any

from reddit_compass.intelligence.actor_types import normalize_title_key
from reddit_compass.intelligence.quality import is_bad_trend_name
from reddit_compass.intelligence.trend_schema import (
    discover_schema_trends,
    extract_action,
    extract_actor,
    story_schema,
)


def _story(story_id: str, title: str, date: str, domain: str = "ai_technology") -> dict[str, Any]:
    return {
        "story_id": story_id,
        "title": title,
        "domain_ids": [domain],
        "first_seen": date,
        "last_seen": date,
    }


def test_action_lexicon_normalizes_wordings_to_one_key() -> None:
    """Разные формулировки одного действия обязаны давать один ключ."""
    wordings = [
        "Amazon lays off 14,000 managers",
        "Salesforce cuts 4000 jobs in support",
        "Intel announces fresh layoffs",
        "Meta slashes its workforce again",
    ]

    keys = {extract_action(title)[0] for title in wordings}  # type: ignore[index]

    assert keys == {"layoffs"}


def test_publisher_is_not_an_actor() -> None:
    """Издание — подпись источника, а не участник события."""
    assert extract_actor("The New York Times sues OpenAI over training data") == "OpenAI"
    assert extract_actor("Reuters: Amazon lays off staff") == "Amazon"


def test_schema_key_combines_action_and_domain() -> None:
    """Одно действие без домена собирает рубрику, а не паттерн."""
    ai_launch = story_schema(_story("s1", "OpenAI launches a new model", "2026-08-01"))
    biotech_launch = story_schema(
        _story("s2", "Moderna launches a new product", "2026-08-01", domain="science_climate")
    )

    assert ai_launch is not None and biotech_launch is not None
    assert ai_launch[0] != biotech_launch[0]
    assert "AI" in ai_launch[1]


def test_recurring_pattern_across_actors_becomes_a_trend() -> None:
    stories = [
        _story("s1", "Amazon lays off 14000 managers", "2026-07-28", "labor_career"),
        _story("s2", "Salesforce cuts 4000 jobs", "2026-07-29", "labor_career"),
        _story("s3", "Intel announces fresh layoffs", "2026-07-30", "labor_career"),
    ]

    trends = discover_schema_trends(stories)

    assert len(trends) == 1
    assert trends[0]["story_count"] == 3
    assert len(trends[0]["distinct_actors"]) >= 2
    assert "layoffs" in trends[0]["name_ru"]


def test_one_actor_is_a_storyline_not_a_trend() -> None:
    """Главное отличие от прежнего слоя: повтор у одного актора трендом не является."""
    stories = [
        _story("s1", "OpenAI launches a new model", "2026-07-28"),
        _story("s2", "OpenAI releases another model", "2026-07-29"),
        _story("s3", "OpenAI unveils a new tool", "2026-07-30"),
    ]

    assert discover_schema_trends(stories) == []


def test_single_day_burst_is_not_a_trend() -> None:
    stories = [
        _story("s1", "Amazon lays off staff", "2026-07-28", "labor_career"),
        _story("s2", "Intel cuts jobs", "2026-07-28", "labor_career"),
        _story("s3", "Meta slashes its workforce", "2026-07-28", "labor_career"),
    ]

    assert discover_schema_trends(stories) == []


def test_stories_without_a_recognised_action_are_skipped() -> None:
    stories = [
        _story("s1", "Thoughts on the weather today", "2026-07-28"),
        _story("s2", "A quiet afternoon in the park", "2026-07-29"),
        _story("s3", "Some musings about nothing", "2026-07-30"),
    ]

    assert discover_schema_trends(stories) == []


def test_sports_and_military_senses_are_vetoed() -> None:
    """Глаголы лексикона многозначны; вне своего домена они дают ложную схему.

    Все примеры — из первого замера 58 трендов, где они попали в тренды ошибочно.
    """
    out_of_scope = [
        "Why is Hamilton getting so many penalties? - F1 Q&A",
        "Como becomes the first club to ban headers in their youth academy",
        "Chelsea's Mudryk free to play as agreement cuts doping ban",
        "Israel raises defense alert over possible US strikes on Iran",
        "the Islamists have Launched an attack in Ceuta",
        "Pirlo out of Italy coach race after scrutiny over betting",
    ]

    assert [extract_action(title) for title in out_of_scope] == [None] * len(out_of_scope)


def test_sports_governance_is_vetoed_by_domain_not_by_wording() -> None:
    """Спортивное регулирование не содержит спортивной лексики вообще.

    Все четыре заголовка — из прогона 3 августа. Регулярка `_SPORTS_MARKERS` их не
    ловит («FA rule breaches», «points deduction», «agent rules»), и оштрафованный
    футбольный клуб доехал до тренда `regulatory fines in business by companies`.
    Причина, по которой домен нужно смотреть целиком: `_story_domain` берёт первый,
    а разметка была ``["business_markets", "sports"]``.
    """
    leaked = [
        ("Chelsea fined £10 million for FA rule breaches, avoid points deduction", ["sports"]),
        ("Chelsea fined £10m but avoid suspended points deduction", ["sports"]),
        ("Chelsea fined £10m for breaching agent rules", ["business_markets", "sports"]),
    ]

    for title, domains in leaked:
        assert extract_action(title) is not None, f"вето по заголовку и не должно ловить: {title}"
        story = {**_story("s", title, "2026-08-01"), "domain_ids": domains}
        assert story_schema(story) is None, title


def test_business_stories_survive_the_domain_veto() -> None:
    """Вето по домену не имеет права уносить обычные деловые сюжеты."""
    story = {
        **_story("s", "EU fines Google 890mn over ad tech", "2026-08-01"),
        "domain_ids": ["business_markets"],
    }

    assert story_schema(story) is not None


def test_in_scope_actions_survive_the_veto() -> None:
    in_scope = {
        "US bans Chinese open source AI models": "ban",
        "Pixar lays off over 100 Bay Area workers": "layoffs",
        "EU fines Google 890mn over ad tech": "regulator_fine",
        "Amazon workers strike over pay": "protest",
        "Anthropic raises $3.5B in a new funding round": "funding",
    }

    for title, expected in in_scope.items():
        action = extract_action(title)
        assert action is not None and action[0] == expected, title


def test_trend_names_use_the_source_language() -> None:
    """Имена — на английском, как в оригинальных материалах, без перевода."""
    stories = [
        _story("s1", "Amazon lays off 14000 managers", "2026-07-28", "labor_career"),
        _story("s2", "Salesforce cuts 4000 jobs", "2026-07-29", "labor_career"),
        _story("s3", "Intel announces fresh layoffs", "2026-07-30", "labor_career"),
    ]

    name = discover_schema_trends(stories)[0]["name_ru"]

    assert name == "layoffs in the labour market"
    assert not any("а" <= char <= "я" for char in name.lower())


def test_schema_v2_adapter_satisfies_the_release_contract() -> None:
    """Адаптер обязан отдавать все поля, которые пишет ``create_trend_release``."""
    from reddit_compass.intelligence.engine import _discover_trends_schema_v2

    stories = [
        {
            **_story("s1", "Amazon lays off 14000 managers", "2026-07-28", "labor_career"),
            "source_count": 2,
        },
        {
            **_story("s2", "Salesforce cuts 4000 jobs", "2026-07-29", "labor_career"),
            "source_count": 1,
        },
        {
            **_story("s3", "Intel announces fresh layoffs", "2026-07-30", "labor_career"),
            "source_count": 3,
        },
    ]

    adapted = _discover_trends_schema_v2(stories, params={})

    assert len(adapted) == 1
    trend, memberships = adapted[0]
    required = {
        "trend_id",
        "name_ru",
        "pattern",
        "domain_ids",
        "confidence",
        "lifecycle",
        "source_scope",
        "first_seen",
        "last_seen",
        "story_count",
        "source_count",
        "project_scores",
        "evidence_story_ids",
        "counterpoints",
        "review_status",
        "review_id",
    }
    assert required <= set(trend)
    assert trend["story_count"] == 3
    assert trend["source_count"] == len(trend["distinct_actors"])
    assert len(memberships) == 3
    assert all(len(entry) == 3 for entry in memberships)


def test_schema_v2_trend_id_is_stable_across_runs() -> None:
    """Одинаковый вход обязан давать тот же trend_id — релизы воспроизводимы."""
    from reddit_compass.intelligence.engine import _discover_trends_schema_v2

    stories = [
        _story("s1", "Amazon lays off 14000 managers", "2026-07-28", "labor_career"),
        _story("s2", "Salesforce cuts 4000 jobs", "2026-07-29", "labor_career"),
        _story("s3", "Intel announces fresh layoffs", "2026-07-30", "labor_career"),
    ]

    first = _discover_trends_schema_v2(stories, params={})[0][0]["trend_id"]
    second = _discover_trends_schema_v2(list(reversed(stories)), params={})[0][0]["trend_id"]

    assert first == second


def test_min_distinct_actors_is_configurable() -> None:
    """Порог различных акторов — параметр релиза, а не константа."""
    from reddit_compass.intelligence.engine import _discover_trends_schema_v2

    stories = [
        _story("s1", "OpenAI launches a new model", "2026-07-28"),
        _story("s2", "OpenAI releases another model", "2026-07-29"),
        _story("s3", "OpenAI unveils a new tool", "2026-07-30"),
    ]

    assert _discover_trends_schema_v2(stories, params={}) == []
    assert len(_discover_trends_schema_v2(stories, params={"trend_min_distinct_actors": 1})) == 1


def test_low_signal_stories_never_reach_the_trend_layer() -> None:
    """Регулярные треды отсеивались только при слиянии пар, но не на слое Trends.

    В опубликованном shadow-релизе из-за этого висели «тренды»
    «discussion advice thread july general» и «moronic monday question thread june july».
    """
    from reddit_compass.intelligence.clustering import is_low_signal_title
    from reddit_compass.intelligence.taxonomy import is_routine_beat

    published_as_trends = [
        "Daily General Discussion and Advice Thread - July 30, 2026",
        "Moronic Monday - June 15, 2026 - Your Weekly Questions Thread",
    ]

    for title in published_as_trends:
        assert is_low_signal_title(title) or is_routine_beat(title), title


def _typed(*pairs: tuple[str, str, str]) -> dict[str, tuple[str, str]]:
    """Таблица типов по заголовкам: ``(заголовок, актор, тип)``."""
    return {normalize_title_key(title): (actor, kind) for title, actor, kind in pairs}


_LAYOFF_STORIES = [
    _story("s1", "Amazon lays off 14000 managers", "2026-07-28", "labor_career"),
    _story("s2", "Salesforce cuts 4000 jobs", "2026-07-29", "labor_career"),
    _story("s3", "Intel announces fresh layoffs", "2026-07-30", "labor_career"),
]


def test_depth_two_output_is_unchanged_by_the_new_parameters() -> None:
    """Критерий аддитивности: глубина 3 без таблицы типов тождественна глубине 2."""
    baseline = discover_schema_trends(_LAYOFF_STORIES)

    assert discover_schema_trends(_LAYOFF_STORIES, depth=3, actor_types={}) == baseline
    assert discover_schema_trends(_LAYOFF_STORIES, depth=3, actor_types=None) == baseline


def test_depth_three_splits_a_group_into_children_under_one_parent() -> None:
    stories = [
        _story("s1", "EU fines Google 890mn over ad tech", "2026-07-28", "business_markets"),
        _story("s2", "Brussels fines Apple over app rules", "2026-07-29", "business_markets"),
        _story("s3", "Ireland fines Meta over data transfers", "2026-07-30", "business_markets"),
        _story("s4", "Sony penalty over refunds upheld", "2026-07-28", "business_markets"),
        _story("s5", "Valve faces a penalty over refunds", "2026-07-29", "business_markets"),
        _story("s6", "Nintendo penalty over pricing stands", "2026-07-30", "business_markets"),
    ]
    actor_types = _typed(
        ("EU fines Google 890mn over ad tech", "EU", "government agency"),
        ("Brussels fines Apple over app rules", "Brussels", "government agency"),
        ("Ireland fines Meta over data transfers", "Ireland", "government agency"),
        ("Sony penalty over refunds upheld", "Sony", "company"),
        ("Valve faces a penalty over refunds", "Valve", "company"),
        ("Nintendo penalty over pricing stands", "Nintendo", "company"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types=actor_types)

    roots = [trend for trend in trends if trend["depth"] == 2]
    children = [trend for trend in trends if trend["depth"] == 3]
    assert len(roots) == 1
    assert len(children) == 2
    assert {child["actor_type"] for child in children} == {"company", "government agency"}
    assert all(child["parent_schema_key"] == roots[0]["schema_key"] for child in children)


def test_children_strictly_refine_the_parent() -> None:
    """Глубина 3 только дробит: состав родителя — надмножество составов детей."""
    stories = [
        _story("s1", "EU fines Google 890mn over ad tech", "2026-07-28", "business_markets"),
        _story("s2", "Brussels fines Apple over app rules", "2026-07-29", "business_markets"),
        _story("s3", "Ireland fines Meta over data transfers", "2026-07-30", "business_markets"),
        _story("s4", "Sony penalty over refunds upheld", "2026-07-28", "business_markets"),
        _story("s5", "Valve faces a penalty over refunds", "2026-07-29", "business_markets"),
        _story("s6", "Nintendo penalty over pricing stands", "2026-07-30", "business_markets"),
        # Без типа — обязан остаться в родителе и не попасть ни в одного ребёнка.
        _story("s7", "Regulators weigh a fine over ad tech", "2026-07-31", "business_markets"),
    ]
    actor_types = _typed(
        ("EU fines Google 890mn over ad tech", "EU", "government agency"),
        ("Brussels fines Apple over app rules", "Brussels", "government agency"),
        ("Ireland fines Meta over data transfers", "Ireland", "government agency"),
        ("Sony penalty over refunds upheld", "Sony", "company"),
        ("Valve faces a penalty over refunds", "Valve", "company"),
        ("Nintendo penalty over pricing stands", "Nintendo", "company"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types=actor_types)
    root = next(trend for trend in trends if trend["depth"] == 2)
    children = [trend for trend in trends if trend["depth"] == 3]

    child_stories = {story_id for child in children for story_id in child["story_ids"]}
    assert child_stories <= set(root["story_ids"])
    assert sum(int(child["story_count"]) for child in children) <= int(root["story_count"])
    assert "s7" in root["story_ids"]
    assert "s7" not in child_stories


def test_a_single_surviving_child_collapses_into_the_parent() -> None:
    """Один ребёнок означает, что типизация ничего не разделила.

    Публиковать его — молча потерять нетипизированные сюжеты из слоя с покрытием ~5 %.
    """
    actor_types = _typed(
        ("Amazon lays off 14000 managers", "Amazon", "company"),
        ("Salesforce cuts 4000 jobs", "Salesforce", "company"),
        ("Intel announces fresh layoffs", "Intel", "company"),
    )

    trends = discover_schema_trends(_LAYOFF_STORIES, depth=3, actor_types=actor_types)

    assert [trend["depth"] for trend in trends] == [2]
    assert trends[0]["story_count"] == 3


def test_actor_type_must_be_appropriate_for_the_action() -> None:
    """`product launches` с типом `person` дал акторов «I», Mamdani, Rep. Russell Fry.

    Проверка контрфактическая: обе подгруппы берут все три порога, поэтому без таблицы
    уместности здесь родились бы два ребёнка, и один из них — «product launches in AI by
    individuals». Таблица оставляет ровно одного кандидата, и группа схлопывается.
    """
    launches = [
        _story("s1", "Mamdani launches a new tool for agents", "2026-07-28"),
        _story("s2", "Rep. Russell Fry unveils a new product", "2026-07-29"),
        _story("s3", "Sam Altman releases a new service", "2026-07-30"),
        _story("s4", "OpenAI launches a new model", "2026-07-28"),
        _story("s5", "Anthropic unveils a new tool", "2026-07-29"),
        _story("s6", "Mistral releases a new model", "2026-07-30"),
    ]
    people = _typed(
        ("Mamdani launches a new tool for agents", "Mamdani", "person"),
        ("Rep. Russell Fry unveils a new product", "Rep. Russell Fry", "person"),
        ("Sam Altman releases a new service", "Sam Altman", "person"),
    )
    companies = _typed(
        ("OpenAI launches a new model", "OpenAI", "company"),
        ("Anthropic unveils a new tool", "Anthropic", "company"),
        ("Mistral releases a new model", "Mistral", "company"),
    )

    # Контроль: у действия, которому люди уместны, такая же разметка даёт двух детей.
    control = discover_schema_trends(
        [
            _story("c1", "Mamdani sues Meta over training data", "2026-07-28"),
            _story("c2", "Rep. Russell Fry sues OpenAI over privacy", "2026-07-29"),
            _story("c3", "Sam Altman sues a former partner", "2026-07-30"),
            _story("c4", "Reddit sues Anthropic over scraping", "2026-07-28"),
            _story("c5", "Getty sues Stability over images", "2026-07-29"),
            _story("c6", "Disney sues Midjourney over characters", "2026-07-30"),
        ],
        depth=3,
        actor_types=_typed(
            ("Mamdani sues Meta over training data", "Mamdani", "person"),
            ("Rep. Russell Fry sues OpenAI over privacy", "Rep. Russell Fry", "person"),
            ("Sam Altman sues a former partner", "Sam Altman", "person"),
            ("Reddit sues Anthropic over scraping", "Reddit", "company"),
            ("Getty sues Stability over images", "Getty", "company"),
            ("Disney sues Midjourney over characters", "Disney", "company"),
        ),
    )
    assert {trend["actor_type"] for trend in control} == {"", "person", "company"}

    trends = discover_schema_trends(launches, depth=3, actor_types={**people, **companies})

    assert {trend["actor_type"] for trend in trends} == {""}
    root = trends[0]
    assert root["story_count"] == 6, "люди остаются в родителе, а не выбрасываются"


def test_a_category_is_not_an_actor() -> None:
    """GLiNER не отличает «Anthropic» от «AI firms»; для outage тип company допустим."""
    stories = [
        _story("s1", "AI firms report an outage", "2026-07-28"),
        _story("s2", "AI goes down for several hours", "2026-07-29"),
        _story("s3", "ChatGPT downtime hits users", "2026-07-30"),
    ]
    actor_types = _typed(
        ("AI firms report an outage", "AI firms", "company"),
        ("AI goes down for several hours", "AI", "company"),
        ("ChatGPT downtime hits users", "ChatGPT", "company"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types=actor_types)

    assert all(trend["depth"] == 2 for trend in trends)


def _launches(*titles_dates: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        _story(f"s{index}", title, date)
        for index, (title, date) in enumerate(titles_dates, start=1)
    ]


def test_object_splits_a_group_the_actor_type_cannot() -> None:
    """У `launch` допустим единственный тип актора, поэтому делит только объект.

    Ровно этот случай и есть исходная претензия: `product launches in AI` на 38 сюжетов
    читался рубрикой, и типизация акторов его не трогала.
    """
    stories = _launches(
        ("OpenAI releases GPT-6 weights", "2026-07-28"),
        ("DeepSeek launches an open source model", "2026-07-29"),
        ("Meta releases a new language model", "2026-07-30"),
        ("Tau Robotics unveils a humanoid robot", "2026-07-28"),
        ("Kroger stores launch a robot programme", "2026-07-29"),
        ("China unveils humanoid robots for factories", "2026-07-30"),
    )
    typed = _typed(
        ("OpenAI releases GPT-6 weights", "OpenAI", "company"),
        ("DeepSeek launches an open source model", "DeepSeek", "company"),
        ("Meta releases a new language model", "Meta", "company"),
        ("Tau Robotics unveils a humanoid robot", "Tau Robotics", "company"),
        ("Kroger stores launch a robot programme", "Kroger", "company"),
        ("China unveils humanoid robots for factories", "China", "country"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types=typed)

    children = [t for t in trends if t["depth"] == 3]
    assert {str(t["name_ru"]) for t in children} == {
        "product launches in AI: models",
        "product launches in AI: robots",
    }


def test_object_child_names_carry_the_parent_action() -> None:
    """Метка объекта не имеет права нести действие внутри себя.

    Первая версия называла ребёнка «model releases in AI». Под `outages and breaches`
    это давало бессмыслицу, а два разных родителя — одинаковое имя и падение поля
    `trends_duplicate_name_count`.
    """
    stories = _launches(
        ("OpenAI releases GPT-6 weights", "2026-07-28"),
        ("DeepSeek launches an open source model", "2026-07-29"),
        ("Meta releases a new language model", "2026-07-30"),
        ("Tau Robotics unveils a humanoid robot", "2026-07-28"),
        ("Kroger stores launch a robot programme", "2026-07-29"),
        ("China unveils humanoid robots for factories", "2026-07-30"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types={})
    names = [str(t["name_ru"]) for t in trends]
    root = next(str(t["name_ru"]) for t in trends if t["depth"] == 2)

    assert len(set(names)) == len(names)
    assert all(name == root or name.startswith(f"{root}: ") for name in names)


def test_object_split_needs_no_actor_type_table() -> None:
    """Разбиение по объекту обязано работать без таблицы типов."""
    stories = _launches(
        ("OpenAI releases GPT-6 weights", "2026-07-28"),
        ("DeepSeek launches an open source model", "2026-07-29"),
        ("Meta releases a new language model", "2026-07-30"),
        ("Tau Robotics unveils a humanoid robot", "2026-07-28"),
        ("Kroger stores launch a robot programme", "2026-07-29"),
        ("China unveils humanoid robots for factories", "2026-07-30"),
    )

    assert [t for t in discover_schema_trends(stories, depth=3, actor_types={}) if t["depth"] == 3]


def test_stories_without_a_recognised_object_stay_in_the_parent() -> None:
    """Инвариант строгого уточнения держится и на фасете объекта."""
    stories = _launches(
        ("OpenAI releases GPT-6 weights", "2026-07-28"),
        ("DeepSeek launches an open source model", "2026-07-29"),
        ("Meta releases a new language model", "2026-07-30"),
        ("Tau Robotics unveils a humanoid robot", "2026-07-28"),
        ("Kroger stores launch a robot programme", "2026-07-29"),
        ("China unveils humanoid robots for factories", "2026-07-30"),
        # Ни одного опознанного объекта: вопрос, а не запуск.
        ("How do you make your AI project stand out when everyone is launching?", "2026-07-31"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types={})
    root = next(t for t in trends if t["depth"] == 2)
    in_children = {sid for t in trends if t["depth"] == 3 for sid in t["story_ids"]}

    assert "s7" in root["story_ids"]
    assert "s7" not in in_children
    assert in_children <= set(root["story_ids"])


def test_actor_type_wins_when_it_actually_discriminates() -> None:
    """Объект — запасной фасет, а не замена: где тип актора делит, берётся он."""
    stories = [
        _story("s1", "EU fines Google 890mn over ad tech", "2026-07-28", "business_markets"),
        _story("s2", "Brussels fines Apple over app rules", "2026-07-29", "business_markets"),
        _story("s3", "Ireland fines Meta over data transfers", "2026-07-30", "business_markets"),
        _story("s4", "Sony penalty over refunds upheld", "2026-07-28", "business_markets"),
        _story("s5", "Valve faces a penalty over refunds", "2026-07-29", "business_markets"),
        _story("s6", "Nintendo penalty over pricing stands", "2026-07-30", "business_markets"),
    ]
    typed = _typed(
        ("EU fines Google 890mn over ad tech", "EU", "government agency"),
        ("Brussels fines Apple over app rules", "Brussels", "government agency"),
        ("Ireland fines Meta over data transfers", "Ireland", "government agency"),
        ("Sony penalty over refunds upheld", "Sony", "company"),
        ("Valve faces a penalty over refunds", "Valve", "company"),
        ("Nintendo penalty over pricing stands", "Nintendo", "company"),
    )

    children = [
        t for t in discover_schema_trends(stories, depth=3, actor_types=typed) if t["depth"] == 3
    ]

    assert {str(t["actor_type"]) for t in children} == {"company", "government agency"}


def test_publishers_are_rejected_on_the_typed_path_too() -> None:
    """Проверка была только у регулярочного пути, типизированный её обходил.

    В замере глубины 3 на 4 746 сюжетах тренд `regulatory fines in business by companies`
    из-за этого получил акторов «Financial Times» и «Fox Business» — подпись источника,
    а не участника события.
    """
    stories = [
        _story("s1", "Financial Times reports a fine on Google", "2026-07-28", "business_markets"),
        _story("s2", "Fox Business covers a penalty on Apple", "2026-07-29", "business_markets"),
        _story("s3", "Bloomberg reports a fine on Meta", "2026-07-30", "business_markets"),
        _story("s4", "China fines a chipmaker over exports", "2026-07-28", "business_markets"),
        _story("s5", "Russia fines a platform over content", "2026-07-29", "business_markets"),
        _story("s6", "US sanctions a shipping group", "2026-07-30", "business_markets"),
    ]
    actor_types = _typed(
        ("Financial Times reports a fine on Google", "Financial Times", "company"),
        ("Fox Business covers a penalty on Apple", "Fox Business", "company"),
        ("Bloomberg reports a fine on Meta", "Bloomberg", "company"),
        ("China fines a chipmaker over exports", "China", "country"),
        ("Russia fines a platform over content", "Russia", "country"),
        ("US sanctions a shipping group", "US", "country"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types=actor_types)

    typed_actors = {
        actor for trend in trends if trend["depth"] == 3 for actor in trend["distinct_actors"]
    }
    assert not typed_actors & {"Financial Times", "Fox Business", "Bloomberg"}


def test_min_distinct_actors_holds_inside_every_child() -> None:
    """Условие, из-за которого третьим компонентом не может быть сам актор."""
    stories = [
        _story("s1", "EU fines Google 890mn over ad tech", "2026-07-28", "business_markets"),
        _story("s2", "Brussels fines Apple over app rules", "2026-07-29", "business_markets"),
        _story("s3", "Ireland fines Meta over data transfers", "2026-07-30", "business_markets"),
        _story("s4", "Sony penalty over refunds upheld", "2026-07-28", "business_markets"),
        _story("s5", "Sony penalty over pricing stands", "2026-07-29", "business_markets"),
        _story("s6", "Sony penalty over ads upheld", "2026-07-30", "business_markets"),
    ]
    # Три сюжета одного актора — сюжетная линия, а не паттерн: ребёнка быть не должно.
    actor_types = _typed(
        ("EU fines Google 890mn over ad tech", "EU", "government agency"),
        ("Brussels fines Apple over app rules", "Brussels", "government agency"),
        ("Ireland fines Meta over data transfers", "Ireland", "government agency"),
        ("Sony penalty over refunds upheld", "Sony", "company"),
        ("Sony penalty over pricing stands", "Sony", "company"),
        ("Sony penalty over ads upheld", "Sony", "company"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types=actor_types)

    assert "company" not in {trend["actor_type"] for trend in trends}
    for trend in trends:
        assert len(trend["distinct_actors"]) >= 2


def test_child_names_extend_the_parent_and_stay_distinct() -> None:
    """Поле `trends_duplicate_name_count` — max 0, дубль имени валит релиз."""
    stories = [
        _story("s1", "EU fines Google 890mn over ad tech", "2026-07-28", "business_markets"),
        _story("s2", "Brussels fines Apple over app rules", "2026-07-29", "business_markets"),
        _story("s3", "Ireland fines Meta over data transfers", "2026-07-30", "business_markets"),
        _story("s4", "Sony penalty over refunds upheld", "2026-07-28", "business_markets"),
        _story("s5", "Valve faces a penalty over refunds", "2026-07-29", "business_markets"),
        _story("s6", "Nintendo penalty over pricing stands", "2026-07-30", "business_markets"),
    ]
    actor_types = _typed(
        ("EU fines Google 890mn over ad tech", "EU", "government agency"),
        ("Brussels fines Apple over app rules", "Brussels", "government agency"),
        ("Ireland fines Meta over data transfers", "Ireland", "government agency"),
        ("Sony penalty over refunds upheld", "Sony", "company"),
        ("Valve faces a penalty over refunds", "Valve", "company"),
        ("Nintendo penalty over pricing stands", "Nintendo", "company"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types=actor_types)
    names = [str(trend["name_ru"]) for trend in trends]
    root_name = next(str(t["name_ru"]) for t in trends if t["depth"] == 2)

    assert len(set(names)) == len(names)
    assert not any(is_bad_trend_name(name) for name in names)
    assert sorted(name for name in names if name != root_name) == [
        f"{root_name} by companies",
        f"{root_name} by government agencies",
    ]


def test_child_keys_always_use_three_slots() -> None:
    """Иначе сюжет без домена дал бы `layoffs|company` и столкнулся с доменом «company»."""
    stories = [
        {**_story("s1", "EU fines Google 890mn over ad tech", "2026-07-28"), "domain_ids": []},
        {**_story("s2", "Brussels fines Apple over app rules", "2026-07-29"), "domain_ids": []},
        {**_story("s3", "Ireland fines Meta over data transfers", "2026-07-30"), "domain_ids": []},
        {**_story("s4", "Sony penalty over refunds upheld", "2026-07-28"), "domain_ids": []},
        {**_story("s5", "Valve faces a penalty over refunds", "2026-07-29"), "domain_ids": []},
        {**_story("s6", "Nintendo penalty over pricing stands", "2026-07-30"), "domain_ids": []},
    ]
    actor_types = _typed(
        ("EU fines Google 890mn over ad tech", "EU", "government agency"),
        ("Brussels fines Apple over app rules", "Brussels", "government agency"),
        ("Ireland fines Meta over data transfers", "Ireland", "government agency"),
        ("Sony penalty over refunds upheld", "Sony", "company"),
        ("Valve faces a penalty over refunds", "Valve", "company"),
        ("Nintendo penalty over pricing stands", "Nintendo", "company"),
    )

    trends = discover_schema_trends(stories, depth=3, actor_types=actor_types)

    children = [trend for trend in trends if trend["depth"] == 3]
    assert children
    assert all(str(child["schema_key"]).count("|") == 2 for child in children)
    assert "regulator_fine||company" in {str(child["schema_key"]) for child in children}


def test_unmapped_domains_still_get_distinct_names() -> None:
    """Разные схемы обязаны давать разные имена, иначе релиз падает на поле дублей.

    `_DOMAIN_LABELS` покрывает только частые домены. Для остальных метка была пустой,
    поэтому layoffs|finance_consumer и layoffs|climate_energy назывались одинаково —
    боевой прогон schema_v2 упал на `trends_duplicate_name_count`.
    """
    domains = ("labor_career", "finance_consumer", "climate_energy", "ai_technology")
    names = [
        story_schema(_story("s", "Amazon lays off 14000 managers", "2026-08-01", d))[1]  # type: ignore[index]
        for d in domains
    ]

    assert len(set(names)) == len(domains), names
