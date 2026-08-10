"""Тесты LinkedIn-адаптера (sources/linkedin.py).

Фикстуры синтетические, но повторяют структуру реальных гостевых страниц,
проверенную спайком 2026-08-10: JSON-LD SocialMediaPosting / Article,
og-мета, региональные поддомены в выдаче.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from reddit_compass.collector import _FILE_MAP as COLLECTOR_FILE_MAP
from reddit_compass.intelligence.compat import _LEGACY_FILE_MAP, postcard_to_content_item
from reddit_compass.intelligence.rebuild import _LEGACY_FILES as REBUILD_FILES
from reddit_compass.intelligence.repair import _LEGACY_FILES as REPAIR_FILES
from reddit_compass.sources.linkedin import (
    LINKEDIN_AUTHORS,
    POST_URL_RE,
    SEED_POST_URLS,
    LinkedinAuthor,
    activity_timestamp,
    article_matches_author,
    canonicalize_linkedin_url,
    extract_post_urls,
    filter_fresh_candidates,
    parse_article_card,
    parse_post_card,
    render_linkedin_report,
)

SNAPSHOT_DATE = "2026-08-10"


def _post_jsonld(author_name: str = "Andrew Ng") -> dict[str, object]:
    return {
        "@context": "https://schema.org",
        "@type": "SocialMediaPosting",
        "@id": "urn:li:activity:6981363923094560768",
        "headline": "A serious matter for newcomers to AI",
        "text": "A serious matter for newcomers to AI",
        "articleBody": (
            "I’d like to address the serious matter of some newcomers to AI "
            "experiencing imposter syndrome, where someone wonders if they’re "
            "a fraud or really belong in the AI community."
        ),
        "author": {"@type": "Person", "name": author_name},
        "datePublished": "2022-09-29T21:27:46.713Z",
        "commentCount": 113,
        "interactionStatistic": [
            {
                "@type": "InteractionCounter",
                "interactionType": "http://schema.org/LikeAction",
                "userInteractionCount": 10970,
            },
            {
                "@type": "InteractionCounter",
                "interactionType": "https://schema.org/CommentAction",
                "userInteractionCount": 113,
            },
        ],
        "hasPart": {
            "@type": "WebPageElement",
            "isAccessibleForFree": False,
            "cssSelector": ".details",
        },
        "comment": [
            {
                "@type": "Comment",
                "datePublished": "2022-11-04T14:27:12.563Z",
                "text": "Thank you for building this community.",
                "author": {"@type": "Person", "name": "Jane Reader"},
                "interactionStatistic": {
                    "@type": "InteractionCounter",
                    "interactionType": "http://schema.org/LikeAction",
                    "userInteractionCount": 12,
                },
            }
        ],
    }


def _post_page_html(author_name: str = "Andrew Ng") -> str:
    block = json.dumps(_post_jsonld(author_name))
    return (
        "<html><head>"
        f'<script type="application/ld+json">{block}</script>'
        '<meta property="og:title" content="The Batch | Andrew Ng | 113 comments">'
        '<meta property="og:description" content="short og preview">'
        "</head><body></body></html>"
    )


def _video_post_html() -> str:
    """Видео-пост: LinkedIn отдаёт VideoObject с creator вместо author."""
    block = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "VideoObject",
            "headline": "Decision intelligence in 6 minutes",
            "name": "Decision intelligence in 6 minutes",
            "description": "A short video about decision-first AI leadership.",
            "creator": {"@type": "Person", "name": "Cassie Kozyrkov"},
            "datePublished": "2026-04-27T12:00:07.754Z",
            "commentCount": 12,
            "interactionStatistic": {
                "@type": "InteractionCounter",
                "interactionType": "http://schema.org/LikeAction",
                "userInteractionCount": 381,
            },
        }
    )
    return (
        "<html><head>"
        f'<script type="application/ld+json">{block}</script>'
        "</head><body></body></html>"
    )


def _article_page_html(author_name: str) -> str:
    block = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": "Decision intelligence explained",
            "author": {"@type": "Person", "name": author_name},
            "datePublished": "2023-04-11T00:00:01.000+00:00",
            "commentCount": 5,
            "interactionStatistic": {
                "@type": "InteractionCounter",
                "interactionType": "http://schema.org/LikeAction",
                "userInteractionCount": 42,
            },
        }
    )
    return (
        "<html><head>"
        f'<script type="application/ld+json">{block}</script>'
        '<meta property="og:description" content="og article preview">'
        "</head><body></body></html>"
    )


class TestActivityTimestamp:
    def test_known_id(self):
        # ID из спайка: datePublished поста 2022-09-29T21:27:46Z
        ts = activity_timestamp("6981363923094560768")
        assert ts is not None
        expected = datetime(2022, 9, 29, tzinfo=UTC)
        assert abs((ts - expected).total_seconds()) < 24 * 3600

    def test_invalid_id(self):
        assert activity_timestamp("not-a-number") is None


class TestFilterFreshCandidates:
    def test_drops_stale_keeps_fresh_and_unknown(self):
        cutoff = datetime(2026, 7, 1, tzinfo=UTC)
        entries = [
            (
                "https://www.linkedin.com/posts/a_stale-activity-111-x",
                "111",
                datetime(2022, 9, 29, tzinfo=UTC),
            ),
            (
                "https://www.linkedin.com/posts/a_new-activity-222-x",
                "222",
                datetime(2026, 8, 1, tzinfo=UTC),
            ),
            (
                "https://www.linkedin.com/posts/a_mid-activity-333-x",
                "333",
                datetime(2026, 7, 15, tzinfo=UTC),
            ),
            ("https://www.linkedin.com/posts/a_nodate-activity-444-x", "444", None),
        ]
        urls = filter_fresh_candidates(entries, cutoff, limit=8)
        assert urls == [
            "https://www.linkedin.com/posts/a_new-activity-222-x",
            "https://www.linkedin.com/posts/a_mid-activity-333-x",
            # Кандидат без даты не отбрасывается, но идёт после датированных
            "https://www.linkedin.com/posts/a_nodate-activity-444-x",
        ]

    def test_limit_and_dedupe(self):
        cutoff = datetime(2020, 1, 1, tzinfo=UTC)
        url = "https://www.linkedin.com/posts/a_x-activity-222-x"
        entries = [
            (url, "222", datetime(2026, 8, 1, tzinfo=UTC)),
            (url, "222", datetime(2026, 8, 1, tzinfo=UTC)),
            (
                "https://www.linkedin.com/posts/a_y-activity-333-x",
                "333",
                datetime(2026, 7, 15, tzinfo=UTC),
            ),
        ]
        assert filter_fresh_candidates(entries, cutoff, limit=1) == [url]
        assert len(filter_fresh_candidates(entries, cutoff, limit=8)) == 2

    def test_seed_urls_survive_spring_window(self):
        """Seed'ы — запасной корпус при деградации поиска: в окне 2026 они живы."""
        cutoff = datetime(2026, 2, 1, tzinfo=UTC)
        entries: list[tuple[str, str, datetime | None]] = []
        for url in SEED_POST_URLS["kozyrkov"]:
            canonical = canonicalize_linkedin_url(url)
            m = POST_URL_RE.search(canonical)
            assert m is not None
            entries.append((canonical, m.group(2), activity_timestamp(m.group(2))))
        urls = filter_fresh_candidates(entries, cutoff, limit=8)
        assert len(urls) == len(SEED_POST_URLS["kozyrkov"])


class TestPipelineMapping:
    """LinkedIn — отдельный канал: его файловые мэппинги не должны потеряться."""

    def test_collector_file_map(self):
        assert COLLECTOR_FILE_MAP["linkedin"] == "linkedin.jsonl"

    def test_compat_file_map(self):
        assert _LEGACY_FILE_MAP["linkedin.jsonl"] == ("linkedin", "voices")

    def test_rebuild_and_repair_pick_up_linkedin_jsonl(self):
        assert "linkedin.jsonl" in REBUILD_FILES
        assert "linkedin.jsonl" in REPAIR_FILES


class TestCanonicalize:
    def test_regional_subdomain(self):
        url = "https://de.linkedin.com/posts/andrewyng_x-activity-6981363923094560768-rjaY"
        assert canonicalize_linkedin_url(url) == (
            "https://www.linkedin.com/posts/andrewyng_x-activity-6981363923094560768-rjaY"
        )

    def test_strips_query(self):
        url = "https://www.linkedin.com/pulse/some-article?trk=abc"
        assert canonicalize_linkedin_url(url) == "https://www.linkedin.com/pulse/some-article"


class TestExtractPostUrls:
    def test_filters_by_slug_and_normalizes(self):
        html = " ".join(
            [
                "https://de.linkedin.com/posts/andrewyng_hello-activity-6896574118641172480--nLq",
                "https://www.linkedin.com/posts/qiio-magazin_zitat-activity-7120700346359795712-wmLQ",
                "https://www.linkedin.com/posts/andrewyng_world-activity-6942886494437089281-jEtY",
                "https://www.linkedin.com/posts/andrewyng_hello-activity-6896574118641172480--nLq",
            ]
        )
        urls = extract_post_urls(html, "andrewyng")
        flat = [u for u, _, _ in urls]
        assert flat == [
            "https://www.linkedin.com/posts/andrewyng_hello-activity-6896574118641172480--nLq",
            "https://www.linkedin.com/posts/andrewyng_world-activity-6942886494437089281-jEtY",
        ]
        _, activity_id, ts = urls[0]
        assert activity_id == "6896574118641172480"
        assert ts is not None and ts.year == 2022


class TestParsePostCard:
    def test_full_mapping(self):
        url = "https://www.linkedin.com/posts/andrewyng_x-activity-6981363923094560768-rjaY"
        card = parse_post_card(_post_page_html(), url, "Andrew Ng", "andrewyng", SNAPSHOT_DATE)
        assert card is not None
        assert card.post_id == "6981363923094560768"
        assert card.subreddit == "linkedin"
        assert card.title == "A serious matter for newcomers to AI"
        assert card.author == "Andrew Ng"
        assert card.created_utc == "2022-09-29T21:27:46.713000Z"
        assert card.score == 10970
        assert card.num_comments == 113
        assert card.monitoring_type == "linkedin"
        assert card.keyword == "andrewyng"
        assert card.snapshot_date == SNAPSHOT_DATE
        # Превью из articleBody длиннее og-меты и text — берём максимум
        assert "imposter syndrome" in card.selftext
        assert len(card.top_comments) == 1
        comment = card.top_comments[0]
        assert comment.author == "Jane Reader"
        assert comment.score == 12
        assert comment.created_utc == "2022-11-04T14:27:12.563Z"

    def test_video_post_parses_via_videoobject(self):
        """Регрессия: видео-посты молча выпадали, парсер ждал только SocialMediaPosting."""
        url = "https://www.linkedin.com/posts/kozyrkov_x-activity-7454499621982281728-Oo3Q"
        card = parse_post_card(
            _video_post_html(), url, "Cassie Kozyrkov", "kozyrkov", SNAPSHOT_DATE
        )
        assert card is not None
        assert card.author == "Cassie Kozyrkov"  # из creator, не из fallback
        assert card.title == "Decision intelligence in 6 minutes"
        assert card.selftext.startswith("A short video")
        assert card.score == 381
        assert card.num_comments == 12
        assert card.created_utc == "2026-04-27T12:00:07.754000Z"

    def test_no_jsonld_returns_none(self):
        url = "https://www.linkedin.com/posts/andrewyng_x-activity-6981363923094560768-rjaY"
        card = parse_post_card("<html></html>", url, "Andrew Ng", "andrewyng", SNAPSHOT_DATE)
        assert card is None

    def test_authors_configured(self):
        slugs = {a.slug for a in LINKEDIN_AUTHORS}
        assert slugs == {"andrewyng", "fei-fei-li-4541247", "alliekmiller", "kozyrkov"}


class TestParseArticleCard:
    def test_author_article_accepted(self):
        url = "https://www.linkedin.com/pulse/decision-intelligence-explained"
        html = _article_page_html("Cassie Kozyrkov")
        card = parse_article_card(html, url, SNAPSHOT_DATE, "kozyrkov")
        assert card is not None
        assert card.post_id.startswith("pulse-")
        assert card.title == "Decision intelligence explained"
        assert article_matches_author(card, LinkedinAuthor("Cassie Kozyrkov", "kozyrkov"))

    def test_third_party_article_rejected(self):
        url = "https://www.linkedin.com/pulse/ai-expert-fei-fei-li"
        html = _article_page_html("Meet Global by Business Next Media")
        card = parse_article_card(html, url, SNAPSHOT_DATE, "fei-fei-li-4541247")
        assert card is not None
        assert not article_matches_author(card, LinkedinAuthor("Fei-Fei Li", "fei-fei-li-4541247"))

    def test_no_article_jsonld(self):
        card = parse_article_card(
            "<html></html>", "https://www.linkedin.com/pulse/x", SNAPSHOT_DATE, "s"
        )
        assert card is None


class TestCompatIntegration:
    def test_postcard_to_content_item(self):
        url = "https://www.linkedin.com/posts/andrewyng_x-activity-6981363923094560768-rjaY"
        card = parse_post_card(_post_page_html(), url, "Andrew Ng", "andrewyng", SNAPSHOT_DATE)
        assert card is not None
        item = postcard_to_content_item(card, "linkedin.jsonl", "2026-08-10T12:00:00+00:00")
        assert item.provider == "linkedin"
        assert item.source_cluster == "voices"
        assert item.content_scope == "excerpt"
        assert item.source_section == "andrewyng"
        assert item.external_id == "6981363923094560768"
        assert item.raw_engagement == {"reactions": 10970.0, "comments": 113.0}
        assert item.published_at == "2022-09-29T21:27:46.713000Z"


class TestReport:
    def test_renders_authors_and_posts(self):
        url = "https://www.linkedin.com/posts/andrewyng_x-activity-6981363923094560768-rjaY"
        card = parse_post_card(_post_page_html(), url, "Andrew Ng", "andrewyng", SNAPSHOT_DATE)
        assert card is not None
        report = render_linkedin_report([card], SNAPSHOT_DATE)
        assert "## Andrew Ng (1)" in report
        assert "реакций: 10970" in report
        assert url in report
        assert "💬 **Jane Reader**" in report

    def test_multi_author_grouping_and_order(self):
        url_a = "https://www.linkedin.com/posts/andrewyng_x-activity-6981363923094560768-rjaY"
        ng = parse_post_card(_post_page_html(), url_a, "Andrew Ng", "andrewyng", SNAPSHOT_DATE)
        ffl = parse_post_card(
            _post_page_html(author_name="Fei-Fei Li"),
            "https://www.linkedin.com/posts/fei-fei-li-4541247_y-activity-7158595819326066690-7h8w",
            "Fei-Fei Li",
            "fei-fei-li-4541247",
            SNAPSHOT_DATE,
        )
        assert ng is not None and ffl is not None
        report = render_linkedin_report([ffl, ng], SNAPSHOT_DATE)
        assert report.index("## Andrew Ng (1)") < report.index("## Fei-Fei Li (1)")

    def test_empty_report_notes_degradation(self):
        report = render_linkedin_report([], SNAPSHOT_DATE)
        assert "не собраны" in report
