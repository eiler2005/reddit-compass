"""LLM-анализ сигналов: Qwen API → pain points, business relevance, темы для колонок.

Вход: posts.jsonl (snapshot). Выход: signals.jsonl + секция в отчёте.
API: QwenCloud OpenAI-compatible (Qwen). Ключ: DASHSCOPE_API_KEY (pay-as-you-go).
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

BATCH_SIZE = 20  # постов на один LLM-запрос (flash тянет 20)
MAX_CONCURRENT = 3

# Конфигурации провайдеров (ключ → base_url + модели)
_TOKEN_PLAN_URL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
_DASHSCOPE_INTL_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# Модельная пирамида (цена/качество):
#   qwen3.8-max-preview — синтез (сложное, мало вызовов, скидка 17:00–03:00 МСК)
#   qwen3.6-flash       — классификация постов (простое извлечение, дёшево)
#   qwen3.7-plus        — зарезервирована для задач средней сложности
_MODEL_SYNTHESIS = "qwen3.8-max-preview"  # сложное → дорогая модель
_MODEL_CLASSIFY = "qwen3.6-flash"  # массовая классификация → самая дешёвая
_MODEL_CHEAP = "qwen3.6-flash"  # простое → самая дешёвая


def _get_api_config() -> tuple[str, str, str, str]:
    """Возвращает (api_key, base_url, classification_model, synthesis_model).

    Приоритет: QWEN_TOKEN_PLAN_KEY (token-plan) → QWEN_Pay_As_You_Go / DASHSCOPE_API_KEY.
    Пирамида моделей: классификация=qwen3.7-plus, синтез=qwen3.8-max-preview.
    """
    # Token-plan ключ (пирамида qwen3.7-plus / qwen3.8-max-preview)
    token_plan_key = os.environ.get("QWEN_TOKEN_PLAN_KEY", "")
    if token_plan_key:
        return token_plan_key, _TOKEN_PLAN_URL, _MODEL_CLASSIFY, _MODEL_SYNTHESIS
    # Pay-as-you-go / стандартный ключ (qwen-plus/max)
    for var in ("DASHSCOPE_API_KEY", "QWEN_Pay_As_You_Go_PLAN_KEY"):
        key = os.environ.get(var, "")
        if key:
            base_url = os.environ.get("DASHSCOPE_BASE_URL", _DASHSCOPE_INTL_URL)
            return key, base_url, "qwen-plus", "qwen-max"
    raise ValueError(
        "Ключ Qwen не установлен. Задайте QWEN_TOKEN_PLAN_KEY "
        "или DASHSCOPE_API_KEY. Получить: https://home.qwencloud.com/api-keys"
    )


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

    top_themes: list[Any] = field(default_factory=list)  # str или {theme, explanation}
    column_ideas: list[str] = field(default_factory=list)
    narrative_shifts: list[str] = field(default_factory=list)
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_api_key() -> str:
    """Возвращает API-ключ (для обратной совместимости)."""
    return _get_api_config()[0]


async def _call_qwen(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.3,
) -> str:
    """Вызов Qwen API (OpenAI-compatible, через aiohttp)."""
    import aiohttp

    api_key, base_url, default_model, _ = _get_api_config()
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model or default_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 8000,
    }

    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=300)
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
    "- buying_intent: boolean (намерение купить/использовать продукт)\n"
    "- business_relevance: int 1-10 (релевантность для бизнеса)\n"
    "- book_relevance: int 1-10 (релевантность для книги"
    ' "Когда интеллект стал дешёвым")\n'
    "- themes: string[] (КОНКРЕТНЫЕ темы, 1-3; не общие категории"
    ' вроде "technology" или "politics", а конкретные сюжеты:'
    ' "Flock cameras vandalism", "Oracle AI layoffs")\n'
    "- summary: string (1 предложение)\n\n"
    "Верни ТОЛЬКО JSON-массив, без пояснений.\n\n"
    "Посты:\n{posts_json}"
)


SYNTHESIS_PROMPT = (
    'Ты — главный редактор книги "Когда интеллект стал дешёвым"'
    " (3 тома: Человек, Бизнес, Общество). Пиши на русском.\n\n"
    "На основе проанализированных сигналов из Reddit и СМИ"
    " ({total_posts} постов, {date}):\n\n"
    "1. **top_themes**: Топ-5 КОНКРЕТНЫХ сюжетов дня.\n"
    "   ⛔ ЗАПРЕЩЕНЫ общие категории ЛЮБОГО топика:"
    " «AI ethics», «technology», «politics», «economics»,"
    " «privacy», «labor market», «societal impact».\n"
    "   ✅ Только конкретные события и сюжеты, привязанные к постам.\n"
    "   Формула: [КТО] + [ЧТО СДЕЛАЛ] + [ПОЧЕМУ ЭТО ВАЖНО].\n"
    "   Примеры: «AI-агенты выходят из-под контроля: побег из песочницы"
    " OpenAI и взлом Hugging Face», «Flock cameras: американцы разбивают"
    " камеры слежения, а те прячутся в знаки», «Oracle увольняет 21 000"
    " после провальной AI-ставки».\n"
    " Для каждой темы дай:\n"
    '   - "theme": конкретная формулировка сюжета (на русском)\n'
    '   - "explanation": 2-3 предложения — что происходит,'
    " какие посты это показывают, почему важно для книги/колонок\n"
    "2. **column_ideas**: 3 идеи для колонок в РБК"
    " (конкретные углы подачи, привязанные к сюжетам дня, на русском)\n"
    "3. **narrative_shifts**: Сдвиги в нарративе"
    " (что КОНКРЕТНО изменилось в восприятии AI за эту неделю, на русском)\n\n"
    "Верни JSON: top_themes (массив объектов {{theme, explanation}}),"
    " column_ideas (массив строк), narrative_shifts (массив строк).\n"
    "ТОЛЬКО JSON, без пояснений вне JSON.\n\n"
    "Сигналы:\n{signals_json}"
)


async def analyze_posts(
    cards: list[PostCard],
    model: str | None = None,
) -> list[SignalCard]:
    """Анализирует посты батчами через Qwen API."""
    if not cards:
        return []

    _, _, classification_model, _ = _get_api_config()
    model = model or classification_model

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
                "selftext": c.selftext[:200],
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
        except TimeoutError:
            logger.warning("LLM batch %d: timeout, retry через 10с...", batch_idx + 1)
            await asyncio.sleep(10)
            try:
                response = await _call_qwen(messages, model=model)
                if response:
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
                                    (c.title for c in batch if c.post_id == item.get("post_id")),
                                    "",
                                ),
                                subreddit=next(
                                    (
                                        c.subreddit
                                        for c in batch
                                        if c.post_id == item.get("post_id")
                                    ),
                                    "",
                                ),
                                score=next(
                                    (c.score for c in batch if c.post_id == item.get("post_id")), 0
                                ),
                                pain_points=item.get("pain_points", []),
                                buying_intent=bool(item.get("buying_intent", False)),
                                business_relevance=int(item.get("business_relevance", 0)),
                                book_relevance=int(item.get("book_relevance", 0)),
                                themes=item.get("themes", []),
                                summary=item.get("summary", ""),
                                model=model,
                            )
                        )
                    logger.info(
                        "LLM batch %d/%d (retry): %d сигналов",
                        batch_idx + 1,
                        len(batches),
                        len(parsed),
                    )
            except Exception:
                logger.warning("LLM batch %d: retry не удался, пропускаем", batch_idx + 1)
            continue

        # Пауза между батчами (rate limit Qwen)
        if batch_idx < len(batches) - 1:
            await asyncio.sleep(1.0)

    return signals


async def synthesize(
    signals: list[SignalCard],
    snapshot_date: str,
    total_posts: int,
    model: str | None = None,
) -> SynthesisResult:
    """Синтез: топ-темы, идеи для колонок, сдвиги нарратива."""
    _, _, _, synthesis_model = _get_api_config()
    model = model or synthesis_model
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
            if isinstance(theme, dict):
                title = theme.get("theme", "")
                explanation = theme.get("explanation", "")
                lines.append(f"{i}. **{title}**")
                if explanation:
                    lines.append(f"   {explanation}")
            else:
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


def render_trend_radar(
    snap_dir: Path,
    snapshot_date: str,
    top_n: int = 10,
) -> str:
    """Генерирует полный трендовый радар по кластерам с ссылками.

    Структура: AI/Tech → Surveillance → Труд → Бизнес → Общество → HN → RSS.
    Каждый кластер: топ постов с URL.
    """
    import json as _json

    lines: list[str] = [
        f"# 🧭 Трендовый радар: {snapshot_date}",
        "",
    ]

    # Загружаем данные
    reddit_posts: list[dict[str, Any]] = []
    hn_posts: list[dict[str, Any]] = []
    rss_posts: list[dict[str, Any]] = []
    ladder_posts: list[dict[str, Any]] = []
    ph_posts: list[dict[str, Any]] = []

    posts_file = snap_dir / "posts.jsonl"
    if posts_file.exists():
        for line in posts_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                reddit_posts.append(_json.loads(line))

    hn_file = snap_dir / "hackernews.jsonl"
    if hn_file.exists():
        for line in hn_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                hn_posts.append(_json.loads(line))

    rss_file = snap_dir / "rss.jsonl"
    if rss_file.exists():
        for line in rss_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rss_posts.append(_json.loads(line))

    ladder_file = snap_dir / "ladder.jsonl"
    if ladder_file.exists():
        for line in ladder_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ladder_posts.append(_json.loads(line))

    ph_file = snap_dir / "producthunt.jsonl"
    if ph_file.exists():
        for line in ph_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ph_posts.append(_json.loads(line))

    total = len(reddit_posts) + len(hn_posts) + len(rss_posts) + len(ladder_posts) + len(ph_posts)
    n_subs = len({p.get("subreddit", "") for p in reddit_posts})

    lines.append(
        f"> **{total} единиц** | Reddit: {len(reddit_posts)} ({n_subs} sub) "
        f"| HN: {len(hn_posts)} | RSS: {len(rss_posts)} "
        f"| Ladder: {len(ladder_posts)} | PH: {len(ph_posts)}"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Мега-тренды: топ через ВСЕ источники ─────────────────────────────────

    all_items: list[dict[str, Any]] = []
    for p in reddit_posts:
        permalink = p.get("permalink", "")
        url = f"https://www.reddit.com{permalink}" if permalink else p.get("url", "")
        all_items.append(
            {
                "score": p.get("score", 0),
                "title": p.get("title", ""),
                "source": f"r/{p.get('subreddit', '')}",
                "url": url,
            }
        )
    for p in hn_posts:
        hn_url = p.get("url") or f"https://news.ycombinator.com/item?id={p.get('post_id', '')}"
        all_items.append(
            {
                "score": p.get("score", 0),
                "title": p.get("title", ""),
                "source": "HN",
                "url": hn_url,
            }
        )
    for p in ladder_posts:
        all_items.append(
            {
                "score": p.get("score", 0),
                "title": p.get("title", ""),
                "source": p.get("subreddit", "ladder"),
                "url": p.get("url", ""),
            }
        )
    for p in ph_posts:
        all_items.append(
            {
                "score": p.get("score", 0),
                "title": p.get("title", ""),
                "source": "PH",
                "url": p.get("url", ""),
            }
        )

    all_items.sort(key=lambda x: x["score"], reverse=True)
    if all_items:
        lines.append("## 🔥 Мега-тренды (топ через все источники)")
        lines.append("")
        lines.append("| # | Score | Источник | Title | Ссылка |")
        lines.append("|---|---|---|---|---|")
        for i, item in enumerate(all_items[:15], 1):
            title = item["title"][:65]
            lines.append(
                f"| {i} | {item['score']} | {item['source']} | {title} | [→]({item['url']}) |"
            )
        lines.append("")

    # ── Кластеры Reddit ──────────────────────────────────────────────────────

    clusters: list[tuple[str, str, list[str]]] = [
        (
            "🤖 AI и технологии",
            "ai_tech",
            [
                "artificial",
                "singularity",
                "ChatGPT",
                "AI_Agents",
                "LocalLLaMA",
                "MachineLearning",
                "vibecoding",
                "cursor",
                "LangChain",
                "AutoGPT",
                "crewAI",
            ],
        ),
        (
            "👁 Surveillance и приватность",
            "surveillance",
            [
                "technology",  # фильтруется по ключевым словам
            ],
        ),
        (
            "💼 Труд и карьера",
            "labor",
            [
                "jobs",
                "cscareerquestions",
            ],
        ),
        (
            "🏪 Бизнес и предпринимательство",
            "business",
            [
                "Entrepreneur",
                "smallbusiness",
            ],
        ),
        (
            "🌍 Общество и политика",
            "society",
            [
                "AskReddit",
                "changemyview",
            ],
        ),
    ]

    surveillance_keywords = [
        "flock",
        "camera",
        "surveillance",
        "privacy",
        "track",
        "palantir",
        "ice",
        "data center",
        "eminent domain",
    ]

    for cluster_name, _cluster_id, subreddits in clusters:
        if _cluster_id == "surveillance":
            # Специальный фильтр: r/technology + ключевые слова
            cluster_posts = [
                p
                for p in reddit_posts
                if p.get("subreddit") == "technology"
                and any(kw in p.get("title", "").lower() for kw in surveillance_keywords)
            ]
        elif _cluster_id == "ai_tech":
            cluster_posts = [p for p in reddit_posts if p.get("subreddit") in subreddits]
        else:
            cluster_posts = [p for p in reddit_posts if p.get("subreddit") in subreddits]

        if not cluster_posts:
            continue

        cluster_posts.sort(key=lambda p: p.get("score", 0), reverse=True)
        lines.append(f"## {cluster_name}")
        lines.append("")
        lines.append("| # | Score | r/ | Title | Ссылка |")
        lines.append("|---|---|---|---|---|")
        for i, p in enumerate(cluster_posts[:top_n], 1):
            permalink = p.get("permalink", "")
            url = f"https://www.reddit.com{permalink}" if permalink else p.get("url", "")
            title = p.get("title", "")[:65]
            sub = p.get("subreddit", "")
            lines.append(f"| {i} | {p.get('score', 0)} | {sub} | {title} | [→]({url}) |")
        lines.append("")

    # ── r/technology: остальное (не AI, не surveillance) ─────────────────────

    tech_other = [
        p
        for p in reddit_posts
        if p.get("subreddit") == "technology"
        and not any(kw in p.get("title", "").lower() for kw in surveillance_keywords)
        and not any(w in p.get("title", "").lower() for w in ["ai", "gpt", "llm", "openai"])
    ]
    if tech_other:
        tech_other.sort(key=lambda p: p.get("score", 0), reverse=True)
        lines.append("## 📱 Технологии (общее)")
        lines.append("")
        lines.append("| # | Score | Title | Ссылка |")
        lines.append("|---|---|---|---|")
        for i, p in enumerate(tech_other[:top_n], 1):
            permalink = p.get("permalink", "")
            url = f"https://www.reddit.com{permalink}" if permalink else p.get("url", "")
            title = p.get("title", "")[:70]
            lines.append(f"| {i} | {p.get('score', 0)} | {title} | [→]({url}) |")
        lines.append("")

    # ── Hacker News ──────────────────────────────────────────────────────────

    if hn_posts:
        hn_posts.sort(key=lambda p: p.get("score", 0), reverse=True)
        lines.append("## 💬 Hacker News (голос разработчика)")
        lines.append("")
        lines.append("| # | Score | Title | Ссылка |")
        lines.append("|---|---|---|---|")
        for i, p in enumerate(hn_posts[:top_n], 1):
            url = p.get("url") or f"https://news.ycombinator.com/item?id={p.get('post_id', '')}"
            title = p.get("title", "")[:70]
            lines.append(f"| {i} | {p.get('score', 0)} | {title} | [→]({url}) |")
        lines.append("")

    # ── RSS (СМИ) ────────────────────────────────────────────────────────────

    if rss_posts:
        # Разделяем на AI и не-AI
        rss_ai = [
            p
            for p in rss_posts
            if any(
                w in p.get("title", "").lower() for w in ["ai", "gpt", "llm", "openai", "anthropic"]
            )
        ]
        rss_other = [p for p in rss_posts if p not in rss_ai]

        if rss_ai:
            lines.append("## 📡 СМИ: AI и технологии")
            lines.append("")
            lines.append("| # | Источник | Title | Ссылка |")
            lines.append("|---|---|---|---|")
            for i, p in enumerate(rss_ai[:top_n], 1):
                title = p.get("title", "")[:65]
                url = p.get("url", "")
                lines.append(f"| {i} | {p.get('subreddit', '')} | {title} | [→]({url}) |")
            lines.append("")

        if rss_other:
            lines.append("## 📡 СМИ: общее")
            lines.append("")
            lines.append("| # | Источник | Title | Ссылка |")
            lines.append("|---|---|---|---|")
            for i, p in enumerate(rss_other[:top_n], 1):
                title = p.get("title", "")[:65]
                url = p.get("url", "")
                lines.append(f"| {i} | {p.get('subreddit', '')} | {title} | [→]({url}) |")
            lines.append("")

    # ── Ladder (paywall СМИ) ────────────────────────────────────────────────

    if ladder_posts:
        ladder_ai = [
            p
            for p in ladder_posts
            if any(
                w in p.get("title", "").lower() for w in ["ai", "gpt", "llm", "openai", "anthropic"]
            )
        ]
        ladder_other = [p for p in ladder_posts if p not in ladder_ai]

        if ladder_ai:
            lines.append("## 🪜 СМИ (paywall): AI и технологии")
            lines.append("")
            lines.append("| # | Источник | Title | Ссылка |")
            lines.append("|---|---|---|---|")
            for i, p in enumerate(ladder_ai[:top_n], 1):
                title = p.get("title", "")[:65]
                url = p.get("url", "")
                lines.append(f"| {i} | {p.get('subreddit', '')} | {title} | [→]({url}) |")
            lines.append("")

        if ladder_other:
            lines.append("## 🪜 СМИ (paywall): общее")
            lines.append("")
            lines.append("| # | Источник | Title | Ссылка |")
            lines.append("|---|---|---|---|")
            for i, p in enumerate(ladder_other[:top_n], 1):
                title = p.get("title", "")[:65]
                url = p.get("url", "")
                lines.append(f"| {i} | {p.get('subreddit', '')} | {title} | [→]({url}) |")
            lines.append("")

    # ── ProductHunt ──────────────────────────────────────────────────────────

    if ph_posts:
        ph_posts.sort(key=lambda p: p.get("score", 0), reverse=True)
        lines.append("## 🚀 ProductHunt")
        lines.append("")
        lines.append("| # | Score | Title | Ссылка |")
        lines.append("|---|---|---|---|")
        for i, p in enumerate(ph_posts[:top_n], 1):
            title = p.get("title", "")[:70]
            url = p.get("url", "")
            lines.append(f"| {i} | {p.get('score', 0)} | {title} | [→]({url}) |")
        lines.append("")

    # ── Footer ───────────────────────────────────────────────────────────────

    lines.append("---")
    lines.append("")
    lines.append(
        f"*reddit-compass v0.2 | {snapshot_date} | "
        f"{total} единиц из {n_subs} сабреддитов + HN + RSS + Ladder + PH*"
    )

    return "\n".join(lines)
