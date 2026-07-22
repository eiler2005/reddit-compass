"""Заготовки уведомлений: формирование данных для Telegram/email БЕЗ отправки.

Сервис готовит структурированные сообщения и пишет в data/notifications/.
Реальная отправка — будущий sender (Phase 4), который прочитает эти файлы.
Паттерн: pulsar-trader-lab bot/messaging.py (TelegramConfig, SQLite log).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .models import PostCard, ViralitySignal

logger = logging.getLogger("reddit_compass")

DEFAULT_NOTIFICATIONS_DIR = Path("data/notifications")


@dataclass(frozen=True)
class TelegramConfig:
    """Конфигурация Telegram (для будущего sender'а)."""

    bot_token: str
    chat_id: str
    message_thread_id: int | None = None


@dataclass
class TelegramMessage:
    """Подготовленное сообщение для Telegram (без отправки)."""

    text: str
    parse_mode: str = "HTML"
    disable_web_page_preview: bool = True
    prepared_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class EmailPayload:
    """Подготовленный email (без отправки)."""

    subject: str
    body_html: str
    body_text: str
    prepared_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def prepare_telegram_digest(
    snapshot_date: str,
    cards: list[PostCard],
    signals: list[ViralitySignal] | None = None,
    top_n: int = 5,
) -> TelegramMessage:
    """Формирует текст Telegram-дайджеста из snapshot.

    Пример вывода:
        🧭 reddit-compass: ночной разбор 2026-07-22
        📊 73 поста, 19 сабреддитов, 2 виральных сигнала

        🔥 Топ-5 по score:
        1. [r/artificial] Title... (score: 5200, 342 комм.)
        ...

        ⚡ Виральные сигналы:
        • crosspost: Title... (r/x → r/y, r/z)
    """
    signals = signals or []
    top_posts = sorted(cards, key=lambda c: c.score, reverse=True)[:top_n]
    subreddits_count = len({c.subreddit for c in cards})

    lines = [
        f"🧭 <b>reddit-compass</b>: ночной разбор {snapshot_date}",
        f"📊 {len(cards)} постов, {subreddits_count} сабреддитов,"
        f" {len(signals)} виральных сигналов",
        "",
        f"🔥 <b>Топ-{top_n} по score:</b>",
    ]

    for i, post in enumerate(top_posts, 1):
        lines.append(
            f"{i}. [r/{post.subreddit}] {post.title[:80]}"
            f" (score: {post.score}, {post.num_comments} комм.)"
        )

    if signals:
        lines.append("")
        lines.append("⚡ <b>Виральные сигналы:</b>")
        for sig in signals[:3]:
            targets = ", ".join(sig.crossposted_to[:3])
            lines.append(f"• {sig.signal_type}: {sig.title[:60]} (→ {targets})")

    lines.append("")
    lines.append(f"📁 Полный отчёт: data/snapshots/{snapshot_date}/trends-report.md")

    return TelegramMessage(text="\n".join(lines))


def prepare_email_digest(
    snapshot_date: str,
    cards: list[PostCard],
    signals: list[ViralitySignal] | None = None,
    top_n: int = 10,
) -> EmailPayload:
    """Формирует email-дайджест (subject + HTML + plain text)."""
    signals = signals or []
    top_posts = sorted(cards, key=lambda c: c.score, reverse=True)[:top_n]
    subreddits_count = len({c.subreddit for c in cards})

    subject = f"reddit-compass: {snapshot_date} — {len(cards)} постов, {len(signals)} сигналов"

    # Plain text
    text_lines = [
        f"reddit-compass: ночной разбор {snapshot_date}",
        f"{len(cards)} постов, {subreddits_count} сабреддитов, {len(signals)} виральных сигналов",
        "",
        f"Топ-{top_n} по score:",
    ]
    for i, post in enumerate(top_posts, 1):
        text_lines.append(f"{i}. r/{post.subreddit}: {post.title[:100]} (score: {post.score})")

    # HTML
    html_rows = []
    for post in top_posts:
        html_rows.append(
            f"<tr><td>r/{post.subreddit}</td><td>{post.title[:100]}</td>"
            f"<td>{post.score}</td><td>{post.num_comments}</td></tr>"
        )

    body_html = f"""<html><body>
<h2>reddit-compass: ночной разбор {snapshot_date}</h2>
<p>{len(cards)} постов, {subreddits_count} сабреддитов, {len(signals)} виральных сигналов</p>
<table border="1" cellpadding="4">
<tr><th>Subreddit</th><th>Title</th><th>Score</th><th>Comments</th></tr>
{"".join(html_rows)}
</table>
</body></html>"""

    return EmailPayload(
        subject=subject,
        body_html=body_html,
        body_text="\n".join(text_lines),
    )


def save_notification(
    message: TelegramMessage | EmailPayload,
    snapshot_date: str,
    notifications_dir: Path = DEFAULT_NOTIFICATIONS_DIR,
) -> Path:
    """Пишет подготовленное уведомление в data/notifications/ (JSONL)."""
    notifications_dir.mkdir(parents=True, exist_ok=True)
    kind = "telegram" if isinstance(message, TelegramMessage) else "email"
    path = notifications_dir / f"{kind}-{snapshot_date}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(message.to_json() + "\n")
    logger.info("Уведомление (%s) подготовлено → %s", kind, path)
    return path
