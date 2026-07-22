"""Тесты заготовок уведомлений (notify.py)."""

from __future__ import annotations

from pathlib import Path

from reddit_compass.models import PostCard, ViralitySignal
from reddit_compass.notify import (
    EmailPayload,
    TelegramMessage,
    prepare_email_digest,
    prepare_telegram_digest,
    save_notification,
)


def _make_card(title: str = "Test", score: int = 100, subreddit: str = "ai") -> PostCard:
    return PostCard(
        subreddit=subreddit,
        post_id="x1",
        title=title,
        author="u",
        created_utc=None,
        score=score,
        upvote_ratio=0.9,
        num_comments=5,
        url="",
        selftext="",
        link_flair_text=None,
        is_self=True,
        permalink="/r/ai/x1",
        monitoring_type="hot",
        snapshot_date="2026-07-22",
    )


def _make_signal() -> ViralitySignal:
    return ViralitySignal(
        post_id="x1",
        title="Viral",
        original_subreddit="ai",
        crossposted_to=["tech", "news"],
        total_score=3000,
        total_comments=200,
        signal_type="crosspost",
        detected_at="2026-07-22",
        url="",
    )


class TestTelegramDigest:
    def test_basic_format(self):
        cards = [_make_card("Post A", 500), _make_card("Post B", 300)]
        msg = prepare_telegram_digest("2026-07-22", cards, [_make_signal()])
        assert isinstance(msg, TelegramMessage)
        assert "reddit-compass" in msg.text
        assert "2026-07-22" in msg.text
        assert "2 постов" in msg.text
        assert "Post A" in msg.text
        assert msg.parse_mode == "HTML"

    def test_top_n_limit(self):
        cards = [_make_card(f"Post {i}", i * 10) for i in range(20)]
        msg = prepare_telegram_digest("2026-07-22", cards, top_n=3)
        # Only top 3 should appear
        assert "Post 19" in msg.text
        assert "Post 18" in msg.text
        assert "Post 17" in msg.text

    def test_empty_cards(self):
        msg = prepare_telegram_digest("2026-07-22", [])
        assert "0 постов" in msg.text


class TestEmailDigest:
    def test_basic_format(self):
        cards = [_make_card("Email Post", 999)]
        payload = prepare_email_digest("2026-07-22", cards)
        assert isinstance(payload, EmailPayload)
        assert "2026-07-22" in payload.subject
        assert "Email Post" in payload.body_html
        assert "Email Post" in payload.body_text
        assert "<table" in payload.body_html


class TestSaveNotification:
    def test_save_telegram(self, tmp_path: Path):
        msg = prepare_telegram_digest("2026-07-22", [_make_card()])
        path = save_notification(msg, "2026-07-22", tmp_path)
        assert path.exists()
        assert "telegram" in path.name
        content = path.read_text()
        assert "reddit-compass" in content

    def test_save_email(self, tmp_path: Path):
        payload = prepare_email_digest("2026-07-22", [_make_card()])
        path = save_notification(payload, "2026-07-22", tmp_path)
        assert path.exists()
        assert "email" in path.name
