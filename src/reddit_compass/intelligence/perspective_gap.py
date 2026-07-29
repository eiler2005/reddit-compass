"""Perspective gap: what people discuss but media misses, and vice versa.

mainstream_gap = high Reddit Pulse + low mainstream coverage
elite_media_gap = high mainstream coverage + low Reddit discussion
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class PerspectiveGapItem:
    """An item showing a perspective gap between Reddit and mainstream."""

    item_id: str
    title: str
    subreddit: str
    pulse_score: float
    mainstream_coverage_count: int
    gap_type: str  # "mainstream_gap" or "elite_media_gap"
    gap_score: float


def compute_perspective_gaps(
    conn: sqlite3.Connection,
    signal_release_id: str,
    story_release_id: str,
    *,
    pulse_threshold: float = 60.0,
    mainstream_threshold: int = 2,
    limit: int = 50,
) -> list[PerspectiveGapItem]:
    """Compute perspective gaps between Reddit Pulse and mainstream coverage.

    mainstream_gap: high Reddit Pulse signal with low/no mainstream coverage.
    elite_media_gap: high mainstream story count with low Reddit discussion.
    """
    gaps: list[PerspectiveGapItem] = []

    # Mainstream gap: high pulse, low mainstream coverage
    pulse_rows = conn.execute(
        """SELECT cs.item_id, cs.title, cs.subreddit, cs.pulse_score,
                  cs.mainstream_coverage_count
           FROM community_signals cs
           WHERE cs.signal_release_id = ?
             AND cs.pulse_score >= ?
             AND cs.mainstream_coverage_count < ?
           ORDER BY cs.pulse_score DESC
           LIMIT ?""",
        (signal_release_id, pulse_threshold, mainstream_threshold, limit),
    ).fetchall()

    for row in pulse_rows:
        pulse = float(row["pulse_score"])
        coverage = int(row["mainstream_coverage_count"])
        gap_score = pulse * (1.0 / max(coverage + 1, 1))
        gaps.append(
            PerspectiveGapItem(
                item_id=str(row["item_id"]),
                title=str(row["title"]),
                subreddit=str(row["subreddit"]),
                pulse_score=pulse,
                mainstream_coverage_count=coverage,
                gap_type="mainstream_gap",
                gap_score=round(gap_score, 2),
            )
        )

    # Elite media gap: high mainstream story count, low Reddit pulse
    # Find stories with high source_count but no corresponding high-pulse signal
    story_rows = conn.execute(
        """SELECT s.story_id, s.title, s.source_count, s.item_count
           FROM engine_stories s
           WHERE s.story_release_id = ?
             AND s.source_count >= 3
           ORDER BY s.source_count DESC
           LIMIT ?""",
        (story_release_id, limit),
    ).fetchall()

    for row in story_rows:
        story_id = str(row["story_id"])
        source_count = int(row["source_count"])
        # Check if any item in this story has high Reddit pulse
        max_pulse_row = conn.execute(
            """SELECT MAX(cs.pulse_score) as max_pulse
               FROM community_signals cs
               JOIN engine_story_items esi
                 ON esi.item_id = cs.item_id
               WHERE esi.story_release_id = ?
                 AND esi.story_id = ?
                 AND cs.signal_release_id = ?""",
            (story_release_id, story_id, signal_release_id),
        ).fetchone()
        max_pulse = float(max_pulse_row["max_pulse"] or 0) if max_pulse_row else 0.0
        if max_pulse < pulse_threshold:
            gap_score = source_count * (1.0 / max(max_pulse / 100.0 + 0.1, 0.1))
            gaps.append(
                PerspectiveGapItem(
                    item_id=story_id,
                    title=str(row["title"]),
                    subreddit="",
                    pulse_score=max_pulse,
                    mainstream_coverage_count=source_count,
                    gap_type="elite_media_gap",
                    gap_score=round(gap_score, 2),
                )
            )

    # Sort by gap_score descending
    gaps.sort(key=lambda g: g.gap_score, reverse=True)
    return gaps[:limit]
