"""Strict evidence validation for Story/Trend Engine LLM reviews."""

import json

from reddit_compass.intelligence.engine_reviews import (
    validate_story_review,
    validate_trend_review,
)


def test_story_review_rejects_unknown_evidence() -> None:
    raw = json.dumps(
        {
            "decision": "same_story",
            "event_frame": {
                "actors": ["Acme"],
                "action": "announced",
                "object": "layoffs",
                "geography": ["US"],
                "event_date": "2026-07-29",
            },
            "evidence_item_ids": ["a", "unknown"],
            "conflicts": [],
            "confidence": 0.95,
            "reason": "Same concrete announcement",
        }
    )

    review, errors = validate_story_review(raw, allowed_item_ids={"a", "b"})

    assert review is None
    assert "unknown evidence_item_ids" in errors[0]


def test_story_review_rejects_out_of_range_confidence() -> None:
    raw = json.dumps(
        {
            "decision": "different_story",
            "event_frame": {},
            "evidence_item_ids": ["a", "b"],
            "conflicts": ["different dates"],
            "confidence": 1.2,
            "reason": "Different events",
        }
    )

    review, errors = validate_story_review(raw, allowed_item_ids={"a", "b"})

    assert review is None
    assert errors


def test_trend_review_requires_three_evidence_stories() -> None:
    raw = json.dumps(
        {
            "decision": "coherent_trend",
            "trend_name_ru": "Рост локальных моделей",
            "pattern": "Three distinct launches",
            "story_ids": ["s1", "s2", "s3"],
            "evidence_story_ids": ["s1", "s2"],
            "counterpoints": [],
            "domains": ["ai_technology"],
            "confidence": 0.9,
        }
    )

    review, errors = validate_trend_review(
        raw,
        allowed_story_ids={"s1", "s2", "s3"},
    )

    assert review is None
    assert errors == ["coherent_trend requires at least three evidence stories"]


def test_valid_fenced_story_review_is_accepted() -> None:
    raw = """```json
    {
      "decision": "same_story",
      "event_frame": {"actors": [], "action": "", "object": "", "geography": [], "event_date": ""},
      "evidence_item_ids": ["a", "b"],
      "conflicts": [],
      "confidence": 0.91,
      "reason": "Same event"
    }
    ```"""

    review, errors = validate_story_review(raw, allowed_item_ids={"a", "b"})

    assert errors == []
    assert review is not None
    assert review.decision == "same_story"
