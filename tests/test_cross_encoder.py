"""Политика разбора серой зоны cross-encoder'ом.

Модель не загружается: важна не она, а инварианты вокруг неё — детерминированные
запреты не должны переопределяться никаким скором.
"""

from __future__ import annotations

from typing import Any

from reddit_compass.intelligence.cross_encoder import (
    adjudicate_story_pairs,
    is_adjudicable,
    pair_text,
)
from reddit_compass.intelligence.engine import FrozenItem, PairCandidate


class _FixedScorer:
    """Возвращает заданный логит для каждой пары, считая обращения."""

    def __init__(self, logit: float) -> None:
        self.logit = logit
        self.calls = 0

    def predict(self, pairs: list[tuple[str, str]], **_: Any) -> list[float]:
        self.calls += 1
        return [self.logit] * len(pairs)


def _item(item_id: str, title: str) -> FrozenItem:
    return FrozenItem(
        item_id=item_id,
        provider="reuters",
        source_cluster="business",
        canonical_url=f"https://example.com/{item_id}",
        target_url="",
        discussion_url="",
        title=title,
        excerpt="",
        published_at="2026-08-01T00:00:00Z",
        snapshot_date="2026-08-01",
        content_scope="headline",
        source_section="business",
        domain_ids=["business_markets"],
        raw_engagement={},
        metadata={},
    )


def _pair(decision: str, **features: Any) -> PairCandidate:
    return PairCandidate(
        item_id_a="a",
        item_id_b="b",
        score=0.5,
        decision=decision,
        reason="test",
        features=features,
    )


def _items() -> list[FrozenItem]:
    return [_item("a", "Regulator opens probe"), _item("b", "Regulator probe opened")]


def test_only_conflict_free_review_pairs_are_adjudicable() -> None:
    assert is_adjudicable(_pair("review")) is True
    assert is_adjudicable(_pair("auto_merge")) is False
    assert is_adjudicable(_pair("reject")) is False
    for conflict in ("number_conflict", "location_conflict", "person_conflict"):
        assert is_adjudicable(_pair("review", **{conflict: True})) is False


def test_high_score_promotes_a_review_pair() -> None:
    scorer = _FixedScorer(10.0)  # sigmoid ≈ 1.0

    resolved = adjudicate_story_pairs([_pair("review")], _items(), threshold=0.95, scorer=scorer)

    assert resolved[0].decision == "auto_merge"
    assert resolved[0].features["cross_encoder_score"] > 0.95
    assert scorer.calls == 2, "пара обязана оцениваться в обе стороны"


def test_low_score_closes_a_review_pair() -> None:
    resolved = adjudicate_story_pairs(
        [_pair("review")], _items(), threshold=0.95, scorer=_FixedScorer(-10.0)
    )

    assert resolved[0].decision == "reject"
    assert resolved[0].features["cross_encoder_score"] < 0.05


def test_deterministic_decisions_are_never_overridden() -> None:
    """Даже при максимальном скоре reject и hard conflicts остаются запретом."""
    candidates = [
        PairCandidate("a", "b", 1.0, "auto_merge", "shared canonical/target URL", {}),
        PairCandidate("a", "b", 0.1, "reject", "hard conflict", {}),
        PairCandidate("a", "b", 0.5, "review", "grey", {"number_conflict": True}),
    ]

    resolved = adjudicate_story_pairs(
        candidates, _items(), threshold=0.95, scorer=_FixedScorer(10.0)
    )

    assert [c.decision for c in resolved] == ["auto_merge", "reject", "review"]
    assert all("cross_encoder_score" not in c.features for c in resolved)


def test_no_adjudicable_pairs_skips_the_model_entirely() -> None:
    scorer = _FixedScorer(10.0)

    resolved = adjudicate_story_pairs([_pair("reject")], _items(), scorer=scorer)

    assert scorer.calls == 0
    assert resolved[0].decision == "reject"


def test_pair_text_bounds_the_excerpt() -> None:
    text = pair_text("Title", "word " * 1000)

    assert text.startswith("Headline: Title")
    assert len(text) < 1_800
