"""Verified Story layer: provenance-based story confidence filter.

A Story is verified if at least one condition holds:
- exact_event_url_match (shared canonical/target URL from independent providers)
- near_duplicate_title_fingerprint
- Qwen-confirmed same_story
- manual label same_story
- single-source but high Reddit Pulse and explicitly marked community_only

E5 semantic similarity alone is NOT enough for verification.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Literal

VerificationReason = Literal[
    "cross_source_url",
    "near_duplicate_title",
    "qwen_confirmed",
    "manual_label",
    "community_only_high_pulse",
    "exact_title_match",
]

# Membership reasons that constitute provenance-based verification
_VERIFIED_REASONS: dict[str, VerificationReason] = {
    "shared canonical/target URL": "cross_source_url",
    "shared HuggingFace model release URL": "cross_source_url",
    "near-duplicate title fingerprint": "near_duplicate_title",
    "validated by cached Qwen story review": "qwen_confirmed",
    "exact event title match without hard conflict": "exact_title_match",
}

# Generic anchors that do NOT count as entity anchors for verification
GENERIC_ANCHORS: frozenset[str] = frozenset(
    {
        "ai",
        "agent",
        "agents",
        "open source",
        "startup",
        "tool",
        "model",
        "llm",
        "company",
        "app",
        "platform",
    }
)


@dataclass(frozen=True)
class VerifiedStory:
    """A story that passed provenance-based verification."""

    story_id: str
    title: str
    verification_reasons: list[VerificationReason]
    source_count: int
    item_count: int
    confidence: str
    is_cross_source: bool
    providers: list[str] = field(default_factory=list)


def is_generic_anchor(token: str) -> bool:
    """Check if a token/phrase is a generic anchor that cannot verify a story."""
    return token.lower().strip() in GENERIC_ANCHORS


def get_verified_stories(
    conn: sqlite3.Connection,
    story_release_id: str,
    signal_release_id: str | None = None,
    pulse_threshold: float = 60.0,
) -> list[VerifiedStory]:
    """Return stories that pass provenance-based verification.

    A story is verified if it has at least one membership with a
    provenance-based reason (URL match, near-dup, Qwen-confirmed,
    manual label), OR if it is a single-source story with high
    Reddit Pulse marked as community_only.
    """
    # Load all stories
    story_rows = conn.execute(
        """SELECT story_id, title, source_count, item_count, confidence
           FROM engine_stories
           WHERE story_release_id = ?""",
        (story_release_id,),
    ).fetchall()

    # Load membership reasons per story
    membership_rows = conn.execute(
        """SELECT story_id, item_id, membership_reason
           FROM engine_story_items
           WHERE story_release_id = ?""",
        (story_release_id,),
    ).fetchall()

    # Load providers per story
    provider_rows = conn.execute(
        """SELECT esi.story_id, ri.provider
           FROM engine_story_items esi
           JOIN release_items ri
             ON ri.release_id = (
               SELECT fr.data_release_id FROM facet_releases fr
               JOIN story_releases sr ON sr.facet_release_id = fr.facet_release_id
               WHERE sr.story_release_id = ?
             ) AND ri.item_id = esi.item_id
           WHERE esi.story_release_id = ?""",
        (story_release_id, story_release_id),
    ).fetchall()

    # Load manual labels
    label_rows = conn.execute(
        """SELECT target_id, label
           FROM engine_labels
           WHERE release_id = ? AND target_kind = 'story'
             AND label = 'same_story'""",
        (story_release_id,),
    ).fetchall()
    manual_labels: set[str] = {str(r["target_id"]) for r in label_rows}

    # Build per-story data
    story_reasons: dict[str, set[VerificationReason]] = {}
    story_providers: dict[str, set[str]] = {}

    for row in membership_rows:
        sid = str(row["story_id"])
        reason = str(row["membership_reason"] or "")
        if reason in _VERIFIED_REASONS:
            story_reasons.setdefault(sid, set()).add(_VERIFIED_REASONS[reason])

    for row in provider_rows:
        sid = str(row["story_id"])
        story_providers.setdefault(sid, set()).add(str(row["provider"]))

    # Add manual labels
    for sid in manual_labels:
        story_reasons.setdefault(sid, set()).add("manual_label")

    # Check community_only high pulse if signal_release_id provided
    high_pulse_items: set[str] = set()
    if signal_release_id:
        pulse_rows = conn.execute(
            """SELECT item_id FROM community_signals
               WHERE signal_release_id = ? AND pulse_score >= ?""",
            (signal_release_id, pulse_threshold),
        ).fetchall()
        high_pulse_items = {str(r["item_id"]) for r in pulse_rows}

    # Build verified stories
    verified: list[VerifiedStory] = []
    for row in story_rows:
        sid = str(row["story_id"])
        reasons = story_reasons.get(sid, set())
        providers = story_providers.get(sid, set())
        source_count = int(row["source_count"])
        item_count = int(row["item_count"])
        is_cross_source = len(providers) > 1

        # Check community_only: single-source reddit story with high pulse
        if not reasons and source_count == 1 and providers == {"reddit"}:
            # Check if any item in this story has high pulse
            story_items = [str(r["item_id"]) for r in membership_rows if str(r["story_id"]) == sid]
            if any(iid in high_pulse_items for iid in story_items):
                reasons.add("community_only_high_pulse")

        if reasons:
            verified.append(
                VerifiedStory(
                    story_id=sid,
                    title=str(row["title"]),
                    verification_reasons=sorted(reasons),
                    source_count=source_count,
                    item_count=item_count,
                    confidence=str(row["confidence"]),
                    is_cross_source=is_cross_source,
                    providers=sorted(providers),
                )
            )

    return verified


def get_verified_story_ids(
    conn: sqlite3.Connection,
    story_release_id: str,
    signal_release_id: str | None = None,
    pulse_threshold: float = 60.0,
) -> set[str]:
    """Return set of verified story IDs."""
    return {
        s.story_id
        for s in get_verified_stories(conn, story_release_id, signal_release_id, pulse_threshold)
    }


@dataclass(frozen=True)
class GroupSizeWarning:
    """A story that exceeds group size limits without proper provenance."""

    story_id: str
    title: str
    item_count: int
    provider_count: int
    warning: str


def check_group_size_guards(
    conn: sqlite3.Connection,
    story_release_id: str,
    same_provider_max: int = 8,
    cross_source_max: int = 15,
) -> list[GroupSizeWarning]:
    """Flag stories that exceed group size limits without proper provenance.

    Rules:
    - same-provider-only story with > same_provider_max items requires
      exact URL/near-duplicate provenance
    - cross-source story with > cross_source_max items requires
      Qwen group review or manual label
    """
    warnings: list[GroupSizeWarning] = []

    story_rows = conn.execute(
        """SELECT s.story_id, s.title, s.item_count, s.source_count
           FROM engine_stories s
           WHERE s.story_release_id = ?""",
        (story_release_id,),
    ).fetchall()

    for row in story_rows:
        sid = str(row["story_id"])
        item_count = int(row["item_count"])
        title = str(row["title"])

        # Get providers for this story
        provider_rows = conn.execute(
            """SELECT DISTINCT ri.provider
               FROM engine_story_items esi
               JOIN release_items ri
                 ON ri.release_id = (
                   SELECT fr.data_release_id FROM facet_releases fr
                   JOIN story_releases sr ON sr.facet_release_id = fr.facet_release_id
                   WHERE sr.story_release_id = ?
                 ) AND ri.item_id = esi.item_id
               WHERE esi.story_release_id = ? AND esi.story_id = ?""",
            (story_release_id, story_release_id, sid),
        ).fetchall()
        providers = {str(r["provider"]) for r in provider_rows}
        is_cross_source = len(providers) > 1

        if not is_cross_source and item_count > same_provider_max:
            # Check if all items have exact URL or near-dup provenance
            membership_rows = conn.execute(
                """SELECT membership_reason FROM engine_story_items
                   WHERE story_release_id = ? AND story_id = ?
                     AND membership_reason NOT IN ('story medoid', '')""",
                (story_release_id, sid),
            ).fetchall()
            provenance_reasons = {str(r["membership_reason"]) for r in membership_rows}
            has_strong_provenance = bool(
                provenance_reasons
                & {
                    "shared canonical/target URL",
                    "near-duplicate title fingerprint",
                    "exact event title match without hard conflict",
                }
            )
            if not has_strong_provenance:
                warnings.append(
                    GroupSizeWarning(
                        story_id=sid,
                        title=title,
                        item_count=item_count,
                        provider_count=len(providers),
                        warning=(
                            f"same-provider story with {item_count} items "
                            f"(>{same_provider_max}) lacks URL/near-dup provenance"
                        ),
                    )
                )

        elif is_cross_source and item_count > cross_source_max:
            # Check for Qwen review or manual label
            label_rows = conn.execute(
                """SELECT 1 FROM engine_labels
                   WHERE release_id = ? AND target_kind = 'story'
                     AND target_id = ? AND label = 'same_story'""",
                (story_release_id, sid),
            ).fetchone()
            qwen_rows = conn.execute(
                """SELECT 1 FROM engine_story_items
                   WHERE story_release_id = ? AND story_id = ?
                     AND membership_reason = 'validated by cached Qwen story review'""",
                (story_release_id, sid),
            ).fetchone()
            if not label_rows and not qwen_rows:
                warnings.append(
                    GroupSizeWarning(
                        story_id=sid,
                        title=title,
                        item_count=item_count,
                        provider_count=len(providers),
                        warning=(
                            f"cross-source story with {item_count} items "
                            f"(>{cross_source_max}) lacks Qwen review or manual label"
                        ),
                    )
                )

    return warnings
