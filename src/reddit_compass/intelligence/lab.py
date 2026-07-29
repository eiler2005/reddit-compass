"""Cluster Lab: безопасные эксперименты со stories/trends поверх production DB.

Production SQLite остаётся read-only источником фактов. Все releases, experiments
и proposals пишутся в отдельную lab DB.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import DEFAULT_DATA_DIR, DEFAULT_PROFILE, PROJECT_ROOT
from .clustering import extract_entities, extract_tokens, is_low_signal_title, normalize_title

DEFAULT_LAB_DB_PATH = DEFAULT_DATA_DIR / "cluster_lab.db"
DEFAULT_RELEASES_DIR = DEFAULT_DATA_DIR / "releases"
LAB_SCHEMA_VERSION = 1
_TREND_STOP_TOKENS = {
    "after",
    "against",
    "amid",
    "another",
    "available",
    "best",
    "could",
    "during",
    "first",
    "from",
    "into",
    "latest",
    "like",
    "make",
    "makes",
    "more",
    "most",
    "new",
    "over",
    "says",
    "should",
    "shows",
    "story",
    "these",
    "this",
    "through",
    "under",
    "using",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
    "year",
    "years",
}
_GENERIC_ENTITY_TREND_TOKENS = {
    "ai",
    "ceo",
    "cfo",
    "gop",
    "uk",
    "u.s",
    "u.s.",
    "us",
    "usa",
}

_LAB_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_releases (
    release_id          TEXT PRIMARY KEY,
    profile             TEXT NOT NULL,
    dates_json          TEXT NOT NULL,
    run_ids_json        TEXT NOT NULL,
    source_db_path      TEXT NOT NULL,
    source_db_checksum  TEXT NOT NULL DEFAULT '',
    git_sha             TEXT NOT NULL DEFAULT '',
    item_count          INTEGER NOT NULL DEFAULT 0,
    manifest_json       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    immutable           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS data_release_items (
    release_id  TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    observed_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (release_id, item_id, run_id)
);

CREATE TABLE IF NOT EXISTS cluster_experiments (
    experiment_id  TEXT PRIMARY KEY,
    release_id     TEXT NOT NULL,
    method         TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT '',
    params_json    TEXT NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'created',
    metrics_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    git_sha        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cluster_candidate_pairs (
    experiment_id TEXT NOT NULL,
    item_id_a     TEXT NOT NULL,
    item_id_b     TEXT NOT NULL,
    score         REAL NOT NULL,
    signals_json  TEXT NOT NULL DEFAULT '{}',
    reason        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (experiment_id, item_id_a, item_id_b)
);

CREATE TABLE IF NOT EXISTS cluster_candidate_groups (
    group_id      TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    item_ids_json TEXT NOT NULL,
    score         REAL NOT NULL,
    reason        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS llm_cluster_reviews (
    review_id     TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    group_id      TEXT NOT NULL,
    model         TEXT NOT NULL,
    decision      TEXT NOT NULL,
    response_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_proposals (
    proposal_id       TEXT PRIMARY KEY,
    experiment_id     TEXT NOT NULL,
    proposed_story_id TEXT NOT NULL,
    title             TEXT NOT NULL,
    item_ids_json     TEXT NOT NULL,
    confidence        REAL NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'proposed',
    reason            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS trend_proposals (
    proposal_id       TEXT PRIMARY KEY,
    experiment_id     TEXT NOT NULL,
    trend_name        TEXT NOT NULL,
    story_ids_json    TEXT NOT NULL DEFAULT '[]',
    item_ids_json     TEXT NOT NULL,
    confidence        REAL NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'proposed',
    reason            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cluster_eval_cases (
    case_id      TEXT PRIMARY KEY,
    release_id   TEXT NOT NULL,
    item_ids_json TEXT NOT NULL,
    label_json   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_eval_results (
    result_id     TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    case_id       TEXT NOT NULL,
    result_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promotion_history (
    promotion_id  TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'planned',
    metrics_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rollback_points (
    rollback_id                     TEXT PRIMARY KEY,
    promotion_id                    TEXT NOT NULL,
    run_id                          TEXT NOT NULL,
    previous_stories_checksum       TEXT NOT NULL DEFAULT '',
    previous_story_metrics_checksum TEXT NOT NULL DEFAULT '',
    backup_table_names_json         TEXT NOT NULL DEFAULT '[]',
    created_at                      TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class LabRelease:
    release_id: str
    profile: str
    dates: list[str]
    run_ids: list[str]
    source_db_path: str
    item_count: int
    created_at: str


@dataclass(frozen=True)
class LabExperiment:
    experiment_id: str
    release_id: str
    method: str
    prompt_version: str
    status: str
    metrics: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class LabItem:
    item_id: str
    provider: str
    source_cluster: str
    canonical_url: str
    target_url: str
    discussion_url: str
    title: str
    domain_ids: list[str]
    signal_domain_ids: list[str]
    candidate_themes: list[str]
    pain_points: list[str]
    goal_relevance: dict[str, int]
    current_story_id: str = ""


@dataclass(frozen=True)
class CandidatePair:
    item_id_a: str
    item_id_b: str
    score: float
    reason: str


@dataclass(frozen=True)
class ProposalStats:
    release_items: int
    selected_items: int
    candidate_pairs: int
    candidate_groups: int
    story_proposals: int
    cross_source_story_proposals: int
    trend_proposals: int


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def lab_db(path: Path = DEFAULT_LAB_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    migrate_lab(conn)
    return conn


def migrate_lab(conn: sqlite3.Connection) -> None:
    conn.executescript(_LAB_SCHEMA)
    conn.execute(f"PRAGMA user_version = {LAB_SCHEMA_VERSION}")
    conn.commit()


def create_release(
    source_conn: sqlite3.Connection,
    lab_conn: sqlite3.Connection,
    *,
    source_db_path: Path,
    releases_dir: Path = DEFAULT_RELEASES_DIR,
    profile: str = DEFAULT_PROFILE,
    dates: list[str],
) -> LabRelease:
    if not dates:
        raise ValueError("At least one date is required")
    source_conn.row_factory = sqlite3.Row
    release_id = _next_release_id(lab_conn, profile, dates)
    placeholders = ",".join("?" for _ in dates)
    run_rows = source_conn.execute(
        f"""SELECT run_id, snapshot_date
            FROM runs
            WHERE profile = ? AND snapshot_date IN ({placeholders})
            ORDER BY snapshot_date""",
        (profile, *dates),
    ).fetchall()
    run_ids = [str(row["run_id"]) for row in run_rows]
    if not run_ids:
        raise ValueError(f"No runs found for profile={profile}, dates={dates}")

    run_placeholders = ",".join("?" for _ in run_ids)
    item_rows = source_conn.execute(
        f"""SELECT DISTINCT item_id, run_id, observed_at
            FROM observations
            WHERE run_id IN ({run_placeholders})
            ORDER BY item_id""",
        (*run_ids,),
    ).fetchall()
    created_at = now_iso()
    checksum = sha256_file(source_db_path)
    git_sha = current_git_sha()
    item_count = len({str(row["item_id"]) for row in item_rows})
    manifest = {
        "release_id": release_id,
        "profile": profile,
        "dates": dates,
        "run_ids": run_ids,
        "source_db_path": str(source_db_path),
        "source_db_checksum": checksum,
        "git_sha": git_sha,
        "item_count": item_count,
        "created_at": created_at,
        "immutable": True,
    }
    lab_conn.execute(
        """INSERT INTO data_releases
           (release_id, profile, dates_json, run_ids_json, source_db_path,
            source_db_checksum, git_sha, item_count, manifest_json, created_at, immutable)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (
            release_id,
            profile,
            json.dumps(dates, ensure_ascii=False),
            json.dumps(run_ids, ensure_ascii=False),
            str(source_db_path),
            checksum,
            git_sha,
            item_count,
            json.dumps(manifest, ensure_ascii=False, sort_keys=True),
            created_at,
        ),
    )
    lab_conn.executemany(
        """INSERT INTO data_release_items
           (release_id, item_id, run_id, observed_at)
           VALUES (?, ?, ?, ?)""",
        [
            (release_id, str(row["item_id"]), str(row["run_id"]), str(row["observed_at"] or ""))
            for row in item_rows
        ],
    )
    lab_conn.commit()
    release_dir = releases_dir / release_id
    release_dir.mkdir(parents=True, exist_ok=False)
    (release_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (release_dir / "refs.json").write_text(
        json.dumps(
            [{"item_id": str(row["item_id"]), "run_id": str(row["run_id"])} for row in item_rows],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return LabRelease(
        release_id=release_id,
        profile=profile,
        dates=dates,
        run_ids=run_ids,
        source_db_path=str(source_db_path),
        item_count=item_count,
        created_at=created_at,
    )


def list_releases(lab_conn: sqlite3.Connection) -> list[LabRelease]:
    rows = lab_conn.execute(
        """SELECT release_id, profile, dates_json, run_ids_json, source_db_path,
                  item_count, created_at
           FROM data_releases
           ORDER BY created_at DESC"""
    ).fetchall()
    return [
        LabRelease(
            release_id=str(row["release_id"]),
            profile=str(row["profile"]),
            dates=_json_list(row["dates_json"]),
            run_ids=_json_list(row["run_ids_json"]),
            source_db_path=str(row["source_db_path"]),
            item_count=int(row["item_count"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def create_experiment(
    lab_conn: sqlite3.Connection,
    *,
    release_id: str,
    method: str = "hybrid_v1",
    prompt_version: str = "",
    params: dict[str, Any] | None = None,
) -> LabExperiment:
    release = get_release(lab_conn, release_id)
    if release is None:
        raise ValueError(f"Release not found: {release_id}")
    created_at = now_iso()
    experiment_id = _stable_id("exp", release_id, method, created_at)
    metrics: dict[str, Any] = {}
    lab_conn.execute(
        """INSERT INTO cluster_experiments
           (experiment_id, release_id, method, prompt_version, params_json,
            status, metrics_json, created_at, git_sha)
           VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?)""",
        (
            experiment_id,
            release_id,
            method,
            prompt_version,
            json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(metrics, ensure_ascii=False, sort_keys=True),
            created_at,
            current_git_sha(),
        ),
    )
    lab_conn.commit()
    return LabExperiment(
        experiment_id=experiment_id,
        release_id=release_id,
        method=method,
        prompt_version=prompt_version,
        status="created",
        metrics=metrics,
        created_at=created_at,
    )


def get_release(lab_conn: sqlite3.Connection, release_id: str) -> LabRelease | None:
    row = lab_conn.execute(
        """SELECT release_id, profile, dates_json, run_ids_json, source_db_path,
                  item_count, created_at
           FROM data_releases
           WHERE release_id = ?""",
        (release_id,),
    ).fetchone()
    if row is None:
        return None
    return LabRelease(
        release_id=str(row["release_id"]),
        profile=str(row["profile"]),
        dates=_json_list(row["dates_json"]),
        run_ids=_json_list(row["run_ids_json"]),
        source_db_path=str(row["source_db_path"]),
        item_count=int(row["item_count"]),
        created_at=str(row["created_at"]),
    )


def get_experiment(lab_conn: sqlite3.Connection, experiment_id: str) -> LabExperiment | None:
    row = lab_conn.execute(
        """SELECT experiment_id, release_id, method, prompt_version, status,
                  metrics_json, created_at
           FROM cluster_experiments
           WHERE experiment_id = ?""",
        (experiment_id,),
    ).fetchone()
    if row is None:
        return None
    return LabExperiment(
        experiment_id=str(row["experiment_id"]),
        release_id=str(row["release_id"]),
        method=str(row["method"]),
        prompt_version=str(row["prompt_version"]),
        status=str(row["status"]),
        metrics=_json_dict(row["metrics_json"]),
        created_at=str(row["created_at"]),
    )


def open_source_db_for_experiment(
    lab_conn: sqlite3.Connection, experiment_id: str
) -> sqlite3.Connection:
    experiment = get_experiment(lab_conn, experiment_id)
    if experiment is None:
        raise ValueError(f"Experiment not found: {experiment_id}")
    release = get_release(lab_conn, experiment.release_id)
    if release is None:
        raise ValueError(f"Release not found: {experiment.release_id}")
    db_path = Path(release.source_db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def propose(
    source_conn: sqlite3.Connection,
    lab_conn: sqlite3.Connection,
    *,
    experiment_id: str,
    domain: str | None = None,
    limit: int = 150,
) -> ProposalStats:
    experiment = get_experiment(lab_conn, experiment_id)
    if experiment is None:
        raise ValueError(f"Experiment not found: {experiment_id}")
    release = get_release(lab_conn, experiment.release_id)
    if release is None:
        raise ValueError(f"Release not found: {experiment.release_id}")
    items = load_release_items(source_conn, lab_conn, release)
    selected = _select_items(items, domain=domain, limit=limit)
    _delete_experiment_outputs(lab_conn, experiment_id)
    pairs = build_candidate_pairs(selected)
    groups = _groups_from_pairs(selected, pairs)
    _save_pairs(lab_conn, experiment_id, pairs)
    story_proposal_ids, story_provider_counts = _save_story_proposals(
        lab_conn, experiment_id, selected, groups
    )
    trend_count = _save_trend_proposals(lab_conn, experiment_id, selected, story_proposal_ids)
    stats = ProposalStats(
        release_items=release.item_count,
        selected_items=len(selected),
        candidate_pairs=len(pairs),
        candidate_groups=len(groups),
        story_proposals=len(story_provider_counts),
        cross_source_story_proposals=sum(
            1 for provider_count in story_provider_counts if provider_count >= 2
        ),
        trend_proposals=trend_count,
    )
    _update_experiment_metrics(lab_conn, experiment_id, stats, status="proposed")
    lab_conn.commit()
    return stats


def compare(
    source_conn: sqlite3.Connection,
    lab_conn: sqlite3.Connection,
    *,
    experiment_id: str,
) -> dict[str, Any]:
    experiment = get_experiment(lab_conn, experiment_id)
    if experiment is None:
        raise ValueError(f"Experiment not found: {experiment_id}")
    release = get_release(lab_conn, experiment.release_id)
    if release is None:
        raise ValueError(f"Release not found: {experiment.release_id}")
    run_placeholders = ",".join("?" for _ in release.run_ids)
    current_story_count = source_conn.execute(
        f"SELECT COUNT(DISTINCT story_id) FROM story_items WHERE run_id IN ({run_placeholders})",
        (*release.run_ids,),
    ).fetchone()[0]
    current_cross_source = source_conn.execute(
        f"""SELECT COUNT(*)
            FROM story_metrics
            WHERE run_id IN ({run_placeholders}) AND source_count >= 2""",
        (*release.run_ids,),
    ).fetchone()[0]
    story_proposals = _count_lab(lab_conn, "story_proposals", experiment_id)
    trend_proposals = _count_lab(lab_conn, "trend_proposals", experiment_id)
    cross_source_story_proposals = lab_conn.execute(
        """SELECT COUNT(*)
           FROM story_proposals
           WHERE experiment_id = ? AND json_array_length(item_ids_json) >= 2""",
        (experiment_id,),
    ).fetchone()[0]
    result = {
        "release_id": release.release_id,
        "experiment_id": experiment_id,
        "release_items": release.item_count,
        "current_story_count": int(current_story_count),
        "current_cross_source": int(current_cross_source),
        "story_proposals": story_proposals,
        "cross_source_story_proposals": int(cross_source_story_proposals),
        "trend_proposals": trend_proposals,
    }
    lab_conn.execute(
        "UPDATE cluster_experiments SET metrics_json = ? WHERE experiment_id = ?",
        (json.dumps(result, ensure_ascii=False, sort_keys=True), experiment_id),
    )
    lab_conn.commit()
    return result


def load_release_items(
    source_conn: sqlite3.Connection,
    lab_conn: sqlite3.Connection,
    release: LabRelease,
) -> list[LabItem]:
    source_conn.row_factory = sqlite3.Row
    item_ids = [
        str(row["item_id"])
        for row in lab_conn.execute(
            "SELECT DISTINCT item_id FROM data_release_items WHERE release_id = ? ORDER BY item_id",
            (release.release_id,),
        ).fetchall()
    ]
    if not item_ids:
        return []
    item_rows = _fetch_rows_by_item_ids(source_conn, "items", item_ids)
    signals = _load_signals(source_conn, release.run_ids, item_ids)
    story_ids = _load_story_ids(source_conn, release.run_ids, item_ids)
    result: list[LabItem] = []
    for row in item_rows:
        item_id = str(row["item_id"])
        signal = signals.get(item_id, {})
        result.append(
            LabItem(
                item_id=item_id,
                provider=str(row["provider"]),
                source_cluster=str(row["source_cluster"]),
                canonical_url=str(row["canonical_url"] or ""),
                target_url=str(_row_value(row, "target_url", "")),
                discussion_url=str(_row_value(row, "discussion_url", "")),
                title=str(row["title"]),
                domain_ids=_json_list(_row_value(row, "domain_ids", '["other"]'), ["other"]),
                signal_domain_ids=_json_list(signal.get("domain_ids"), []),
                candidate_themes=_json_list(signal.get("candidate_themes"), []),
                pain_points=_json_list(signal.get("pain_points"), []),
                goal_relevance=_json_int_dict(signal.get("goal_relevance")),
                current_story_id=story_ids.get(item_id, ""),
            )
        )
    return result


def build_candidate_pairs(items: list[LabItem]) -> list[CandidatePair]:
    pairs: list[CandidatePair] = []
    for idx, left in enumerate(items):
        for right in items[idx + 1 :]:
            pair = _score_pair(left, right)
            if pair is not None:
                pairs.append(pair)
    return sorted(pairs, key=lambda pair: (-pair.score, pair.item_id_a, pair.item_id_b))


def sha256_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _score_pair(left: LabItem, right: LabItem) -> CandidatePair | None:
    urls_left = _canonical_urls(left)
    urls_right = _canonical_urls(right)
    shared_urls = urls_left & urls_right
    if shared_urls:
        return CandidatePair(left.item_id, right.item_id, 1.0, "shared canonical/target URL")
    normalized_left = normalize_title(left.title, left.provider)
    normalized_right = normalize_title(right.title, right.provider)
    if is_low_signal_title(left.title) or is_low_signal_title(right.title):
        return None
    tokens_left = extract_tokens(normalized_left)
    tokens_right = extract_tokens(normalized_right)
    if not tokens_left or not tokens_right:
        return None
    entities = extract_entities(left.title) & extract_entities(right.title)
    shared_tokens = tokens_left & tokens_right
    token_overlap = len(shared_tokens) / max(len(tokens_left | tokens_right), 1)
    same_provider = left.provider == right.provider
    from rapidfuzz import fuzz

    title_score = fuzz.token_set_ratio(normalized_left, normalized_right) / 100.0
    shared_theme = bool(set(left.candidate_themes) & set(right.candidate_themes))
    shared_pain = bool(set(left.pain_points) & set(right.pain_points))
    provider_bonus = 0.08 if not same_provider else 0.0
    signal_bonus = 0.05 if shared_theme or shared_pain else 0.0
    entity_bonus = min(0.12, 0.04 * len(entities))
    score = min(
        0.99,
        title_score * 0.72 + token_overlap * 0.20 + provider_bonus + signal_bonus + entity_bonus,
    )
    if title_score >= 0.9:
        reason = "near-identical title"
    elif not same_provider and title_score >= 0.72 and (entities or shared_theme or shared_pain):
        reason = "cross-source title/entity/signal overlap"
    elif not same_provider and title_score >= 0.62 and len(entities) >= 2:
        reason = "cross-source entity overlap"
    else:
        return None
    return CandidatePair(left.item_id, right.item_id, round(score, 4), reason)


def _groups_from_pairs(items: list[LabItem], pairs: list[CandidatePair]) -> list[list[LabItem]]:
    parent = {item.item_id: item.item_id for item in items}

    def find(item_id: str) -> str:
        while parent[item_id] != item_id:
            parent[item_id] = parent[parent[item_id]]
            item_id = parent[item_id]
        return item_id

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for pair in pairs:
        union(pair.item_id_a, pair.item_id_b)

    grouped: dict[str, list[LabItem]] = {}
    for item in items:
        grouped.setdefault(find(item.item_id), []).append(item)
    groups = [group for group in grouped.values() if len(group) >= 2]
    return sorted(groups, key=lambda group: (-len(group), [item.item_id for item in group]))


def _save_pairs(
    lab_conn: sqlite3.Connection, experiment_id: str, pairs: list[CandidatePair]
) -> None:
    lab_conn.executemany(
        """INSERT INTO cluster_candidate_pairs
           (experiment_id, item_id_a, item_id_b, score, signals_json, reason)
           VALUES (?, ?, ?, ?, '{}', ?)""",
        [
            (
                experiment_id,
                pair.item_id_a,
                pair.item_id_b,
                pair.score,
                pair.reason,
            )
            for pair in pairs
        ],
    )


def _save_story_proposals(
    lab_conn: sqlite3.Connection,
    experiment_id: str,
    items: list[LabItem],
    groups: list[list[LabItem]],
) -> tuple[dict[str, str], list[int]]:
    del items
    story_proposal_ids: dict[str, str] = {}
    saved_provider_counts: list[int] = []
    for group in groups:
        title = _best_title(group)
        if is_low_signal_title(title):
            continue
        item_ids = sorted(item.item_id for item in group)
        group_id = _stable_id("group", experiment_id, *item_ids)
        proposed_story_id = _stable_id("lab_story", *item_ids)
        proposal_id = _stable_id("sp", experiment_id, proposed_story_id)
        confidence = _group_confidence(group)
        reason = _group_reason(group)
        lab_conn.execute(
            """INSERT INTO cluster_candidate_groups
               (group_id, experiment_id, item_ids_json, score, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (
                group_id,
                experiment_id,
                json.dumps(item_ids, ensure_ascii=False),
                confidence,
                reason,
            ),
        )
        lab_conn.execute(
            """INSERT INTO story_proposals
               (proposal_id, experiment_id, proposed_story_id, title, item_ids_json,
                confidence, evidence_ids_json, status, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?)""",
            (
                proposal_id,
                experiment_id,
                proposed_story_id,
                title,
                json.dumps(item_ids, ensure_ascii=False),
                confidence,
                json.dumps(item_ids[:5], ensure_ascii=False),
                reason,
            ),
        )
        for item_id in item_ids:
            story_proposal_ids[item_id] = proposed_story_id
        saved_provider_counts.append(len({item.provider for item in group}))
    return story_proposal_ids, saved_provider_counts


def _save_trend_proposals(
    lab_conn: sqlite3.Connection,
    experiment_id: str,
    items: list[LabItem],
    story_proposal_ids: dict[str, str],
) -> int:
    buckets: dict[str, list[LabItem]] = {}
    for item in items:
        for theme in item.candidate_themes[:3]:
            buckets.setdefault(f"theme:{theme}", []).append(item)
        for pain in item.pain_points[:3]:
            buckets.setdefault(f"pain:{pain}", []).append(item)
        for entity in sorted(extract_entities(item.title) - _GENERIC_ENTITY_TREND_TOKENS)[:6]:
            buckets.setdefault(f"entity:{entity}", []).append(item)
        for topic_key in _title_topic_keys(item):
            buckets.setdefault(f"title_topic:{topic_key}", []).append(item)
    saved = 0
    for key, bucket_items in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:30]:
        unique_items = _unique_items(bucket_items)
        providers = {item.provider for item in unique_items}
        if len(unique_items) < 3 or len(providers) < 2:
            continue
        item_ids = [item.item_id for item in unique_items]
        story_ids = sorted(
            {
                story_id
                for item in unique_items
                for story_id in (item.current_story_id, story_proposal_ids.get(item.item_id, ""))
                if story_id
            }
        )
        proposal_id = _stable_id("tp", experiment_id, key, *item_ids)
        trend_name = _trend_name(key)
        confidence = round(min(0.95, 0.45 + len(providers) * 0.06 + len(unique_items) * 0.015), 4)
        lab_conn.execute(
            """INSERT INTO trend_proposals
               (proposal_id, experiment_id, trend_name, story_ids_json, item_ids_json,
                confidence, evidence_ids_json, status, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?)""",
            (
                proposal_id,
                experiment_id,
                trend_name,
                json.dumps(story_ids, ensure_ascii=False),
                json.dumps(item_ids, ensure_ascii=False),
                confidence,
                json.dumps(item_ids[:5], ensure_ascii=False),
                f"{key}; {len(unique_items)} items; {len(providers)} providers",
            ),
        )
        saved += 1
    return saved


def _delete_experiment_outputs(lab_conn: sqlite3.Connection, experiment_id: str) -> None:
    for table in (
        "cluster_candidate_pairs",
        "cluster_candidate_groups",
        "llm_cluster_reviews",
        "story_proposals",
        "trend_proposals",
        "cluster_eval_results",
    ):
        lab_conn.execute(f"DELETE FROM {table} WHERE experiment_id = ?", (experiment_id,))


def _update_experiment_metrics(
    lab_conn: sqlite3.Connection,
    experiment_id: str,
    stats: ProposalStats,
    *,
    status: str,
) -> None:
    lab_conn.execute(
        "UPDATE cluster_experiments SET status = ?, metrics_json = ? WHERE experiment_id = ?",
        (
            status,
            json.dumps(stats.__dict__, ensure_ascii=False, sort_keys=True),
            experiment_id,
        ),
    )


def _select_items(items: list[LabItem], domain: str | None, limit: int) -> list[LabItem]:
    filtered = [
        item
        for item in items
        if domain is None or domain in set(item.domain_ids + item.signal_domain_ids)
    ]
    return sorted(filtered, key=_item_sort_key)[:limit]


def _item_sort_key(item: LabItem) -> tuple[int, str, str]:
    return (-max(item.goal_relevance.values(), default=0), item.provider, item.item_id)


def _canonical_urls(item: LabItem) -> set[str]:
    return {
        url.rstrip("/")
        for url in (item.canonical_url, item.target_url, item.discussion_url)
        if url and url.startswith(("http://", "https://"))
    }


def _best_title(group: list[LabItem]) -> str:
    return sorted(
        group,
        key=lambda item: (-len(extract_entities(item.title)), len(item.title)),
    )[0].title


def _group_confidence(group: list[LabItem]) -> float:
    providers = {item.provider for item in group}
    return round(min(0.97, 0.55 + len(group) * 0.06 + len(providers) * 0.08), 4)


def _group_reason(group: list[LabItem]) -> str:
    providers = sorted({item.provider for item in group})
    return f"{len(group)} items; {len(providers)} provider(s): {', '.join(providers)}"


def _trend_name(key: str) -> str:
    kind, value = key.split(":", 1)
    if kind == "pain":
        return f"Pain point: {value}"
    if kind == "entity":
        return f"Entity cluster: {value}"
    if kind == "title_topic":
        return f"Title pattern: {value}"
    return value


def _title_topic_keys(item: LabItem) -> list[str]:
    if is_low_signal_title(item.title):
        return []
    normalized = normalize_title(item.title, item.provider)
    tokens = [
        token
        for token in normalized.split()
        if token not in _TREND_STOP_TOKENS and not token.isdigit()
    ]
    if len(tokens) < 2:
        return []
    keys: list[str] = []
    for idx, left in enumerate(tokens):
        for right in tokens[idx + 1 : idx + 4]:
            if left == right:
                continue
            keys.append(" ".join(sorted((left, right))))
    return keys[:8]


def _unique_items(items: list[LabItem]) -> list[LabItem]:
    seen: set[str] = set()
    result: list[LabItem] = []
    for item in items:
        if item.item_id not in seen:
            seen.add(item.item_id)
            result.append(item)
    return result


def _fetch_rows_by_item_ids(
    conn: sqlite3.Connection, table: str, item_ids: list[str]
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for chunk in _chunks(item_ids, 500):
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            conn.execute(
                f"SELECT * FROM {table} WHERE item_id IN ({placeholders})",
                (*chunk,),
            ).fetchall()
        )
    return rows


def _load_signals(
    conn: sqlite3.Connection, run_ids: list[str], item_ids: list[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not run_ids or not item_ids:
        return result
    for item_chunk in _chunks(item_ids, 500):
        item_placeholders = ",".join("?" for _ in item_chunk)
        run_placeholders = ",".join("?" for _ in run_ids)
        rows = conn.execute(
            f"""SELECT *
                FROM item_signals
                WHERE run_id IN ({run_placeholders})
                  AND item_id IN ({item_placeholders})""",
            (*run_ids, *item_chunk),
        ).fetchall()
        for row in rows:
            result[str(row["item_id"])] = dict(row)
    return result


def _load_story_ids(
    conn: sqlite3.Connection, run_ids: list[str], item_ids: list[str]
) -> dict[str, str]:
    result: dict[str, str] = {}
    if not run_ids or not item_ids:
        return result
    for item_chunk in _chunks(item_ids, 500):
        item_placeholders = ",".join("?" for _ in item_chunk)
        run_placeholders = ",".join("?" for _ in run_ids)
        rows = conn.execute(
            f"""SELECT item_id, story_id
                FROM story_items
                WHERE run_id IN ({run_placeholders})
                  AND item_id IN ({item_placeholders})""",
            (*run_ids, *item_chunk),
        ).fetchall()
        for row in rows:
            result[str(row["item_id"])] = str(row["story_id"])
    return result


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[idx : idx + size] for idx in range(0, len(values), size)]


def _next_release_id(lab_conn: sqlite3.Connection, profile: str, dates: list[str]) -> str:
    date_part = dates[0] if len(dates) == 1 else f"{dates[0]}_{dates[-1]}"
    prefix = f"{date_part}-{profile}-r"
    rows = lab_conn.execute(
        "SELECT release_id FROM data_releases WHERE release_id LIKE ?",
        (f"{prefix}%",),
    ).fetchall()
    versions: list[int] = []
    for row in rows:
        suffix = str(row["release_id"]).removeprefix(prefix)
        if suffix.isdigit():
            versions.append(int(suffix))
    return f"{prefix}{max(versions, default=0) + 1}"


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\u241f".join(parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def _json_list(raw: Any, fallback: list[str] | None = None) -> list[str]:
    if isinstance(raw, list):
        return [str(v) for v in raw]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    return list(fallback or [])


def _row_value(row: sqlite3.Row, key: str, default: Any) -> Any:
    keys = set(row.keys())
    return row[key] if key in keys and row[key] is not None else default


def _json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _json_int_dict(raw: Any) -> dict[str, int]:
    parsed = _json_dict(raw)
    result: dict[str, int] = {}
    for key, value in parsed.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def _count_lab(lab_conn: sqlite3.Connection, table: str, experiment_id: str) -> int:
    return int(
        lab_conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()[0]
    )
