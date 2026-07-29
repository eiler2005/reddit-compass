"""Typed, optional entity/event-frame extraction for news story matching."""

from __future__ import annotations

import importlib
import re
from typing import Any

_UNINITIALIZED = object()
_SPACY_PIPELINE: Any = _UNINITIALIZED

_GENERIC_ANCHORS = {
    "ai",
    "ceo",
    "cfo",
    "uk",
    "us",
    "u.s.",
    "usa",
}
_ALIASES = {
    "open ai": "openai",
    "openai inc": "openai",
    "alphabet inc": "alphabet",
    "google llc": "google",
    "united states": "united states",
    "u.s.": "united states",
    "us": "united states",
}
_NUMBER_PATTERN = re.compile(
    r"(?:[$€£]\s?)?\d[\d,.]*(?:\s?(?:%|percent|million|billion|trillion|m|bn|tn))?",
    re.IGNORECASE,
)
_ACTION_HINTS = (
    ("layoff", ("lay off", "lays off", "laid off", "cuts", "cut", "slashes", "fires")),
    ("ban", ("bans", "ban", "blocks", "blocked", "prohibits")),
    ("sue", ("sues", "sue", "lawsuit", "legal action")),
    ("investigate", ("investigates", "probe", "probes", "investigation")),
    ("launch", ("launches", "launch", "unveils", "debuts", "releases")),
    ("raise", ("raises", "raise", "hikes", "increase", "increases")),
    ("fall", ("falls", "slumps", "drops", "declines", "plunges")),
    ("rise", ("rises", "jumps", "surges", "gains", "soars")),
    ("approve", ("approves", "approved", "greenlights", "allows")),
    ("reject", ("rejects", "rejected", "halts", "pauses", "scraps")),
    ("charge", ("charges", "charged", "indicts", "prosecutes")),
    ("acquire", ("acquires", "buys", "merges")),
    ("warn", ("warns", "warning", "warned")),
)
_GEO_ALIASES = {
    "america": "united states",
    "american": "united states",
    "britain": "united kingdom",
    "british": "united kingdom",
    "california": "california",
    "china": "china",
    "chinese": "china",
    "europe": "europe",
    "european": "europe",
    "france": "france",
    "germany": "germany",
    "iran": "iran",
    "israel": "israel",
    "japan": "japan",
    "russia": "russia",
    "russian": "russia",
    "saudi": "saudi arabia",
    "u.s.": "united states",
    "us": "united states",
    "ukraine": "ukraine",
}


def extract_structured_event_frame(
    *,
    title: str,
    excerpt: str,
    event_date: str,
    fallback_entities: list[str],
    use_spacy: bool = True,
) -> tuple[dict[str, Any], list[str], str]:
    """Return typed event anchors; fall back deterministically when spaCy is unavailable."""
    text = f"{title}. {excerpt[:800]}".strip()
    pipeline = _get_spacy_pipeline() if use_spacy else None
    if pipeline is None:
        entities = _normalize_entities(fallback_entities)
        action = _extract_action_hint(text)
        geography_hints = _extract_geography_hints(text)
        return (
            {
                "actors": entities[:8],
                "people": [],
                "organizations": entities[:8],
                "action": action,
                "object": _extract_object_hint(title, action),
                "geography": geography_hints,
                "event_date": event_date,
                "dates": [event_date] if event_date else [],
                "numbers": _extract_numbers(title),
            },
            entities,
            "deterministic_fallback",
        )

    document: Any = pipeline(text)
    people: list[str] = []
    organizations: list[str] = []
    geography: list[str] = []
    dates: list[str] = []
    numbers: list[str] = []
    for entity in document.ents:
        normalized = _normalize_entity(str(entity.text))
        if not normalized:
            continue
        label = str(entity.label_)
        if label == "PERSON":
            people.append(normalized)
        elif label in {"ORG", "PRODUCT", "EVENT"}:
            organizations.append(normalized)
        elif label in {"GPE", "LOC", "FAC"}:
            geography.append(normalized)
        elif label in {"DATE", "TIME"}:
            dates.append(normalized)
        elif label in {"MONEY", "PERCENT", "QUANTITY", "CARDINAL", "ORDINAL"}:
            numbers.append(normalized)

    action = ""
    object_text = ""
    for token in document:
        if not action and str(token.pos_) == "VERB":
            action = str(token.lemma_).lower()
        if not object_text and str(token.dep_) in {"dobj", "obj", "attr"}:
            object_text = " ".join(str(part.text) for part in token.subtree)[:240]
        if action and object_text:
            break

    actors = _normalize_entities(people + organizations + fallback_entities)
    return (
        {
            "actors": actors[:12],
            "people": sorted(set(people)),
            "organizations": sorted(set(organizations)),
            "action": action,
            "object": object_text,
            "geography": sorted(set(geography)),
            "event_date": event_date,
            "dates": sorted(set(dates + ([event_date] if event_date else []))),
            "numbers": sorted(set(numbers + _extract_numbers(title)))[:12],
        },
        actors,
        "spacy_en_core_web_sm",
    )


def _get_spacy_pipeline() -> Any | None:
    global _SPACY_PIPELINE
    if _SPACY_PIPELINE is not _UNINITIALIZED:
        return _SPACY_PIPELINE
    try:
        spacy = importlib.import_module("spacy")
        _SPACY_PIPELINE = spacy.load("en_core_web_sm")
    except (ImportError, OSError):
        _SPACY_PIPELINE = None
    return _SPACY_PIPELINE


def _normalize_entities(entities: list[str]) -> list[str]:
    return sorted({normalized for entity in entities if (normalized := _normalize_entity(entity))})


def _normalize_entity(entity: str) -> str:
    normalized = " ".join(entity.lower().strip(" \t\r\n.,:;!?()[]{}'\"").split())
    normalized = _ALIASES.get(normalized, normalized)
    if normalized in _GENERIC_ANCHORS or len(normalized) < 2:
        return ""
    return normalized


def _extract_numbers(text: str) -> list[str]:
    return sorted(
        {
            " ".join(match.group(0).lower().split())
            for match in _NUMBER_PATTERN.finditer(text)
            if match.group(0).strip()
        }
    )[:12]


def _extract_action_hint(text: str) -> str:
    normalized = f" {text.lower()} "
    for action, phrases in _ACTION_HINTS:
        if any(f" {phrase} " in normalized for phrase in phrases):
            return action
    return ""


def _extract_geography_hints(text: str) -> list[str]:
    normalized = text.lower()
    return sorted(
        {
            canonical
            for hint, canonical in _GEO_ALIASES.items()
            if re.search(rf"\b{re.escape(hint)}\b", normalized)
        }
    )[:8]


def _extract_object_hint(title: str, action: str) -> str:
    if not action:
        return ""
    normalized = " ".join(title.split())
    return normalized[:240]
