"""Дашборд: интерактивный, с ссылками, по кластерам.

Сервится из FastAPI по GET /dashboard. Серверный рендер, без JS-фреймворков.
Тёмный editorial-стиль. Все посты — кликабельные ссылки на первоисточник.
"""

from __future__ import annotations

from typing import Any

_PAGE_TOP = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🧭 reddit-compass — трендовый радар</title>
<style>
:root {
  --bg: #0f0f1a;
  --fg: #e0e0e0;
  --accent: #4a9eff;
  --accent2: #ff6b6b;
  --green: #51cf66;
  --muted: #777;
  --card: #1a1a2e;
  --border: #2a2a4a;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, 'Segoe UI', Roboto, monospace;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.5;
  padding: 1.5rem;
  max-width: 1100px;
  margin: 0 auto;
}
h1 { color: var(--accent); font-size: 1.5rem; margin-bottom: 0.3rem; }
h2 {
  color: var(--fg); font-size: 1.1rem; margin: 2rem 0 0.7rem;
  border-bottom: 1px solid var(--border); padding-bottom: 0.4rem;
}
h2 a { color: var(--fg); text-decoration: none; }
.meta { color: var(--muted); font-size: 0.85rem; margin-bottom: 1rem; }
nav { margin: 1rem 0; display: flex; flex-wrap: wrap; gap: 0.5rem; }
nav a {
  background: var(--card); border: 1px solid var(--border); border-radius: 4px;
  padding: 0.3rem 0.7rem; font-size: 0.8rem; color: var(--accent); text-decoration: none;
}
nav a:hover { background: var(--border); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 0.8rem; margin: 1rem 0; }
.stat { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 0.8rem; text-align: center; }
.stat .num { font-size: 1.6rem; color: var(--accent); font-weight: bold; }
.stat .label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.post {
  display: flex; align-items: baseline; gap: 0.7rem;
  padding: 0.5rem 0; border-bottom: 1px solid var(--border);
}
.post:hover { background: rgba(74,158,255,0.05); }
.post .score { color: var(--accent); font-weight: bold; min-width: 50px; text-align: right; font-size: 0.85rem; }
.post .src { color: var(--muted); font-size: 0.75rem; min-width: 90px; }
.post a { color: var(--fg); text-decoration: none; font-size: 0.9rem; }
.post a:hover { color: var(--accent); text-decoration: underline; }
.post .comments { color: var(--muted); font-size: 0.75rem; margin-left: auto; }
.footer { margin-top: 2.5rem; color: var(--muted); font-size: 0.75rem; border-top: 1px solid var(--border); padding-top: 1rem; }
.footer a { color: var(--accent); }
</style>
</head>
<body>
<h1>🧭 reddit-compass</h1>
<p class="meta">Трендовый радар · {latest} · {total_posts} постов из {sources_count} источников</p>

<div class="stats">
  <div class="stat"><div class="num">{total_posts}</div><div class="label">Постов</div></div>
  <div class="stat"><div class="num">{reddit_count}</div><div class="label">Reddit</div></div>
  <div class="stat"><div class="num">{hn_count}</div><div class="label">HN</div></div>
  <div class="stat"><div class="num">{rss_count}</div><div class="label">RSS</div></div>
  <div class="stat"><div class="num">{ladder_count}</div><div class="label">Ladder</div></div>
  <div class="stat"><div class="num">{subreddits_count}</div><div class="label">Subreddits</div></div>
</div>

<nav>
  <a href="#status">📋 Статус</a>
  <a href="#sources">📚 Источники</a>
  <a href="#mega">🔥 Мега-тренды</a>
  <a href="#ai">🤖 AI/Tech</a>
  <a href="#surveillance">👁 Surveillance</a>
  <a href="#labor">💼 Труд</a>
  <a href="#business">🏪 Бизнес</a>
  <a href="#society">🌍 Общество</a>
  <a href="#hn">💬 HN</a>
  <a href="#rss">📡 СМИ RSS</a>
  <a href="#ladder">🪜 СМИ Ladder</a>
</nav>
"""

_PAGE_BOTTOM = """
<div class="footer">
  reddit-compass v0.2 · <a href="/docs">API</a> · <a href="/health">health</a>
  ·数据来源: Reddit + Hacker News + RSS (Guardian, Reuters, TechCrunch, Verge, Ars)
</div>
</body>
</html>"""

# Читаемые названия источников
SOURCE_LABELS = {
    "nytimes": "NYT",
    "washingtonpost": "Washington Post",
    "wired": "Wired",
    "time": "Time",
    "vanityfair": "Vanity Fair",
    "newyorker": "New Yorker",
    "americanbanker": "American Banker",
    "foxnews": "Fox News",
    "ft": "Financial Times",
    "bbc": "BBC",
    "guardian": "Guardian",
    "reuters": "Reuters",
    "techcrunch": "TechCrunch",
    "verge": "The Verge",
    "arstechnica": "Ars Technica",
    "usatoday": "USA Today",
    "foxbusiness": "Fox Business",
    "medium": "Medium",
    "hackernews": "Hacker News",
    "producthunt": "ProductHunt",
}


def _source_label(p: dict[str, Any]) -> str:
    """Читаемое название источника для поста."""
    source = p.get("source", "reddit")
    sub = p.get("subreddit", "")
    if source == "reddit":
        return f"r/{sub}"
    # Для ladder/rss: subreddit содержит имя источника
    label = SOURCE_LABELS.get(sub) or SOURCE_LABELS.get(source) or sub or source
    return str(label)


def _post_row(p: dict[str, Any]) -> str:
    """Одна строка поста с кликабельной ссылкой."""
    score = p.get("score", 0)
    title = p.get("title", "")[:80]
    source = p.get("source", "reddit")
    permalink = p.get("permalink", "")
    url = p.get("url", "")

    if source == "reddit" and permalink:
        link = f"https://www.reddit.com{permalink}"
    elif url:
        link = url
    else:
        link = "#"

    src_label = _source_label(p)
    comments = p.get("num_comments", 0)
    comments_str = f"💬{comments}" if comments else ""

    return (
        f'<div class="post">'
        f'<span class="score">{score}</span>'
        f'<span class="src">{src_label}</span>'
        f'<a href="{link}" target="_blank">{title}</a>'
        f'<span class="comments">{comments_str}</span>'
        f"</div>\n"
    )


def _status_icon(status: str) -> str:
    return {"ok": "✅", "error": "❌", "empty": "⚠️", "skipped": "⏭"}.get(status, "❓")


def _render_manifest(manifest: dict[str, Any] | None) -> str:
    """Панель статуса последнего запуска."""
    if not manifest:
        return (
            '<h2 id="status">📋 Статус запуска</h2>\n'
            '<p class="meta">Манифест не найден — запуск ещё не производился '
            "или данные не синхронизированы.</p>\n"
        )

    status = manifest.get("status", "unknown")
    status_label = {
        "done": "✅ завершён",
        "partial": "⚠️ частично",
        "running": "⏳ выполняется",
    }.get(status, status)
    started = manifest.get("started_at", "—")[:19].replace("T", " ")
    duration = manifest.get("duration_sec", 0)

    html = '<h2 id="status">📋 Статус запуска</h2>\n'
    html += (
        f'<p class="meta">Запуск: <b>{started} UTC</b> · '
        f"статус: <b>{status_label}</b> · длительность: <b>{duration:.0f}с</b> · "
        f"всего: <b>{manifest.get('total_items', 0)}</b> items</p>\n"
    )
    html += "<table>\n<tr><th>Источник</th><th>Статус</th><th>Собрано</th><th>Время</th><th>Заметка</th></tr>\n"
    for s in manifest.get("sources", []):
        icon = _status_icon(s.get("status", ""))
        errors = "; ".join(s.get("errors", [])[:2])
        note = s.get("note", "")
        note_full = f"{note} {errors}".strip()
        html += (
            f"<tr><td>{s.get('name', '')}</td>"
            f"<td>{icon} {s.get('status', '')}</td>"
            f"<td class='score'>{s.get('count', 0)}</td>"
            f"<td>{s.get('duration_sec', 0):.0f}с</td>"
            f"<td class='sub'>{note_full}</td></tr>\n"
        )
    html += "</table>\n"
    return html


def load_posts_from_snapshot(snap_dir: Any) -> list[dict[str, Any]]:
    """Загружает посты из JSONL-файлов snapshot-директории."""
    import json as _json
    from pathlib import Path

    snap = Path(snap_dir)
    posts: list[dict[str, Any]] = []
    source_map = {
        "posts.jsonl": "reddit",
        "hackernews.jsonl": "hackernews",
        "rss.jsonl": "rss",
        "ladder.jsonl": "ladder",
        "producthunt.jsonl": "producthunt",
    }
    for fname, source in source_map.items():
        fp = snap / fname
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                p = _json.loads(line)
                p["source"] = source
                posts.append(p)
            except _json.JSONDecodeError:
                continue
    return posts


def render_dashboard(
    stats: dict[str, Any], posts: list[dict[str, Any]], manifest: dict[str, Any] | None = None
) -> str:
    """Рендерит интерактивный дашборд с ссылками по кластерам."""

    # Разделяем по источникам (определяем по subreddit или source)
    def _get_source(p: dict[str, Any]) -> str:
        sub = p.get("subreddit", "")
        src = p.get("source", "")
        if sub == "hackernews" or src == "hackernews":
            return "hackernews"
        if sub == "producthunt" or src == "producthunt":
            return "producthunt"
        if src == "ladder" or p.get("monitoring_type") == "ladder":
            return "ladder"
        if src == "rss" or p.get("monitoring_type") == "rss":
            return "rss"
        return "reddit"

    reddit = [p for p in posts if _get_source(p) == "reddit"]
    hn = [p for p in posts if _get_source(p) == "hackernews"]
    rss = [p for p in posts if _get_source(p) == "rss"]
    ladder = [p for p in posts if _get_source(p) == "ladder"]

    # Кластеры Reddit
    ai_subs = {
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
    }
    labor_subs = {"jobs", "cscareerquestions"}
    business_subs = {"Entrepreneur", "smallbusiness"}
    society_subs = {"AskReddit", "changemyview"}
    surveillance_kw = ["flock", "camera", "surveillance", "privacy", "palantir", "data center"]

    ai_posts = sorted(
        [p for p in reddit if p.get("subreddit") in ai_subs],
        key=lambda x: x.get("score", 0),
        reverse=True,
    )
    surv_posts = sorted(
        [
            p
            for p in reddit
            if p.get("subreddit") == "technology"
            and any(kw in p.get("title", "").lower() for kw in surveillance_kw)
        ],
        key=lambda x: x.get("score", 0),
        reverse=True,
    )
    labor_posts = sorted(
        [p for p in reddit if p.get("subreddit") in labor_subs],
        key=lambda x: x.get("score", 0),
        reverse=True,
    )
    biz_posts = sorted(
        [p for p in reddit if p.get("subreddit") in business_subs],
        key=lambda x: x.get("score", 0),
        reverse=True,
    )
    society_posts = sorted(
        [p for p in reddit if p.get("subreddit") in society_subs],
        key=lambda x: x.get("score", 0),
        reverse=True,
    )

    # Мега-тренды: топ через все
    all_sorted = sorted(posts, key=lambda x: x.get("score", 0), reverse=True)

    n_subs = len({p.get("subreddit", "") for p in reddit})

    html = _PAGE_TOP
    html = html.replace("{latest}", str(stats.get("latest_snapshot", "—")))
    html = html.replace("{total_posts}", str(stats.get("total_posts", 0)))
    html = html.replace("{sources_count}", "3")
    html = html.replace("{reddit_count}", str(len(reddit)))
    html = html.replace("{hn_count}", str(len(hn)))
    html = html.replace("{rss_count}", str(len(rss)))
    html = html.replace("{ladder_count}", str(len(ladder)))
    html = html.replace("{subreddits_count}", str(n_subs))

    # Статус запуска (манифест)
    html += _render_manifest(manifest)

    # Сводка проверенных источников (какие СМИ + сколько ссылок)
    source_counts: dict[str, int] = {}
    for p in posts:
        label = _source_label(p)
        source_counts[label] = source_counts.get(label, 0) + 1
    if source_counts:
        html += '<h2 id="sources">📚 Проверенные источники</h2>\n'
        html += '<p class="meta">Сколько материалов собрано из каждого источника:</p>\n'
        html += "<table>\n<tr><th>Источник</th><th>Материалов</th></tr>\n"
        for label, count in sorted(source_counts.items(), key=lambda x: -x[1]):
            html += f"<tr><td>{label}</td><td class='score'>{count}</td></tr>\n"
        html += f"<tr><td><b>Итого</b></td><td class='score'><b>{len(posts)}</b></td></tr>\n"
        html += "</table>\n"

    # Мега-тренды
    html += '<h2 id="mega">🔥 Мега-тренды (топ через все источники)</h2>\n'
    for p in all_sorted[:20]:
        html += _post_row(p)

    # AI
    if ai_posts:
        html += '<h2 id="ai">🤖 AI и технологии</h2>\n'
        for p in ai_posts[:15]:
            html += _post_row(p)

    # Surveillance
    if surv_posts:
        html += '<h2 id="surveillance">👁 Surveillance и приватность</h2>\n'
        for p in surv_posts[:10]:
            html += _post_row(p)

    # Труд
    if labor_posts:
        html += '<h2 id="labor">💼 Труд и карьера</h2>\n'
        for p in labor_posts[:10]:
            html += _post_row(p)

    # Бизнес
    if biz_posts:
        html += '<h2 id="business">🏪 Бизнес</h2>\n'
        for p in biz_posts[:10]:
            html += _post_row(p)

    # Общество
    if society_posts:
        html += '<h2 id="society">🌍 Общество и политика</h2>\n'
        for p in society_posts[:10]:
            html += _post_row(p)

    # HN
    if hn:
        hn_sorted = sorted(hn, key=lambda x: x.get("score", 0), reverse=True)
        html += '<h2 id="hn">💬 Hacker News</h2>\n'
        for p in hn_sorted[:15]:
            html += _post_row(p)

    # RSS
    if rss:
        html += '<h2 id="rss">📡 СМИ: RSS (BBC, Guardian, Reuters, TechCrunch, Verge, Ars)</h2>\n'
        for p in rss[:15]:
            html += _post_row(p)

    # Ladder (paywall СМИ)
    if ladder:
        html += '<h2 id="ladder">🪜 СМИ: Ladder (NYT, WaPo, FT, Wired, Time, VF, New Yorker)</h2>\n'
        for p in ladder[:20]:
            html += _post_row(p)

    html += _PAGE_BOTTOM
    return html
