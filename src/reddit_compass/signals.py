"""LLM-анализ сигналов: Qwen API → pain points, business relevance, темы для колонок.

Вход: posts.jsonl (snapshot). Выход: signals.jsonl + секция в отчёте.
API: DashScope OpenAI-compatible (Qwen). Ключ: DASHSCOPE_API_KEY.
Модель: qwen-plus (bulk-классификация), qwen-max (синтез).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import PostCard

logger = logging.getLogger("reddit_compass")

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
SYNTHESIS_MODEL = "qwen-max"
BATCH_SIZE = 10  # постов на один LLM-запрос
MAX_CONCURRENT = 3


@dataclass
class SignalCard:
    """Результат LLM-анализа одного поста."""

    post_id: str
    title: str
    subreddit: str
    score: int
    pain_points: list[str] = field(default_factory=list)
    buying_intent: bool = False
    business_relevance: int = 0  # 1-10
    book_relevance: int = 0  # 1-10
    themes: list[str] = field(default_factory=list)
    summary: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class SynthesisResult:
    """Результат синтеза: топ-темы для колонок/книги."""

    top_themes: list[str] = field(default_factory=list)
    column_ideas: list[str] = field(default_factory=list)
    narrative_shifts: list[str] = field(default_factory=list)
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        raise ValueError(
            "DASHSCOPE_API_KEY не установлен. Получите ключ: https://dashscope.console.aliyun.com/"
        )
    return key


async def _call_qwen(
    messages: list[dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
) -> str:
    """Вызов Qwen API (OpenAI-compatible, через aiohttp)."""
    import aiohttp

    api_key = _get_api_key()
    url = f"{DASHSCOPE_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2000,
    }

    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)
        ) as resp,
    ):
        if resp.status != 200:
            text = await resp.text()
            logger.warning("Qwen API error %d: %s", resp.status, text[:200])
            return ""
        data = await resp.json()
        content: str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content


CLASSIFICATION_PROMPT = (
    "Ты — аналитик трендов AI-индустрии."
    " Проанализируй посты с Reddit и извлеки сигналы.\n\n"
    "Для КАЖДОГО поста верни JSON-массив объектов с полями:\n"
    "- post_id: string (из входных данных)\n"
    "- pain_points: string[] (боли/проблемы; пусто если нет)\n"
    "- buying_intent: boolean (намерение купить/использовать AI-продукт)\n"
    "- business_relevance: int 1-10 (релевантность для бизнеса/enterprise AI)\n"
    "- book_relevance: int 1-10 (релевантность для книги"
    ' "Когда интеллект стал дешёвым")\n'
    "- themes: string[] (ключевые темы, 1-3)\n"
    "- summary: string (1 предложение)\n\n"
    "Верни ТОЛЬКО JSON-массив, без пояснений.\n\n"
    "Посты:\n{posts_json}"
)


SYNTHESIS_PROMPT = (
    'Ты — главный редактор книги "Когда интеллект стал дешёвым"'
    " (3 тома: Человек, Бизнес, Общество).\n\n"
    "На основе проанализированных сигналов из Reddit"
    " ({total_posts} постов, {date}):\n\n"
    "1. **top_themes**: Топ-5 тем (короткие формулировки)\n"
    "2. **column_ideas**: 3 идеи для колонок в РБК"
    " (конкретные углы подачи)\n"
    "3. **narrative_shifts**: Сдвиги в нарративе"
    " (что изменилось в восприятии AI)\n\n"
    "Верни JSON с полями top_themes, column_ideas,"
    " narrative_shifts (массивы строк).\n"
    "ТОЛЬКО JSON, без пояснений.\n\n"
    "Сигналы:\n{signals_json}"
)


async def analyze_posts(
    cards: list[PostCard],
    model: str = DEFAULT_MODEL,
) -> list[SignalCard]:
    """Анализирует посты батчами через Qwen API."""
    if not cards:
        return []

    signals: list[SignalCard] = []
    batches = [cards[i : i + BATCH_SIZE] for i in range(0, len(cards), BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches):
        posts_data = [
            {
                "post_id": c.post_id,
                "subreddit": c.subreddit,
                "title": c.title,
                "score": c.score,
                "num_comments": c.num_comments,
                "selftext": c.selftext[:500],
            }
            for c in batch
        ]

        prompt = CLASSIFICATION_PROMPT.format(
            posts_json=json.dumps(posts_data, ensure_ascii=False, indent=1)
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            response = await _call_qwen(messages, model=model)
            if not response:
                continue

            # Парсим JSON из ответа (может быть обёрнут в ```json ... ```)
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]

            parsed = json.loads(text)
            if not isinstance(parsed, list):
                parsed = [parsed]

            for item in parsed:
                signals.append(
                    SignalCard(
                        post_id=str(item.get("post_id", "")),
                        title=next(
                            (c.title for c in batch if c.post_id == item.get("post_id")), ""
                        ),
                        subreddit=next(
                            (c.subreddit for c in batch if c.post_id == item.get("post_id")), ""
                        ),
                        score=next((c.score for c in batch if c.post_id == item.get("post_id")), 0),
                        pain_points=item.get("pain_points", []),
                        buying_intent=bool(item.get("buying_intent", False)),
                        business_relevance=int(item.get("business_relevance", 0)),
                        book_relevance=int(item.get("book_relevance", 0)),
                        themes=item.get("themes", []),
                        summary=item.get("summary", ""),
                        model=model,
                    )
                )

            logger.info("LLM batch %d/%d: %d сигналов", batch_idx + 1, len(batches), len(parsed))

        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("LLM batch %d: parse error: %s", batch_idx + 1, exc)
            continue

        # Пауза между батчами (rate limit Qwen)
        if batch_idx < len(batches) - 1:
            await asyncio.sleep(1.0)

    return signals


async def synthesize(
    signals: list[SignalCard],
    snapshot_date: str,
    total_posts: int,
    model: str = SYNTHESIS_MODEL,
) -> SynthesisResult:
    """Синтез: топ-темы, идеи для колонок, сдвиги нарратива."""
    if not signals:
        return SynthesisResult(model=model)

    # Берём топ-30 по book_relevance для синтеза
    top_signals = sorted(signals, key=lambda s: s.book_relevance, reverse=True)[:30]
    signals_data = [
        {
            "title": s.title,
            "subreddit": s.subreddit,
            "themes": s.themes,
            "pain_points": s.pain_points,
            "book_relevance": s.book_relevance,
            "summary": s.summary,
        }
        for s in top_signals
    ]

    prompt = SYNTHESIS_PROMPT.format(
        total_posts=total_posts,
        date=snapshot_date,
        signals_json=json.dumps(signals_data, ensure_ascii=False, indent=1),
    )
    messages = [{"role": "user", "content": prompt}]

    try:
        response = await _call_qwen(messages, model=model, temperature=0.5)
        if not response:
            return SynthesisResult(model=model)

        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]

        parsed = json.loads(text)
        return SynthesisResult(
            top_themes=parsed.get("top_themes", []),
            column_ideas=parsed.get("column_ideas", []),
            narrative_shifts=parsed.get("narrative_shifts", []),
            model=model,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Synthesis parse error: %s", exc)
        return SynthesisResult(model=model)


def write_signals_jsonl(signals: list[SignalCard], path: Path) -> int:
    """Пишет signals.jsonl."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for sig in signals:
            f.write(sig.to_json() + "\n")
    logger.info("Записано %d сигналов → %s", len(signals), path)
    return len(signals)


def render_signals_report(
    signals: list[SignalCard],
    synthesis: SynthesisResult,
    snapshot_date: str,
) -> str:
    """Рендерит Markdown-секцию сигналов для отчёта."""
    lines = [
        f"## 🤖 LLM-анализ сигналов ({snapshot_date})",
        "",
        f"Проанализировано: {len(signals)} постов",
        "",
    ]

    if synthesis.top_themes:
        lines.append("### Топ-темы")
        for i, theme in enumerate(synthesis.top_themes, 1):
            lines.append(f"{i}. {theme}")
        lines.append("")

    if synthesis.column_ideas:
        lines.append("### Идеи для колонок")
        for idea in synthesis.column_ideas:
            lines.append(f"- {idea}")
        lines.append("")

    if synthesis.narrative_shifts:
        lines.append("### Сдвиги нарратива")
        for shift in synthesis.narrative_shifts:
            lines.append(f"- {shift}")
        lines.append("")

    # Топ по book_relevance
    top = sorted(signals, key=lambda s: s.book_relevance, reverse=True)[:10]
    if top:
        lines.append("### Топ-10 по релевантности для книги")
        lines.append("")
        lines.append("| # | Subreddit | Title | Book | Biz | Themes |")
        lines.append("|---|---|---|---|---|---|")
        for i, s in enumerate(top, 1):
            themes = ", ".join(s.themes[:2])
            lines.append(
                f"| {i} | r/{s.subreddit} | {s.title[:60]} | {s.book_relevance} "
                f"| {s.business_relevance} | {themes} |"
            )
        lines.append("")

    # Pain points
    all_pains = [p for s in signals for p in s.pain_points]
    if all_pains:
        lines.append("### Pain points (все)")
        for pain in all_pains[:15]:
            lines.append(f"- {pain}")
        lines.append("")

    return "\n".join(lines)
