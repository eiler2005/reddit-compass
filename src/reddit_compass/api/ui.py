"""UI routes: /today, /stories/{id}, /explore, /runs.

Jinja2 templates с autoescape. Security headers.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from collections.abc import Generator
from dataclasses import asdict
from functools import lru_cache
from math import log1p
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import DEFAULT_PROFILE
from ..intelligence.engine import (
    DEFAULT_ENGINE_DB_PATH,
    engine_db,
    get_current_publication,
    get_data_release,
    get_publication,
    label_engine_target,
    list_data_releases,
    list_publications,
    open_engine_readonly,
)
from ..intelligence.quality import is_bad_trend_name
from ..intelligence.repository import (
    update_research_state,
)
from ..intelligence.taxonomy import BROAD_DOMAINS
from ..versioning import assets_version
from .view_models import cluster_label

router = APIRouter()

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.autoescape = True
# Cache-buster статики считается из её содержимого. Ручная версия в шаблоне требовала
# помнить про бамп при каждой правке скрипта, а забытый бамп означает старый JS против
# новой разметки — ровно тот класс расхождений, который не виден в тестах.
templates.env.globals["asset_version"] = assets_version()
# Полоса источников подписывает свои сегменты человеческими названиями кластеров,
# а не их идентификаторами: «🗣 Голоса» вместо voices.
templates.env.globals["cluster_label"] = cluster_label

_CSRF_SECRET = secrets.token_hex(32)


def _get_db() -> Generator[sqlite3.Connection, None, None]:
    db_path = Path(os.environ.get("RC_DB_PATH", "data/compass.db"))
    if os.access(db_path, os.W_OK):
        conn = sqlite3.connect(db_path, check_same_thread=False)
    else:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _engine_path() -> Path:
    return Path(os.environ.get("RC_ENGINE_DB_PATH", str(DEFAULT_ENGINE_DB_PATH)))


def _generate_csrf_token() -> str:
    return secrets.token_hex(32)


def _validate_csrf_token(token: str) -> bool:
    return len(token) == 64


def _safe_url(url: str) -> str:
    """Проверяет URL на безопасность."""
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return ""


def _analysis_query(channel: str = "broad", publication_id: str | None = None) -> str:
    params = {"channel": channel}
    if publication_id:
        params["publication_id"] = publication_id
    return urlencode(params)


def _page_url(path: str, **filters: str | None) -> str:
    """Адрес страницы со всеми активными фильтрами, но без ``page``.

    Партиал пагинации дописывает номер сам. Собирать этот адрес в шаблоне
    значило бы вручную перечислять фильтры в каждой ссылке — ровно так на
    /pulse и появились строки на 200 символов, где легко потерять параметр.
    """
    query = urlencode({key: value for key, value in filters.items() if value})
    return f"{path}?{query}" if query else path


_LIFECYCLE_LABELS = {
    "new": "новое",
    "growing": "растёт",
    "stable": "стабильно",
    "fading": "остывает",
    "resurfacing": "вернулось",
    "insufficient_history": "мало истории",
}

_LIFECYCLE_HINTS = {
    "new": "Впервые заметно в текущем окне.",
    "growing": "Скорость выше предыдущего baseline.",
    "stable": "Держится без резкого ускорения.",
    "fading": "Новых подтверждений стало меньше.",
    "resurfacing": "Сюжет вернулся после паузы.",
    "insufficient_history": (
        "Истории пока меньше нужного окна; динамика не классифицируется как рост/падение."
    ),
}

_SCOPE_LABELS = {
    "cross_source": "Reddit + СМИ",
    "community_only": "только сообщества",
    "mainstream_only": "только СМИ",
}

_REVIEW_LABELS = {
    "confirmed": "проверено",
    "pending": "машинный кандидат",
    "rejected": "отклонено",
    "legacy": "legacy",
}

_PROVIDER_LABELS = {
    "reddit": "Reddit",
    "hn": "Hacker News",
    "hackernews": "Hacker News",
    "producthunt": "Product Hunt",
    "reuters": "Reuters",
    "bbc": "BBC",
    "guardian": "Guardian",
    "nytimes": "NYT",
    "nyt": "NYT",
    "financial_times": "FT",
    "ft": "FT",
    "techcrunch": "TechCrunch",
    "the_verge": "The Verge",
    "verge": "The Verge",
    "ars_technica": "Ars Technica",
    "wired": "Wired",
}

_READING_DOMAIN_WEIGHTS = {
    "ai_technology": 26.0,
    "labor_career": 23.0,
    "business_markets": 23.0,
    "security_privacy": 20.0,
    "society_politics": 17.0,
    "world_geopolitics": 16.0,
    "finance_consumer": 15.0,
    "culture_media": 12.0,
    "science_health_education": 10.0,
    "climate_energy_infrastructure": 9.0,
    "sports": 5.0,
    "other": -5.0,
}

_READING_PROVIDER_WEIGHTS = {
    "reuters": 14.0,
    "financial_times": 14.0,
    "ft": 14.0,
    "nytimes": 13.0,
    "nyt": 13.0,
    "bbc": 12.0,
    "guardian": 12.0,
    "wired": 11.0,
    "techcrunch": 10.0,
    "the_verge": 10.0,
    "verge": 10.0,
    "ars_technica": 10.0,
    "hn": 9.0,
    "hackernews": 9.0,
    # Reddit is a useful early/community signal, but raw engagement must not
    # crowd out reported or primary-source material in the daily reading list.
    "reddit": 7.0,
    "producthunt": 7.0,
}

# Человекочитаемые названия типов сигналов Pulse. Сырые `policy_politics` и
# `career_labor` в интерфейсе не читаются, а тип — единственная рабочая тематическая ось
# сигналов: `domain_ids_json` у них практически всегда `other`.
# Покрывает весь ``reddit_pulse.SignalType`` — полнота проверяется тестом.
# Незакрытый тип попадает в интерфейс сырым: на проде так вылезла крупнейшая
# тематика «news link» (494 сигнала) вместо «Ссылки на новости».
_SIGNAL_TYPE_LABELS = {
    "ai_capability": "Возможности AI",
    "ai_risk": "Риски AI",
    "ai_tools": "AI-инструменты",
    "career_labor": "Работа и карьера",
    "complaint": "Жалобы",
    "discussion": "Дискуссии",
    "market_investing": "Рынки и инвестиции",
    "meme_culture": "Мемы и культура",
    "news_link": "Ссылки на новости",
    "pain_point": "Боли",
    "policy_politics": "Политика и регулирование",
    "product_request": "Запросы на продукт",
    "question": "Вопросы",
    "other": "Прочее",
}


def signal_type_label(signal_type: str) -> str:
    return _SIGNAL_TYPE_LABELS.get(signal_type, signal_type.replace("_", " "))


# Строка сигнала подписывает тип по-человечески: «Боли» вместо pain_point.
# Раньше сырой идентификатор просачивался прямо в разметку /pulse.
templates.env.globals["signal_type_label"] = signal_type_label


# Сколько постов одной тематики и одного сабреддита пускаем в блок «Новое на Reddit».
# Политика ограничена жёстче остальных: без этого лента съезжает в неё целиком —
# `policy_politics` держит самый высокий средний pulse среди всех типов.
_NEW_REDDIT_PER_TYPE = 3
_NEW_REDDIT_PER_SUBREDDIT = 2
_NEW_REDDIT_POLITICS_CAP = 3

_READING_CLUSTER_WEIGHTS = {
    "business": 8.0,
    "mainstream": 8.0,
    "technology": 7.0,
    "voices": 7.0,
    "product": 6.0,
    "culture": 5.0,
}


def _decorate_today_trend(trend: dict[str, object], analysis_query: str) -> dict[str, object]:
    enriched = dict(trend)
    lifecycle = str(enriched.get("lifecycle") or "insufficient_history")
    source_scope = str(enriched.get("source_scope") or "")
    review_status = str(enriched.get("review_status") or "pending")
    trend_id = str(enriched.get("trend_id") or "")
    confidence_raw = enriched.get("confidence")
    confidence = float(confidence_raw) if isinstance(confidence_raw, int | float | str) else 0.0
    enriched["url"] = (
        f"/trends/{trend_id}?{analysis_query}" if trend_id else f"/trends?{analysis_query}"
    )
    enriched["lifecycle_label"] = _LIFECYCLE_LABELS.get(lifecycle, lifecycle)
    enriched["lifecycle_hint"] = _LIFECYCLE_HINTS.get(lifecycle, "")
    enriched["source_scope_label"] = _SCOPE_LABELS.get(source_scope, source_scope)
    enriched["review_label"] = _REVIEW_LABELS.get(review_status, review_status)
    enriched["confidence_pct"] = round(confidence * 100)
    return enriched


def _json_dict_value(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list_value(raw: object, fallback: list[str] | None = None) -> list[str]:
    if isinstance(raw, list):
        return [str(value) for value in raw]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(value) for value in parsed]
    return list(fallback or [])


def _provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, provider.replace("_", " ").title())


def _domain_label(domain_id: str) -> str:
    return BROAD_DOMAINS[domain_id].label_ru if domain_id in BROAD_DOMAINS else domain_id


def _float_metric(raw: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return 0.0


def _normalized_reading_key(title: str, primary_url: str, secondary_url: str) -> str:
    url = primary_url or secondary_url
    if url:
        parsed = urlsplit(url)
        return f"{parsed.netloc.lower()}{parsed.path.rstrip('/').lower()}"
    return " ".join(title.lower().split())[:120]


def _numeric_dict_value(raw: dict[str, Any], key: str) -> float:
    value = raw.get(key, 0.0)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _as_int(value: object) -> int:
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return 0
    return 0


def _is_low_signal_reading_title(title: str) -> bool:
    """Keep recurring community housekeeping out of the human reading queue."""
    title_lower = title.lower()
    routine_markers = (
        "game thread",
        "daily discussion",
        "megathread",
        "daily questions",
        "weekly discussion",
        "what are you playing",
    )
    words = [word for word in title_lower.split() if any(char.isalpha() for char in word)]
    return len(words) < 3 or any(marker in title_lower for marker in routine_markers)


def _item_reading_score(
    *,
    row: sqlite3.Row,
    domain_ids: list[str],
    project_scores: dict[str, Any],
    raw_engagement: dict[str, Any],
    radar_date: str,
) -> float:
    provider = str(row["provider"] or "").lower()
    source_cluster = str(row["source_cluster"] or "").lower()
    content_scope = str(row["content_scope"] or "headline")
    title = str(row["title"] or "")
    score = max(
        (_READING_DOMAIN_WEIGHTS.get(domain_id, 0.0) for domain_id in domain_ids),
        default=0.0,
    )
    score += _READING_PROVIDER_WEIGHTS.get(provider, 4.0)
    score += _READING_CLUSTER_WEIGHTS.get(source_cluster, 2.0)
    score += min(
        28.0,
        0.25 * _numeric_dict_value(project_scores, "rbc")
        + 0.18 * _numeric_dict_value(project_scores, "book")
        + 0.12 * _numeric_dict_value(project_scores, "business"),
    )
    if provider in {"reddit", "hn", "hackernews", "producthunt"}:
        # Engagement is measured differently across platforms.  It is a small
        # within-channel tiebreaker here, never a global popularity contest.
        score += min(
            8.0,
            log1p(_float_metric(raw_engagement, "score", "points", "votes")) * 1.0,
        )
        score += min(
            5.0,
            log1p(_float_metric(raw_engagement, "comments", "comment_count", "num_comments")) * 0.9,
        )
    if content_scope == "full":
        score += 5.0
    elif content_scope == "excerpt":
        score += 3.0
    elif content_scope == "abstract":
        score += 2.0
    if str(row["snapshot_date"] or "") == radar_date:
        score += 8.0
    if int(row["story_source_count"] or 0) > 1:
        score += 5.0
    if _is_low_signal_reading_title(title):
        score -= 35.0
    return score


def _build_today_reading_list(
    conn: sqlite3.Connection, radar: dict[str, object], *, limit: int = 20
) -> list[dict[str, object]]:
    data_release_id = str(radar.get("data_release_id") or "")
    story_release_id = str(radar.get("story_release_id") or "")
    radar_date = str(radar.get("date") or "")
    if not data_release_id or not story_release_id:
        return []
    facet_row = conn.execute(
        "SELECT facet_release_id FROM story_releases WHERE story_release_id = ?",
        (story_release_id,),
    ).fetchone()
    if facet_row is None:
        return []
    rows = conn.execute(
        """
        SELECT
            i.item_id,
            i.provider,
            i.source_cluster,
            i.source_section,
            i.title,
            i.canonical_url,
            i.discussion_url,
            i.target_url,
            i.published_at,
            i.observed_at,
            i.snapshot_date,
            i.content_scope,
            i.raw_engagement,
            i.domain_ids AS item_domain_ids,
            f.domain_ids AS facet_domain_ids,
            COALESCE(si.story_id, '') AS story_id,
            COALESCE(s.project_scores, '{}') AS project_scores,
            COALESCE(s.source_count, 0) AS story_source_count
        FROM release_items AS i
        LEFT JOIN item_facets AS f
          ON f.facet_release_id = ?
         AND f.item_id = i.item_id
        LEFT JOIN engine_story_items AS si
          ON si.story_release_id = ?
         AND si.item_id = i.item_id
        LEFT JOIN engine_stories AS s
          ON s.story_release_id = ?
         AND s.story_id = si.story_id
        WHERE i.release_id = ?
        ORDER BY COALESCE(i.published_at, i.observed_at, i.snapshot_date) DESC, i.item_id
        LIMIT 10000
        """,
        (facet_row["facet_release_id"], story_release_id, story_release_id, data_release_id),
    ).fetchall()
    candidates: list[dict[str, object]] = []
    for row in rows:
        title = str(row["title"] or "").strip()
        if not title:
            continue
        provider = str(row["provider"] or "")
        discussion_url = _safe_url(str(row["discussion_url"] or ""))
        target_url = _safe_url(str(row["target_url"] or ""))
        canonical_url = _safe_url(str(row["canonical_url"] or ""))
        if provider == "reddit":
            # A Reddit link-post should lead to the underlying material first;
            # the discussion stays one click away.  Self posts still open at
            # the Reddit discussion URL.
            primary_url = target_url or discussion_url or canonical_url
            secondary_url = (
                discussion_url if discussion_url and discussion_url != primary_url else ""
            )
            secondary_label = "Обсуждение" if secondary_url else ""
        else:
            primary_url = canonical_url or target_url
            secondary_url = (
                discussion_url if discussion_url and discussion_url != primary_url else ""
            )
            secondary_label = "Reddit" if secondary_url else ""
        if not primary_url:
            continue
        domain_ids = _json_list_value(row["facet_domain_ids"] or row["item_domain_ids"], ["other"])
        project_scores = _json_dict_value(row["project_scores"])
        raw_engagement = _json_dict_value(row["raw_engagement"])
        score = _item_reading_score(
            row=row,
            domain_ids=domain_ids,
            project_scores=project_scores,
            raw_engagement=raw_engagement,
            radar_date=radar_date,
        )
        candidates.append(
            {
                "item_id": row["item_id"],
                "title": title,
                "provider": provider,
                "provider_label": _provider_label(provider),
                "source_cluster": row["source_cluster"],
                "source_section": row["source_section"],
                "primary_url": primary_url,
                "secondary_url": secondary_url,
                "secondary_label": secondary_label,
                "story_id": row["story_id"],
                "story_source_count": int(row["story_source_count"] or 0),
                "domain_ids": domain_ids[:3],
                "domain_labels": [_domain_label(domain_id) for domain_id in domain_ids[:3]],
                "published_at": row["published_at"] or row["observed_at"] or row["snapshot_date"],
                "score": round(score, 1),
                "reason": " / ".join(
                    value
                    for value in [
                        "профиль РБК" if _numeric_dict_value(project_scores, "rbc") >= 70 else "",
                        "книга" if _numeric_dict_value(project_scores, "book") >= 60 else "",
                        "Reddit-сигнал" if provider == "reddit" else "",
                        "cross-source story" if int(row["story_source_count"] or 0) > 1 else "",
                    ]
                    if value
                )
                or "ежедневное чтение",
                # One story should appear once even when it has a Reddit
                # discussion plus several articles.  URLs are the fallback
                # when the current StoryRelease has not joined the item yet.
                "_dedupe_key": str(row["story_id"] or "")
                or _normalized_reading_key(title, target_url or canonical_url, primary_url),
                "_primary_domain": domain_ids[0] if domain_ids else "other",
                "_is_current_day": str(row["snapshot_date"] or "") == radar_date,
            }
        )

    def sort_key(item: dict[str, object]) -> tuple[float, str]:
        score_raw = item.get("score", 0.0)
        score = float(score_raw) if isinstance(score_raw, int | float | str) else 0.0
        return (-score, str(item.get("title", "")))

    candidates.sort(key=sort_key)
    selected: list[dict[str, object]] = []
    seen: set[str] = set()
    provider_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    reddit_count = 0
    # A daily reading list is current-day first; a smaller collection falls
    # back to the rest of the immutable release to retain a useful 20 items.
    ordered_candidates = [item for item in candidates if bool(item["_is_current_day"])] + [
        item for item in candidates if not bool(item["_is_current_day"])
    ]
    for item in ordered_candidates:
        key = str(item["_dedupe_key"])
        provider = str(item["provider"])
        primary_domain = str(item["_primary_domain"])
        if key in seen:
            continue
        if provider == "reddit" and reddit_count >= 6:
            continue
        if provider_counts.get(provider, 0) >= 4:
            continue
        if domain_counts.get(primary_domain, 0) >= 5:
            continue
        seen.add(key)
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        domain_counts[primary_domain] = domain_counts.get(primary_domain, 0) + 1
        if provider == "reddit":
            reddit_count += 1
        item.pop("_dedupe_key", None)
        item.pop("_primary_domain", None)
        item.pop("_is_current_day", None)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def _pulse_topic_clouds(
    conn: sqlite3.Connection,
    signal_release_id: str,
    *,
    examples: int = 3,
) -> list[dict[str, object]]:
    """Тематики Pulse со счётчиком и живыми примерами заголовков.

    Тип сигнала — единственная работающая тематическая ось: ``domain_ids_json`` у сигналов
    практически всегда ``other``, потому что фасеты считаются по тексту материала, а у
    Reddit-поста текста обычно нет. Примеры нужны, чтобы название тематики было понятно
    без клика: «Боли» само по себе не объясняет, что внутри.
    """
    rows = conn.execute(
        """
        SELECT signal_type, COUNT(*) AS total, AVG(pulse_score) AS avg_pulse
        FROM community_signals
        WHERE signal_release_id = ?
        GROUP BY signal_type
        ORDER BY total DESC
        """,
        (signal_release_id,),
    ).fetchall()
    clouds: list[dict[str, object]] = []
    for row in rows:
        signal_type = str(row["signal_type"])
        sample = conn.execute(
            """
            SELECT title FROM community_signals
            WHERE signal_release_id = ? AND signal_type = ?
            ORDER BY pulse_score DESC LIMIT ?
            """,
            (signal_release_id, signal_type, max(examples, 0)),
        ).fetchall()
        clouds.append(
            {
                "signal_type": signal_type,
                "label": signal_type_label(signal_type),
                "total": int(row["total"]),
                "avg_pulse": round(float(row["avg_pulse"] or 0.0), 1),
                "examples": [str(item["title"]) for item in sample],
            }
        )
    return clouds


def _build_today_reddit_new(
    conn: sqlite3.Connection,
    signal_release_id: str,
    *,
    exclude_item_ids: set[str],
    signal_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Свежие Reddit-посты, которых ещё не было на странице.

    «Новое» здесь — по дате публикации, а не по полю ``novelty``: оно насыщено
    (>0.5 у 99% сигналов) и ничего не разделяет. Из выборки исключается всё, что уже
    показано в ленте чтения, — иначе блок дублировал бы соседний.

    Разнообразие держится квотами по типу сигнала и сабреддиту, причём политика
    ограничена жёстче остальных: у ``policy_politics`` самый высокий средний pulse,
    и без отдельного потолка она вытесняет всё остальное.
    """
    rows = conn.execute(
        """
        SELECT cs.item_id, cs.subreddit, cs.signal_type, cs.title,
               cs.discussion_url, cs.target_url, cs.pulse_score,
               ri.published_at,
               COALESCE(json_extract(ri.raw_engagement, '$.score'), 0) AS reddit_score,
               COALESCE(json_extract(ri.raw_engagement, '$.comments'), 0) AS reddit_comments
        FROM community_signals cs
        JOIN signal_releases sr ON sr.signal_release_id = cs.signal_release_id
        LEFT JOIN release_items ri
               ON ri.item_id = cs.item_id AND ri.release_id = sr.data_release_id
        WHERE cs.signal_release_id = ?
        ORDER BY ri.published_at DESC, cs.pulse_score DESC
        """,
        (signal_release_id,),
    ).fetchall()

    selected: list[dict[str, object]] = []
    per_type: dict[str, int] = {}
    per_subreddit: dict[str, int] = {}
    for row in rows:
        item_id = str(row["item_id"])
        if item_id in exclude_item_ids:
            continue
        row_type = str(row["signal_type"])
        if signal_type and row_type != signal_type:
            continue
        subreddit = str(row["subreddit"] or "")
        cap = _NEW_REDDIT_POLITICS_CAP if row_type == "policy_politics" else _NEW_REDDIT_PER_TYPE
        # Явно выбранная тематика — это уже запрос на неё, квота по типу не нужна.
        if not signal_type and per_type.get(row_type, 0) >= cap:
            continue
        if per_subreddit.get(subreddit, 0) >= _NEW_REDDIT_PER_SUBREDDIT:
            continue
        per_type[row_type] = per_type.get(row_type, 0) + 1
        per_subreddit[subreddit] = per_subreddit.get(subreddit, 0) + 1
        discussion_url = _safe_url(str(row["discussion_url"] or ""))
        target_url = _safe_url(str(row["target_url"] or ""))
        selected.append(
            {
                "item_id": item_id,
                "title": str(row["title"] or ""),
                "subreddit": subreddit,
                "signal_type": row_type,
                "signal_type_label": signal_type_label(row_type),
                "discussion_url": discussion_url,
                # Ссылка на первоисточник, если пост ведёт наружу; иначе только обсуждение.
                "target_url": target_url if target_url != discussion_url else "",
                "pulse_score": round(float(row["pulse_score"] or 0.0), 1),
                "reddit_score": int(row["reddit_score"] or 0),
                "reddit_comments": int(row["reddit_comments"] or 0),
                "published_at": str(row["published_at"] or "")[:10],
            }
        )
        if len(selected) >= limit:
            break
    return selected


@lru_cache(maxsize=24)
def _cached_today_reading_list(
    engine_path_value: str,
    *,
    publication_id: str,
    data_release_id: str,
    story_release_id: str,
    date: str,
    profile: str,
) -> tuple[dict[str, object], ...]:
    """Compute the compact reading queue once per immutable publication.

    Today loads two small pages to stay under the proxy response cap.  Without
    this cache each page rescored the entire release on FastAPI's async event
    loop, leaving both panels in the loading state for a long time.
    """

    del publication_id, data_release_id, story_release_id  # Cache identity, not query inputs.
    engine_conn = open_engine_readonly(Path(engine_path_value))
    try:
        radar = _load_today_engine_radar(engine_conn, date=date, profile=profile)
        if radar is None:
            return ()
        return tuple(_build_today_reading_list(engine_conn, radar.model_dump(), limit=20))
    finally:
        engine_conn.close()


def _load_today_engine_radar(
    conn: sqlite3.Connection, *, date: str | None, profile: str
) -> Any | None:
    """Resolve the published (or evaluated preview) broad radar for Today."""
    from .v2 import _engine_radar, _resolve_latest_evaluated_preview

    publication = get_current_publication(conn, "broad")
    if publication:
        release = get_data_release(conn, publication.data_release_id)
    else:
        preview = _resolve_latest_evaluated_preview(conn, channel="broad")
        release = preview[1] if preview else None
    engine_date = date or (max(release.dates) if release and release.dates else None)
    if not engine_date:
        return None
    return _engine_radar(
        conn,
        date=engine_date,
        profile=profile,
        mode="broad",
        domain=None,
        channel="broad",
        publication_id=None,
    )


def _today_change_candidates(radar: Any, analysis_query: str) -> list[dict[str, object]]:
    lifecycle_order = (
        "growing",
        "new",
        "resurfacing",
        "stable",
        "insufficient_history",
        "fading",
    )
    # Today is an editorial brief, not a diagnostics surface.  An embedding
    # cluster with a token-bag name (for example ``my ai job me``) is useful in
    # the Engine lab but actively misleading in a reader-facing card.  Surface
    # only trends that passed the bounded review and have a usable name; all
    # candidates remain available in Trends/Engine for inspection.
    changes = [
        trend
        for lifecycle in lifecycle_order
        for trend in radar.shelves.get(lifecycle, [])
        if str(trend.get("review_status") or "pending") == "confirmed"
        and not is_bad_trend_name(str(trend.get("title") or trend.get("name_ru") or ""))
    ][:5]
    cards: list[dict[str, object]] = []
    for trend in changes:
        decorated = _decorate_today_trend(dict(trend), analysis_query)
        # Shelves contain evidence and provenance for Radar. Today needs only
        # a small card contract; returning the full object defeated progressive
        # loading on reverse proxies with a small response limit.
        cards.append(
            {
                "url": str(decorated.get("url") or ""),
                "title": str(decorated.get("title") or "")[:240],
                "pattern": str(decorated.get("pattern") or "")[:360],
                "lifecycle_label": str(decorated.get("lifecycle_label") or ""),
                "source_scope_label": str(decorated.get("source_scope_label") or ""),
                "source_scope": str(decorated.get("source_scope") or ""),
                "review_label": str(decorated.get("review_label") or ""),
                "confidence_pct": _as_int(decorated.get("confidence_pct")),
                "source_count": _as_int(decorated.get("source_count")),
                "story_count": _as_int(decorated.get("story_count")),
            }
        )
    return cards


def _build_today_dashboard(
    radar: dict[str, object], changes: list[dict[str, object]], analysis_query: str
) -> dict[str, object]:
    run_raw = radar.get("run")
    run = cast(dict[str, Any], run_raw) if isinstance(run_raw, dict) else {}
    shelves_raw = radar.get("shelves")
    shelves = cast(dict[str, Any], shelves_raw) if isinstance(shelves_raw, dict) else {}
    all_trends = [
        trend
        for trends in shelves.values()
        if isinstance(trends, list)
        for trend in trends
        if isinstance(trend, dict)
    ]
    source_coverage_raw = run.get("source_coverage")
    source_coverage = (
        cast(dict[str, Any], source_coverage_raw) if isinstance(source_coverage_raw, dict) else {}
    )
    matrix_raw = radar.get("matrix")
    matrix = matrix_raw if isinstance(matrix_raw, list) else []
    source_clusters = sorted(
        {
            str(row.get("source_cluster"))
            for row in matrix
            if isinstance(row, dict) and row.get("source_cluster")
        }
    )
    domains_raw = radar.get("domains")
    domains = domains_raw if isinstance(domains_raw, list) else []
    top_domains: list[dict[str, object]] = []
    for domain in domains[:8]:
        if not isinstance(domain, dict) or not domain.get("domain_id"):
            continue
        domain_id = str(domain["domain_id"])
        domain_query = urlencode({"channel": "broad", "domain": domain_id})
        enriched_domain = dict(domain)
        # A domain is a way to investigate concrete events, not a static
        # counter.  The click therefore opens the filtered Story workspace.
        enriched_domain["url"] = f"/stories?{domain_query}"
        top_domains.append(enriched_domain)
    release_dates = run.get("release_dates", [])
    if not isinstance(release_dates, list):
        release_dates = []
    status_notes: list[dict[str, str]] = []
    if radar.get("input_status") == "partial":
        status_notes.append(
            {
                "title": "Partial",
                "text": (
                    "Выпуск опубликован с неполным входным сбором. Аналитика доступна, "
                    "но покрытие источников нужно читать как частичное."
                ),
            }
        )
    if radar.get("history_status") == "insufficient_history":
        status_notes.append(
            {
                "title": "Мало истории",
                "text": (
                    "Engine пока не накопил достаточно последовательных релизов, поэтому "
                    "не рисует честные growing/fading и помечает карточки как кандидаты."
                ),
            }
        )
    if radar.get("preview"):
        status_notes.append(
            {
                "title": "Preview",
                "text": "Показан latest evaluated release, а не production RadarPublication.",
            }
        )
    if radar.get("serving_previous_publication"):
        status_notes.append(
            {
                "title": "Предыдущая публикация",
                "text": (
                    "Для выбранной даты нет отдельной публикации, показана последняя проверенная."
                ),
            }
        )

    return {
        "item_count": int(run.get("item_count") or 0) if isinstance(run, dict) else 0,
        "source_count": len(source_coverage),
        "source_cluster_count": len(source_clusters),
        "trend_count": len(all_trends),
        "cross_source_trend_count": sum(
            1
            for trend in all_trends
            if str(trend.get("source_scope") or "") == "cross_source"
            or int(trend.get("source_count") or 0) > 1
        ),
        "confirmed_trend_count": sum(
            1 for trend in all_trends if str(trend.get("review_status") or "") == "confirmed"
        ),
        "candidate_trend_count": sum(
            1 for trend in all_trends if str(trend.get("review_status") or "pending") != "confirmed"
        ),
        "change_count": len(changes),
        "top_domains": top_domains,
        "source_clusters": source_clusters,
        "release_window": " → ".join([str(release_dates[0]), str(release_dates[-1])])
        if release_dates
        else "",
        "status_notes": status_notes,
        "quick_links": [
            ("News", f"/news?{analysis_query}", "сырые материалы"),
            ("Stories", f"/stories?{analysis_query}", "конкретные события"),
            ("Trends", f"/trends?{analysis_query}", "паттерны поверх событий"),
            ("Reddit Pulse", "/pulse", "сигналы сообществ"),
            ("Radar", f"/runs/{radar.get('date')}/radar?{analysis_query}", "полный workspace"),
        ],
    }


@router.get("/today", response_class=HTMLResponse)
async def today_page(
    request: Request,
    date: str | None = None,
    profile: str = DEFAULT_PROFILE,
    reddit_type: str | None = None,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Главная страница: briefing на сегодня."""
    engine_path = _engine_path()
    if engine_path.exists():
        engine_conn = open_engine_readonly(engine_path)
        try:
            published_radar = _load_today_engine_radar(engine_conn, date=date, profile=profile)
        finally:
            engine_conn.close()

        if published_radar is not None:
            analysis_query = _analysis_query("broad", None)
            radar_payload = published_radar.model_dump()
            decorated_changes = _today_change_candidates(published_radar, analysis_query)
            # Render the first page on the server.  The browser may then append
            # the remaining ten records, but a cached/failed static JavaScript
            # asset can never leave the primary daily reading list as a
            # permanent "Подбираю…" placeholder.
            try:
                initial_reading = list(
                    _cached_today_reading_list(
                        str(engine_path),
                        publication_id=str(radar_payload.get("publication_id") or "preview"),
                        data_release_id=str(radar_payload.get("data_release_id") or ""),
                        story_release_id=str(radar_payload.get("story_release_id") or ""),
                        date=str(radar_payload["date"]),
                        profile=profile,
                    )[:10]
                )
            except (HTTPException, OSError, sqlite3.Error):
                initial_reading = []

            # Свежее с Reddit: то, чего ещё нет в ленте чтения выше. Считается по
            # опубликованному signal release, поэтому блок остаётся частью выпуска,
            # а не отдельной живой выборкой из корпуса.
            reddit_new: list[dict[str, object]] = []
            reddit_types: list[dict[str, object]] = []
            engine_conn = open_engine_readonly(engine_path)
            try:
                from .v2 import _latest_signal_release

                # Сначала сигналы того же data release, что и публикация. Если Pulse
                # считался на другом релизе (частый случай: радар семидневный, а Pulse
                # однодневный), берём последний финализированный — как это делает /pulse.
                sig_id = _latest_signal_release(
                    engine_conn,
                    data_release_id=str(radar_payload.get("data_release_id") or "") or None,
                ) or _latest_signal_release(engine_conn)
                if sig_id:
                    already_shown = {
                        str(item.get("item_id")) for item in initial_reading if item.get("item_id")
                    }
                    reddit_new = _build_today_reddit_new(
                        engine_conn,
                        sig_id,
                        exclude_item_ids=already_shown,
                        signal_type=reddit_type or None,
                    )
                    reddit_types = [
                        {
                            "signal_type": cloud["signal_type"],
                            "label": cloud["label"],
                            "total": cloud["total"],
                        }
                        for cloud in _pulse_topic_clouds(engine_conn, sig_id, examples=0)
                    ]
            except (HTTPException, OSError, sqlite3.Error):
                reddit_new, reddit_types = [], []
            finally:
                engine_conn.close()

            return templates.TemplateResponse(
                request=request,
                name="engine_today.html",
                context={
                    "radar": radar_payload,
                    "changes": decorated_changes,
                    "reddit_new": reddit_new,
                    "reddit_types": reddit_types,
                    "reddit_type": reddit_type or "",
                    "reddit_quotas": {
                        "per_type": _NEW_REDDIT_PER_TYPE,
                        "per_subreddit": _NEW_REDDIT_PER_SUBREDDIT,
                        "politics": _NEW_REDDIT_POLITICS_CAP,
                    },
                    "dashboard": _build_today_dashboard(
                        radar_payload,
                        decorated_changes,
                        analysis_query,
                    ),
                    "initial_reading": initial_reading,
                    "reading_endpoint": "/ui/today-reading?"
                    + urlencode({"date": radar_payload["date"], "profile": profile}),
                    "changes_endpoint": "/ui/today-changes?"
                    + urlencode({"date": radar_payload["date"], "profile": profile}),
                    "reddit_endpoint": "/ui/today-reddit?"
                    + urlencode({"date": radar_payload["date"], "profile": profile}),
                    "analysis_query": analysis_query,
                },
            )

    # Публикации нет — честная заглушка вместо legacy-брифинга из compass.db.
    # Radar читает только опубликованный релиз; собирать параллельную картину из
    # сырого корпуса значило бы показывать не то, что прошло гейты качества.
    return templates.TemplateResponse(
        request=request,
        name="components/empty_state.html",
        context={
            "message": "Публикации нет. Запустите `reddit-compass engine cycle`.",
        },
    )


def _render_fragment(name: str, context: dict[str, object]) -> HTMLResponse:
    """Отрисовать партиал как HTML-фрагмент для догрузки на клиенте.

    Ленты /today отдают готовую разметку, а не JSON: карточка описана в одном
    месте — в Jinja, — и браузеру остаётся только вставить её. Раньше та же
    разметка существовала вторым экземпляром в императивном JS, и любая правка
    требовала синхронных изменений на двух языках.
    """
    return HTMLResponse(templates.get_template(name).render(**context))


@router.get("/ui/today-changes", response_class=HTMLResponse)
def today_changes_feed(
    date: str | None = None,
    profile: str = DEFAULT_PROFILE,
) -> HTMLResponse:
    """HTML-фрагмент с карточками верхних trend-кандидатов."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return HTMLResponse("")
    engine_conn = open_engine_readonly(engine_path)
    try:
        radar = _load_today_engine_radar(engine_conn, date=date, profile=profile)
        if radar is None:
            return HTMLResponse("")
        cards = _today_change_candidates(radar, _analysis_query("broad", None))
        return HTMLResponse(
            "".join(
                templates.get_template("components/today_change_card.html").render(trend=trend)
                for trend in cards
            )
        )
    except (HTTPException, OSError, sqlite3.Error):
        return HTMLResponse("")
    finally:
        engine_conn.close()


@router.get("/ui/today-reddit", response_class=HTMLResponse)
def today_reddit_feed(
    date: str | None = None,
    profile: str = DEFAULT_PROFILE,
    reddit_type: str | None = None,
) -> HTMLResponse:
    """HTML-фрагмент блока «Новое на Reddit» под выбранную тематику.

    Раньше клик по чипу перезагружал весь /today: заново считались радар,
    дашборд, лента чтения и облака тематик — ради подмены одного списка.
    """
    engine_path = _engine_path()
    if not engine_path.exists():
        return HTMLResponse("")
    engine_conn = open_engine_readonly(engine_path)
    try:
        radar = _load_today_engine_radar(engine_conn, date=date, profile=profile)
        if radar is None:
            return HTMLResponse("")
        payload = radar.model_dump()
        from .v2 import _latest_signal_release

        sig_id = _latest_signal_release(
            engine_conn,
            data_release_id=str(payload.get("data_release_id") or "") or None,
        ) or _latest_signal_release(engine_conn)
        if not sig_id:
            return HTMLResponse("")
        # Исключения те же, что и при серверном рендере: посты, уже показанные
        # в ленте чтения выше, не должны дублироваться после смены фильтра.
        already_shown = {
            str(item.get("item_id"))
            for item in _cached_today_reading_list(
                str(engine_path),
                publication_id=str(payload.get("publication_id") or "preview"),
                data_release_id=str(payload.get("data_release_id") or ""),
                story_release_id=str(payload.get("story_release_id") or ""),
                date=str(payload["date"]),
                profile=profile,
            )[:10]
            if item.get("item_id")
        }
        posts = _build_today_reddit_new(
            engine_conn,
            sig_id,
            exclude_item_ids=already_shown,
            signal_type=reddit_type or None,
        )
        return _render_fragment("components/reddit_new_list.html", {"reddit_new": posts})
    except (HTTPException, OSError, sqlite3.Error):
        return HTMLResponse("")
    finally:
        engine_conn.close()


@router.get("/ui/today-reading", response_class=HTMLResponse)
def today_reading_feed(
    date: str | None = None,
    profile: str = DEFAULT_PROFILE,
    offset: int = 0,
    limit: int = 10,
) -> HTMLResponse:
    """Постраничный HTML-фрагмент ленты чтения."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return HTMLResponse("")
    engine_conn = open_engine_readonly(engine_path)
    try:
        radar = _load_today_engine_radar(engine_conn, date=date, profile=profile)
        if radar is None:
            return HTMLResponse("")
        radar_payload = radar.model_dump()
        # Keep each response below the reverse proxy's small-response limit;
        # the client requests two deterministic pages for the complete top-20.
        page_size = min(max(limit, 1), 10)
        start = max(offset, 0)
        items = _cached_today_reading_list(
            str(engine_path),
            publication_id=str(radar_payload.get("publication_id") or "preview"),
            data_release_id=str(radar_payload.get("data_release_id") or ""),
            story_release_id=str(radar_payload.get("story_release_id") or ""),
            date=str(radar_payload["date"]),
            profile=profile,
        )
        template = templates.get_template("components/today_reading_item.html")
        return HTMLResponse(
            "".join(
                # Нумерация продолжает отрендеренную на сервере первую страницу.
                template.render(item=item, rank=start + index + 1)
                for index, item in enumerate(items[start : start + page_size])
            )
        )
    except (HTTPException, OSError, sqlite3.Error):
        # Today itself can still render its publication/preview state.  Do not
        # turn an unavailable optional reading feed into a broken dashboard.
        return HTMLResponse("")
    finally:
        engine_conn.close()


@router.get("/news", response_class=HTMLResponse)
async def news_page(
    request: Request,
    date: str | None = None,
    domain: str | None = None,
    provider: str | None = None,
    source_cluster: str | None = None,
    q: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """News inbox: raw published items from immutable DataRelease."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_news

    engine_conn = open_engine_readonly(engine_path)
    try:
        news = _engine_news(
            engine_conn,
            channel=channel,
            publication_id=publication_id,
            date=date,
            domain=domain,
            provider=provider,
            source_cluster=source_cluster,
            q=q,
            page=max(page, 1),
            page_size=50,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="news.html",
        context={
            "news": news.model_dump(),
            "filters": {
                "date": date or "",
                "domain": domain or "",
                "provider": provider or "",
                "source_cluster": source_cluster or "",
                "q": q or "",
            },
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
        },
    )


@router.get("/stories", response_class=HTMLResponse)
async def stories_page(
    request: Request,
    domain: str | None = None,
    q: str | None = None,
    project_id: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Published story workspace: concrete events, not raw news."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_stories

    engine_conn = open_engine_readonly(engine_path)
    try:
        stories = _engine_stories(
            engine_conn,
            channel=channel,
            publication_id=publication_id,
            domain=domain,
            q=q,
            project_id=project_id,
            page=max(page, 1),
            page_size=50,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="engine_stories.html",
        context={
            "stories": stories.model_dump(),
            "filters": {"domain": domain or "", "q": q or "", "project_id": project_id or ""},
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
            "page_url": _page_url(
                "/stories",
                channel=channel,
                publication_id=publication_id,
                domain=domain,
                q=q,
                project_id=project_id,
            ),
        },
    )


@router.get("/trends", response_class=HTMLResponse)
async def trends_page(
    request: Request,
    domain: str | None = None,
    lifecycle: str | None = None,
    review_status: str | None = None,
    project_id: str | None = None,
    q: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Published trend workspace: recurring patterns over stories."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_trends

    engine_conn = open_engine_readonly(engine_path)
    try:
        trends = _engine_trends(
            engine_conn,
            channel=channel,
            publication_id=publication_id,
            domain=domain,
            lifecycle=lifecycle,
            review_status=review_status,
            project_id=project_id,
            q=q,
            page=max(page, 1),
            page_size=50,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="engine_trends.html",
        context={
            "trends": trends.model_dump(),
            "filters": {
                "domain": domain or "",
                "lifecycle": lifecycle or "",
                "review_status": review_status or "",
                "project_id": project_id or "",
                "q": q or "",
            },
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
            "page_url": _page_url(
                "/trends",
                channel=channel,
                publication_id=publication_id,
                domain=domain,
                lifecycle=lifecycle,
                review_status=review_status,
                project_id=project_id,
                q=q,
            ),
        },
    )


@router.get("/pulse", response_class=HTMLResponse)
async def pulse_page(
    request: Request,
    sort: str = "pulse",
    signal_type: str | None = None,
    subreddit: str | None = None,
    q: str | None = None,
    page: int = 1,
    view: str = "cards",
) -> HTMLResponse:
    """Reddit Pulse: Reddit-native community signals with raw engagement."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine DB не найдена."},
            status_code=404,
        )
    from .v2 import (
        _engine_pulse_signals,
        _latest_signal_release,
        _safe_url,
    )

    engine_conn = open_engine_readonly(engine_path)
    try:
        sig_id = _latest_signal_release(engine_conn)
        if not sig_id:
            return templates.TemplateResponse(
                request=request,
                name="empty.html",
                context={"message": "Нет Reddit Pulse данных."},
                status_code=404,
            )
        # Режим ссылок: клик по облаку тематики даёт топ-20 прямых ссылок на посты,
        # а не сетку карточек — на этом шаге нужен сам пост, а не его метрики.
        links_view = view == "links"
        page_size = 20 if links_view else 30
        offset = (max(page, 1) - 1) * page_size
        signals, total = _engine_pulse_signals(
            engine_conn,
            sig_id,
            signal_type=signal_type,
            subreddit=subreddit,
            q=q,
            sort=sort,
            limit=page_size,
            offset=offset,
        )
        # Summary for KPI + categorized shelves (only on page 1, no filters)
        pulse_summary = None
        if page == 1 and not signal_type and not subreddit:
            top_pulse, _ = _engine_pulse_signals(engine_conn, sig_id, limit=8)
            top_pain, _ = _engine_pulse_signals(
                engine_conn, sig_id, signal_type="pain_point", limit=5
            )
            top_ai, _ = _engine_pulse_signals(
                engine_conn, sig_id, signal_type="ai_capability", limit=5
            )
            gap_rows = engine_conn.execute(
                "SELECT cs.signal_id, cs.item_id, cs.subreddit, "
                "cs.signal_type, cs.title, cs.discussion_url, "
                "cs.pulse_score, "
                "COALESCE(json_extract(ri.raw_engagement, '$.score'), 0) "
                "as reddit_score, "
                "COALESCE(json_extract(ri.raw_engagement, '$.comments'), 0) "
                "as reddit_comments, "
                "COALESCE(json_extract(ri.raw_engagement, "
                "'$.upvote_ratio'), 0) as upvote_ratio "
                "FROM community_signals cs "
                "LEFT JOIN release_items ri "
                "ON ri.item_id = cs.item_id "
                "AND ri.release_id = (SELECT data_release_id "
                "FROM signal_releases WHERE signal_release_id = ?) "
                "WHERE cs.signal_release_id = ? "
                "AND cs.pulse_score >= 60 "
                "AND cs.mainstream_coverage_count < 2 "
                "ORDER BY cs.pulse_score DESC LIMIT 5",
                (sig_id, sig_id),
            ).fetchall()
            mainstream_gap = [
                {
                    "signal_id": r["signal_id"],
                    "item_id": r["item_id"],
                    "subreddit": r["subreddit"],
                    "signal_type": r["signal_type"],
                    "title": r["title"],
                    "discussion_url": _safe_url(r["discussion_url"]),
                    "pulse_score": r["pulse_score"],
                    "reddit_score": r["reddit_score"],
                    "reddit_comments": r["reddit_comments"],
                    "upvote_ratio": r["upvote_ratio"],
                }
                for r in gap_rows
            ]
            # By-type counts
            type_rows = engine_conn.execute(
                "SELECT signal_type, COUNT(*) as cnt "
                "FROM community_signals WHERE signal_release_id = ? "
                "GROUP BY signal_type ORDER BY cnt DESC",
                (sig_id,),
            ).fetchall()
            by_type = {r["signal_type"]: r["cnt"] for r in type_rows}
            pulse_summary = {
                "signal_release_id": sig_id,
                "total_signals": total,
                "by_type": by_type,
                "top_pulse": top_pulse,
                "top_pain": top_pain,
                "top_ai": top_ai,
                "mainstream_gap": mainstream_gap,
            }
        # Available signal types for dropdown
        type_rows = engine_conn.execute(
            "SELECT DISTINCT signal_type FROM community_signals "
            "WHERE signal_release_id = ? ORDER BY signal_type",
            (sig_id,),
        ).fetchall()
        signal_types = [r["signal_type"] for r in type_rows]
        topic_clouds = _pulse_topic_clouds(engine_conn, sig_id)
    finally:
        engine_conn.close()

    total_pages = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(
        request=request,
        name="engine_pulse.html",
        context={
            "signals": signals,
            "total": total,
            "page": max(page, 1),
            "page_size": page_size,
            "total_pages": total_pages,
            "pulse_summary": pulse_summary,
            "topic_clouds": topic_clouds,
            "links_view": links_view,
            "filters": {
                "sort": sort,
                "signal_type": signal_type or "",
                "subreddit": subreddit or "",
                "q": q or "",
                "view": view,
            },
            "page_url": _page_url(
                "/pulse",
                sort=sort,
                signal_type=signal_type,
                subreddit=subreddit,
                q=q,
                view=view,
            ),
            "signal_type_labels": _SIGNAL_TYPE_LABELS,
            "signal_types": signal_types,
            "sort_options": [
                ("pulse", "Pulse score"),
                ("score", "Reddit score"),
                ("comments", "Comments"),
                ("velocity", "Velocity"),
                ("ratio", "Upvote ratio"),
            ],
        },
    )


@router.get("/trends/{trend_id}", response_class=HTMLResponse)
async def trend_detail_page(
    request: Request,
    trend_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
) -> HTMLResponse:
    """Published trend detail: pattern, member stories and evidence."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_trend_detail

    engine_conn = open_engine_readonly(engine_path)
    try:
        trend = _engine_trend_detail(
            engine_conn,
            trend_id=trend_id,
            channel=channel,
            publication_id=publication_id,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="engine_trend_detail.html",
        context={
            "trend": trend.model_dump(),
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
        },
    )


@router.get("/projects", response_class=HTMLResponse)
async def projects_redirect(
    channel: str = "broad",
    publication_id: str | None = None,
) -> RedirectResponse:
    return RedirectResponse(
        f"/projects/rbc?{_analysis_query(channel, publication_id)}",
        status_code=302,
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_lens_page(
    request: Request,
    project_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
) -> HTMLResponse:
    """Project lens over published stories and trends."""
    engine_path = _engine_path()
    if not engine_path.exists():
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": "Trend Engine publication не найдена."},
            status_code=404,
        )
    from .v2 import _engine_project_lens

    engine_conn = open_engine_readonly(engine_path)
    try:
        lens = _engine_project_lens(
            engine_conn,
            project_id=project_id,
            channel=channel,
            publication_id=publication_id,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request=request,
            name="empty.html",
            context={"message": str(exc.detail)},
            status_code=exc.status_code,
        )
    finally:
        engine_conn.close()
    return templates.TemplateResponse(
        request=request,
        name="project_lens.html",
        context={
            "lens": lens.model_dump(),
            "channel": channel,
            "publication_id": publication_id or "",
            "analysis_query": _analysis_query(channel, publication_id),
        },
    )


@router.get("/stories/{story_id}", response_class=HTMLResponse)
async def story_page(
    request: Request,
    story_id: str,
    channel: str = "broad",
    publication_id: str | None = None,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Страница story: timeline, evidence, research state."""
    engine_path = _engine_path()
    if engine_path.exists():
        from .v2 import _engine_story_detail

        engine_conn = open_engine_readonly(engine_path)
        try:
            engine_story = _engine_story_detail(
                engine_conn,
                story_id=story_id,
                channel=channel,
                publication_id=publication_id,
            )
        except HTTPException as exc:
            engine_story = None
            if exc.status_code != 404:
                return templates.TemplateResponse(
                    request=request,
                    name="empty.html",
                    context={"message": str(exc.detail)},
                    status_code=exc.status_code,
                )
        finally:
            engine_conn.close()
        if engine_story is not None:
            return templates.TemplateResponse(
                request=request,
                name="engine_story_detail.html",
                context={
                    "story": engine_story.model_dump(),
                    "channel": channel,
                    "publication_id": publication_id or "",
                    "analysis_query": _analysis_query(channel, publication_id),
                },
            )

    # Опубликованного сюжета нет — заглушка вместо legacy-детали из compass.db.
    return templates.TemplateResponse(
        request=request,
        name="components/empty_state.html",
        context={"message": f"Сюжет {story_id} не найден в опубликованном релизе."},
        status_code=404,
    )


@router.get("/runs", response_class=HTMLResponse)
def runs_page(
    request: Request,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Operational ledger: collection facts plus derived Engine stages."""
    rows = conn.execute("SELECT * FROM runs ORDER BY snapshot_date DESC LIMIT 30").fetchall()

    from .view_models import status_label

    source_health_by_run: dict[str, list[dict[str, object]]] = {}
    if rows:
        run_ids = [str(row["run_id"]) for row in rows]
        placeholders = ", ".join("?" for _ in run_ids)
        health_rows = conn.execute(
            f"""SELECT run_id, source_id, provider, cluster, status, count,
                       duration_sec, error_code, message
                FROM source_health
                WHERE run_id IN ({placeholders})
                ORDER BY run_id, source_id""",
            run_ids,
        ).fetchall()
        for health in health_rows:
            # Provider×section rows complement but do not replace the adapter
            # stage.  The latter is what determines collection completeness.
            if ":" in str(health["source_id"]):
                continue
            source_health_by_run.setdefault(str(health["run_id"]), []).append(dict(health))

    releases_by_run: dict[str, dict[str, object]] = {}
    engine_path = _engine_path()
    if engine_path.exists():
        engine_conn = open_engine_readonly(engine_path)
        try:
            data_rows = engine_conn.execute(
                """SELECT release_id, run_ids_json, input_status, status, item_count,
                          observation_count, source_coverage_json, created_at, finalized_at
                   FROM data_releases
                   WHERE status = 'finalized'
                   ORDER BY finalized_at DESC, created_at DESC"""
            ).fetchall()
            run_id_set = {str(row["run_id"]) for row in rows}
            data_by_release: dict[str, dict[str, object]] = {}
            run_ids_by_release: dict[str, set[str]] = {}
            for data in data_rows:
                try:
                    release_run_ids = {str(value) for value in json.loads(data["run_ids_json"])}
                except (TypeError, ValueError, json.JSONDecodeError):
                    release_run_ids = set()
                matching_run_ids = run_id_set & release_run_ids
                if not matching_run_ids:
                    continue
                release = dict(data)
                release_id = str(data["release_id"])
                data_by_release[release_id] = release
                run_ids_by_release[release_id] = matching_run_ids

            if data_by_release:
                release_ids = list(data_by_release)
                release_placeholders = ", ".join("?" for _ in release_ids)
                facet_rows = engine_conn.execute(
                    f"""SELECT facet_release_id, data_release_id, status, created_at
                        FROM facet_releases
                        WHERE data_release_id IN ({release_placeholders})
                        ORDER BY created_at DESC""",
                    release_ids,
                ).fetchall()
                facets_by_data: dict[str, dict[str, object]] = {}
                for facet in facet_rows:
                    facets_by_data.setdefault(str(facet["data_release_id"]), dict(facet))
                facet_ids = [str(value["facet_release_id"]) for value in facets_by_data.values()]
                stories_by_facet: dict[str, dict[str, object]] = {}
                if facet_ids:
                    placeholders = ", ".join("?" for _ in facet_ids)
                    story_rows = engine_conn.execute(
                        f"""SELECT story_release_id, facet_release_id, status,
                                   metrics_json, created_at
                            FROM story_releases
                            WHERE facet_release_id IN ({placeholders})
                            ORDER BY created_at DESC""",
                        facet_ids,
                    ).fetchall()
                    for story in story_rows:
                        stories_by_facet.setdefault(str(story["facet_release_id"]), dict(story))
                story_ids = [str(value["story_release_id"]) for value in stories_by_facet.values()]
                trends_by_story: dict[str, dict[str, object]] = {}
                if story_ids:
                    placeholders = ", ".join("?" for _ in story_ids)
                    trend_rows = engine_conn.execute(
                        f"""SELECT trend_release_id, story_release_id, status, history_status,
                                   metrics_json, created_at
                            FROM trend_releases
                            WHERE story_release_id IN ({placeholders})
                            ORDER BY created_at DESC""",
                        story_ids,
                    ).fetchall()
                    for trend in trend_rows:
                        trends_by_story.setdefault(str(trend["story_release_id"]), dict(trend))
                publications = engine_conn.execute(
                    """SELECT p.publication_id, p.channel, p.data_release_id, p.input_status,
                               p.allow_partial, p.created_at,
                               CASE WHEN c.current_publication_id = p.publication_id
                                    THEN 1 ELSE 0 END
                                  AS is_current
                        FROM radar_publications p
                        LEFT JOIN published_channels c
                          ON c.current_publication_id = p.publication_id
                        ORDER BY is_current DESC, p.created_at DESC"""
                ).fetchall()
                publication_by_data: dict[str, dict[str, object]] = {}
                for publication in publications:
                    publication_by_data.setdefault(
                        str(publication["data_release_id"]), dict(publication)
                    )

                quality_by_data: dict[str, dict[str, object]] = {}
                # ``engine_quality_reports`` is written by ``engine cycle`` and
                # ``engine quality``.  The run journal is a read model: it must
                # never recompute taxonomy and clustering metrics for every
                # historical release while serving an HTTP request.
                try:
                    quality_rows = engine_conn.execute(
                        f"""SELECT data_release_id, story_release_id, trend_release_id,
                                   floors_json, passed, created_at
                            FROM engine_quality_reports
                            WHERE data_release_id IN ({release_placeholders})
                            ORDER BY created_at DESC""",
                        release_ids,
                    ).fetchall()
                except sqlite3.Error:
                    # Older Engine DBs are still readable during a rolling
                    # deploy.  Missing persisted reports are shown as such;
                    # source and release facts must remain available.
                    quality_rows = []

                for quality_row in quality_rows:
                    data_release_id = str(quality_row["data_release_id"])
                    if data_release_id in quality_by_data:
                        continue
                    facet = facets_by_data.get(data_release_id)
                    story = stories_by_facet.get(str(facet["facet_release_id"])) if facet else None
                    trend = trends_by_story.get(str(story["story_release_id"])) if story else None
                    if not story or not trend:
                        continue
                    if str(quality_row["story_release_id"]) != str(
                        story["story_release_id"]
                    ) or str(quality_row["trend_release_id"]) != str(trend["trend_release_id"]):
                        continue
                    try:
                        floors = json.loads(str(quality_row["floors_json"]))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        floors = []
                    quality_by_data[data_release_id] = {
                        "passed": bool(quality_row["passed"]),
                        "failed": [
                            str(floor.get("metric", "quality_invalid"))
                            for floor in floors
                            if isinstance(floor, dict) and not bool(floor.get("passed"))
                        ],
                        "created_at": str(quality_row["created_at"]),
                    }

                for data_release_id, release_record in data_by_release.items():
                    facet = facets_by_data.get(data_release_id)
                    story = stories_by_facet.get(str(facet["facet_release_id"])) if facet else None
                    trend = trends_by_story.get(str(story["story_release_id"])) if story else None
                    release_record["facet"] = facet
                    release_record["story"] = story
                    release_record["trend"] = trend
                    release_record["quality"] = quality_by_data.get(data_release_id)
                    release_record["publication"] = publication_by_data.get(data_release_id)

                def release_selection_key(
                    release: dict[str, object],
                ) -> tuple[int, int, int, int, int, str]:
                    """Prefer the active complete chain over a newer incomplete attempt."""
                    publication = cast(
                        dict[str, object] | None,
                        release.get("publication"),
                    )
                    return (
                        int(bool(publication and publication.get("is_current"))),
                        int(bool(release.get("trend"))),
                        int(bool(release.get("story"))),
                        int(bool(release.get("facet"))),
                        int(release.get("input_status") == "complete"),
                        str(release.get("created_at", "")),
                    )

                # Several immutable attempts can originate from the same raw
                # run.  The journal must show the operationally relevant
                # chain (current publication, then most complete analysis),
                # not merely the newest Data Release row.
                for data_release_id, release_record in data_by_release.items():
                    for run_id in run_ids_by_release[data_release_id]:
                        current = releases_by_run.get(run_id)
                        if current is None or release_selection_key(
                            release_record
                        ) > release_selection_key(current):
                            releases_by_run[run_id] = release_record
        except sqlite3.Error:
            # A run ledger must continue to show source truth even if a
            # read-only Engine file is temporarily unavailable.
            releases_by_run = {}
        finally:
            engine_conn.close()

    runs: list[dict[str, object]] = []
    for row in rows:
        run_id = str(row["run_id"])
        date = str(row["snapshot_date"])

        # Observations are per-run facts.  ``items.snapshot_date`` makes a
        # later re-observation look like it belonged to the wrong collection.
        item_count = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        health = source_health_by_run.get(run_id, [])
        source_ready = sum(1 for source in health if source.get("status") in {"ok", "empty"})
        source_total = len(health)
        run_release = releases_by_run.get(run_id)
        story = cast(dict[str, object] | None, run_release.get("story")) if run_release else None
        trend = cast(dict[str, object] | None, run_release.get("trend")) if run_release else None
        story_metrics = _json_dict_value(story.get("metrics_json") if story else "{}")
        trend_metrics = _json_dict_value(trend.get("metrics_json") if trend else "{}")
        quality = (
            cast(dict[str, object] | None, run_release.get("quality")) if run_release else None
        )
        publication = (
            cast(dict[str, object] | None, run_release.get("publication")) if run_release else None
        )
        stages = [
            {
                "name": "Сбор источников",
                "status": str(row["status"]),
                "detail": (
                    f"{source_ready}/{source_total} source adapters готовы"
                    if source_total
                    else "source health не записан"
                ),
            },
            {
                "name": "Frozen Data Release",
                "status": str(run_release.get("status")) if run_release else "pending",
                "detail": (
                    f"{run_release.get('item_count', 0)} items · "
                    f"input {run_release.get('input_status', '')}"
                    if run_release
                    else "ещё не создан"
                ),
            },
            {
                "name": "Stories",
                "status": str(story.get("status")) if story else "pending",
                "detail": (
                    f"{story_metrics.get('story_count', 0)} stories · "
                    f"{story_metrics.get('cross_source_story_count', 0)} cross-source"
                    if story
                    else "ожидает Data Release"
                ),
            },
            {
                "name": "Trends / Qwen",
                "status": str(trend.get("status")) if trend else "pending",
                "detail": (
                    f"{trend_metrics.get('trend_count', 0)} candidates · "
                    f"{trend_metrics.get('confirmed_trend_count', 0)} confirmed · "
                    f"history {trend.get('history_status', '')}"
                    if trend
                    else "ожидает Stories"
                ),
            },
            {
                "name": "Quality gate",
                "status": (
                    "passed"
                    if quality and quality.get("passed")
                    else "failed"
                    if quality
                    else "pending"
                ),
                "detail": (
                    "все абсолютные полы пройдены"
                    if quality and quality.get("passed")
                    else (
                        "не пройдены: " + ", ".join(cast(list[str], quality.get("failed", [])))
                        if quality
                        else "результат ещё не записан для этой версии"
                    )
                ),
            },
            {
                "name": "Publication",
                "status": (
                    "published" if publication and publication.get("is_current") else "pending"
                ),
                "detail": (
                    f"{publication.get('channel')} · input {publication.get('input_status')}"
                    if publication
                    else "не опубликован"
                ),
            },
        ]
        runs.append(
            {
                "run_id": run_id,
                "date": date,
                "profile": row["profile"],
                "status": row["status"],
                "status_label": status_label(row["status"]),
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "item_count": item_count,
                "source_ready": source_ready,
                "source_total": source_total,
                "source_health": health,
                "release": run_release,
                "stages": stages,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="runs.html",
        context={"runs": runs},
    )


@router.post("/ui/stories/{story_id}/research-state")
async def update_research_state_endpoint(
    story_id: str,
    saved: bool = Form(False),
    status: str = Form("unread"),
    note: str = Form(""),
    return_to: str = Form("/today"),
    csrf_token: str = Form(""),
    conn: sqlite3.Connection = Depends(_get_db),
) -> Response:
    """Обновление research state для story."""
    if not _validate_csrf_token(csrf_token):
        return Response(status_code=403)

    if not return_to.startswith("/"):
        return_to = "/today"

    from datetime import UTC, datetime

    updated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    update_research_state(
        conn,
        story_id,
        saved=saved,
        status=status,
        note=note[:5000],
        updated_at=updated_at,
    )
    conn.commit()

    return RedirectResponse(url=return_to, status_code=303)


@router.post("/ui/engine/feedback")
async def engine_feedback_endpoint(
    target_kind: str = Form("story"),
    target_id: str = Form(""),
    release_id: str = Form(""),
    value: str = Form("useful"),
    return_to: str = Form("/today"),
    csrf_token: str = Form(""),
) -> Response:
    """Фаза 7: обратная связь в один клик (полезно/мусор) → engine_labels.

    Ежедневное использование пополняет golden set без отдельной разметки.
    """

    if not _validate_csrf_token(csrf_token):
        return Response(status_code=403)
    if not return_to.startswith("/"):
        return_to = "/today"
    if target_kind not in {"story", "trend"} or not target_id or not release_id:
        return RedirectResponse(url=return_to, status_code=303)
    label = "useful" if value == "useful" else "useless"
    engine_path = _engine_path()
    engine_conn = engine_db(engine_path)
    try:
        label_engine_target(
            engine_conn,
            target_kind=target_kind,
            target_id=target_id,
            release_id=release_id,
            label=label,  # type: ignore[arg-type]
            note="ui_feedback",
        )
    finally:
        engine_conn.close()
    return RedirectResponse(url=return_to, status_code=303)


@router.get("/engine", response_class=HTMLResponse)
async def engine_page(request: Request) -> HTMLResponse:
    """Read-only control plane for immutable engine releases."""
    path = _engine_path()
    if not path.exists():
        return templates.TemplateResponse(
            request=request,
            name="engine.html",
            context={
                "available": False,
                "data_releases": [],
                "story_releases": [],
                "trend_releases": [],
                "publications": [],
                "current_publication": None,
            },
        )

    conn = open_engine_readonly(path)
    try:
        story_releases = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM story_releases ORDER BY created_at DESC"
            ).fetchall()
        ]
        trend_releases = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM trend_releases ORDER BY created_at DESC"
            ).fetchall()
        ]
        current = get_current_publication(conn, "broad")
        context = {
            "available": True,
            "data_releases": [asdict(release) for release in list_data_releases(conn)],
            "story_releases": story_releases,
            "trend_releases": trend_releases,
            "publications": [asdict(publication) for publication in list_publications(conn)],
            "current_publication": asdict(current) if current else None,
        }
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="engine.html",
        context=context,
    )


@router.get("/about", response_class=HTMLResponse)
async def about_page(request: Request) -> HTMLResponse:
    """Что это за сервис и что означают его термины.

    Stories, Project Lens, source scope, confidence, preview mode встречались
    только в интерфейсе, где их некому расшифровать. Страница статична: она
    описывает продуктовый контракт, а не состояние данных.
    """
    return templates.TemplateResponse(request=request, name="about.html", context={})


@router.get("/dashboard", include_in_schema=False)
async def dashboard_redirect() -> RedirectResponse:
    """Legacy redirect: /dashboard → /today."""
    return RedirectResponse(url="/today", status_code=302)


@router.get("/explore", include_in_schema=False)
async def explore_redirect(request: Request) -> RedirectResponse:
    """Legacy redirect: /explore → /news.

    Отдельная страница поиска по историям снята вместе с legacy-слоем: она читала
    `compass.db` напрямую, в обход опубликованного релиза. Фильтры живут на /news,
    поэтому строку запроса переносим как есть.
    """
    query = request.url.query
    return RedirectResponse(url=f"/news?{query}" if query else "/news", status_code=301)


@router.get("/radar", response_class=HTMLResponse)
async def radar_redirect(
    channel: str = "broad",
    publication_id: str | None = None,
    conn: sqlite3.Connection = Depends(_get_db),
) -> RedirectResponse:
    """Redirect на последний доступный Radar."""
    from .query_service import resolve_latest_run

    engine_path = _engine_path()
    if engine_path.exists():
        engine_conn = open_engine_readonly(engine_path)
        try:
            publication = (
                get_publication(engine_conn, publication_id)
                if publication_id
                else get_current_publication(engine_conn, channel)
            )
            if publication:
                release = get_data_release(engine_conn, publication.data_release_id)
                if release and release.dates:
                    return RedirectResponse(
                        url=(
                            f"/runs/{max(release.dates)}/radar?"
                            f"{_analysis_query(channel, publication_id)}"
                        ),
                        status_code=302,
                    )
        finally:
            engine_conn.close()

    date = resolve_latest_run(conn)
    if date is None:
        return RedirectResponse(url="/today", status_code=302)
    return RedirectResponse(
        url=f"/runs/{date}/radar?{_analysis_query(channel, publication_id)}",
        status_code=302,
    )


@router.get("/runs/{date}/radar", response_class=HTMLResponse)
async def radar_page(
    request: Request,
    date: str,
    profile: str = DEFAULT_PROFILE,
    mode: str = "broad",
    domain: str | None = None,
    channel: str = "broad",
    publication_id: str | None = None,
    conn: sqlite3.Connection = Depends(_get_db),
) -> HTMLResponse:
    """Полный аналитический Radar."""

    engine_path = _engine_path()
    if engine_path.exists():
        from .v2 import _engine_radar

        engine_conn = open_engine_readonly(engine_path)
        pulse_summary = None
        try:
            published_radar = _engine_radar(
                engine_conn,
                date=date,
                profile=profile,
                mode=mode,
                domain=domain,
                channel=channel,
                publication_id=publication_id,
            )
            if published_radar is not None:
                from .v2 import _engine_pulse_signals, _latest_signal_release, _safe_url

                sig_id = _latest_signal_release(
                    engine_conn,
                    data_release_id=published_radar.data_release_id,
                    date=date,
                )
                if sig_id:
                    top_pulse, _ = _engine_pulse_signals(engine_conn, sig_id, limit=8)
                    top_pain, _ = _engine_pulse_signals(
                        engine_conn,
                        sig_id,
                        signal_type="pain_point",
                        limit=5,
                    )
                    top_ai, _ = _engine_pulse_signals(
                        engine_conn,
                        sig_id,
                        signal_type="ai_capability",
                        limit=5,
                    )
                    # Mainstream gap: high pulse, low coverage
                    gap_rows = engine_conn.execute(
                        "SELECT signal_id, item_id, subreddit, "
                        "signal_type, title, discussion_url, "
                        "target_url, pulse_score, subreddit_percentile, "
                        "comment_velocity, discussion_depth, "
                        "cross_subreddit_repetition, novelty, "
                        "domain_ids_json, theme_ids_json, "
                        "pain_points_json "
                        "FROM community_signals "
                        "WHERE signal_release_id = ? "
                        "AND pulse_score >= 60 "
                        "AND mainstream_coverage_count < 2 "
                        "ORDER BY pulse_score DESC LIMIT 5",
                        (sig_id,),
                    ).fetchall()
                    mainstream_gap = [
                        {
                            "signal_id": r["signal_id"],
                            "item_id": r["item_id"],
                            "subreddit": r["subreddit"],
                            "signal_type": r["signal_type"],
                            "title": r["title"],
                            "discussion_url": _safe_url(r["discussion_url"]),
                            "pulse_score": r["pulse_score"],
                        }
                        for r in gap_rows
                    ]
                    pulse_summary = {
                        "signal_release_id": sig_id,
                        "top_pulse": top_pulse,
                        "top_pain": top_pain,
                        "top_ai": top_ai,
                        "mainstream_gap": mainstream_gap,
                    }
        finally:
            engine_conn.close()
        if published_radar is not None:
            return templates.TemplateResponse(
                request=request,
                name="engine_radar.html",
                context={
                    "radar": published_radar.model_dump(),
                    "analysis_query": _analysis_query(channel, publication_id),
                    "pulse": pulse_summary,
                },
            )

    # Публикации для этой даты нет — заглушка вместо legacy-радара из compass.db.
    return templates.TemplateResponse(
        request=request,
        name="components/empty_state.html",
        context={
            "message": "Опубликованного радара нет. Запустите `reddit-compass engine cycle`.",
        },
    )
