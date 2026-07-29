import sqlite3
from pathlib import Path

from reddit_compass.intelligence.lab import (
    compare,
    create_experiment,
    create_release,
    lab_db,
    list_releases,
    propose,
)
from reddit_compass.intelligence.migrations import migrate
from reddit_compass.intelligence.models import (
    ContentItem,
    ItemSignal,
    Observation,
    SourceCluster,
    Story,
    StoryMetric,
)
from reddit_compass.intelligence.repository import (
    replace_run_signals,
    replace_run_stories,
    upsert_items,
    upsert_observations,
    upsert_run,
)


def test_cluster_lab_release_experiment_propose_and_compare(tmp_path: Path) -> None:
    source_db_path = tmp_path / "compass.db"
    source_conn = _seed_source_db(source_db_path)
    lab_conn = lab_db(tmp_path / "cluster_lab.db")

    release = create_release(
        source_conn,
        lab_conn,
        source_db_path=source_db_path,
        releases_dir=tmp_path / "releases",
        profile="broad",
        dates=["2026-07-29"],
    )
    experiment = create_experiment(lab_conn, release_id=release.release_id, method="hybrid_v1")

    before_story_count = source_conn.execute("SELECT COUNT(*) FROM story_metrics").fetchone()[0]
    stats = propose(
        source_conn,
        lab_conn,
        experiment_id=experiment.experiment_id,
        domain="ai_technology",
        limit=10,
    )
    after_story_count = source_conn.execute("SELECT COUNT(*) FROM story_metrics").fetchone()[0]
    comparison = compare(source_conn, lab_conn, experiment_id=experiment.experiment_id)

    assert release.release_id == "2026-07-29-broad-r1"
    assert release.item_count == 4
    assert stats.selected_items == 4
    assert stats.story_proposals >= 1
    assert stats.trend_proposals >= 1
    assert before_story_count == after_story_count
    assert comparison["current_story_count"] == 4
    assert comparison["story_proposals"] == stats.story_proposals
    assert comparison["trend_proposals"] == stats.trend_proposals


def test_cluster_lab_release_create_versions_are_immutable(tmp_path: Path) -> None:
    source_db_path = tmp_path / "compass.db"
    source_conn = _seed_source_db(source_db_path)
    lab_conn = lab_db(tmp_path / "cluster_lab.db")

    first = create_release(
        source_conn,
        lab_conn,
        source_db_path=source_db_path,
        releases_dir=tmp_path / "releases",
        profile="broad",
        dates=["2026-07-29"],
    )
    second = create_release(
        source_conn,
        lab_conn,
        source_db_path=source_db_path,
        releases_dir=tmp_path / "releases",
        profile="broad",
        dates=["2026-07-29"],
    )

    assert first.release_id == "2026-07-29-broad-r1"
    assert second.release_id == "2026-07-29-broad-r2"
    assert [r.release_id for r in list_releases(lab_conn)] == [
        second.release_id,
        first.release_id,
    ]
    assert (tmp_path / "releases" / first.release_id / "manifest.json").exists()
    assert (tmp_path / "releases" / second.release_id / "manifest.json").exists()


def test_cluster_lab_propose_is_idempotent_per_experiment(tmp_path: Path) -> None:
    source_db_path = tmp_path / "compass.db"
    source_conn = _seed_source_db(source_db_path)
    lab_conn = lab_db(tmp_path / "cluster_lab.db")
    release = create_release(
        source_conn,
        lab_conn,
        source_db_path=source_db_path,
        releases_dir=tmp_path / "releases",
        profile="broad",
        dates=["2026-07-29"],
    )
    experiment = create_experiment(lab_conn, release_id=release.release_id)

    first = propose(source_conn, lab_conn, experiment_id=experiment.experiment_id, limit=10)
    second = propose(source_conn, lab_conn, experiment_id=experiment.experiment_id, limit=10)
    row_count = lab_conn.execute(
        "SELECT COUNT(*) FROM story_proposals WHERE experiment_id = ?",
        (experiment.experiment_id,),
    ).fetchone()[0]

    assert first == second
    assert row_count == first.story_proposals


def _seed_source_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    run_id = "2026-07-29:broad"
    upsert_run(
        conn,
        run_id=run_id,
        snapshot_date="2026-07-29",
        profile="broad",
        status="complete",
        started_at="2026-07-29T07:00:00Z",
        finished_at="2026-07-29T07:10:00Z",
    )
    items = [
        _item(
            "i_reuters",
            "reuters",
            "business",
            "OpenAI starts security review after Hugging Face breach",
        ),
        _item(
            "i_nyt",
            "nytimes",
            "mainstream",
            "OpenAI opens security review following Hugging Face breach",
        ),
        _item(
            "i_hn",
            "hackernews",
            "developers",
            "Hugging Face breach sparks open source AI security debate",
        ),
        _item(
            "i_tc",
            "techcrunch",
            "tech_culture",
            "AI startups face new security breach concerns",
        ),
    ]
    upsert_items(conn, items)
    upsert_observations(
        conn,
        [
            Observation(run_id=run_id, item_id=item.item_id, observed_at="2026-07-29T07:00:00Z")
            for item in items
        ],
    )
    replace_run_signals(
        conn,
        run_id,
        [
            ItemSignal(
                item_id=item.item_id,
                domain_ids=["ai_technology", "security_privacy"],
                candidate_themes=["AI security"],
                pain_points=["security breach"],
                goal_relevance={"book": 80, "rbc": 70},
                analyzed_at="2026-07-29T07:00:00Z",
            )
            for item in items
        ],
    )
    replace_run_stories(
        conn,
        run_id,
        [
            Story(
                story_id=f"story_{item.item_id}",
                canonical_key=item.item_id,
                title=item.title,
                domain_ids=item.domain_ids,
                item_ids=[item.item_id],
            )
            for item in items
        ],
        [
            StoryMetric(
                run_id=run_id,
                story_id=f"story_{item.item_id}",
                item_count=1,
                source_count=1,
            )
            for item in items
        ],
    )
    conn.commit()
    return conn


def _item(
    item_id: str,
    provider: str,
    source_cluster: SourceCluster,
    title: str,
) -> ContentItem:
    return ContentItem(
        item_id=item_id,
        provider=provider,
        source_cluster=source_cluster,
        external_id=item_id,
        canonical_url=f"https://example.com/{item_id}",
        title=title,
        observed_at="2026-07-29T07:00:00Z",
        snapshot_date="2026-07-29",
        content_scope="headline",
        domain_ids=["ai_technology", "security_privacy"],
    )
