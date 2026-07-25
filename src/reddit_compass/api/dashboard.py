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
<div class="topnav" style="margin-bottom:1rem;padding-bottom:0.5rem;border-bottom:1px solid #2a2a4a;">
  <a href="/dashboard" style="color:#4a9eff;margin-right:1.2rem;">🧭 Дашборд</a>
  <a href="/runs" style="color:#4a9eff;margin-right:1.2rem;">📁 Запуски</a>
  <a href="/docs" style="color:#4a9eff;">📖 API</a>
</div>
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
  <a href="#themes">🎯 Темы</a>
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


def load_signals_from_snapshot(snap_dir: Any) -> list[dict[str, Any]]:
    """Загружает LLM-сигналы из signals.jsonl."""
    import json as _json
    from pathlib import Path

    fp = Path(snap_dir) / "signals.jsonl"
    if not fp.exists():
        return []
    signals: list[dict[str, Any]] = []
    for line in fp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            signals.append(_json.loads(line))
        except _json.JSONDecodeError:
            continue
    return signals


def render_themes_section(
    signals: list[dict[str, Any]], posts_by_id: dict[str, dict[str, Any]]
) -> str:
    """Секция 'Темы и посты': группировка постов по LLM-темам."""
    if not signals:
        return ""

    # Группируем посты по темам
    theme_posts: dict[str, list[dict[str, Any]]] = {}
    for sig in signals:
        post_id = sig.get("post_id", "")
        post = posts_by_id.get(post_id)
        if not post:
            continue
        for theme in sig.get("themes", []):
            theme_posts.setdefault(theme, []).append(post)

    if not theme_posts:
        return ""

    # Сортируем темы по числу постов
    sorted_themes = sorted(theme_posts.items(), key=lambda x: -len(x[1]))

    html = '<h2 id="themes">🎯 Темы и посты (LLM-разметка)</h2>\n'
    html += '<p class="meta">Посты, сгруппированные по темам, выделенным LLM:</p>\n'
    for theme, tposts in sorted_themes[:15]:
        tposts_sorted = sorted(tposts, key=lambda p: p.get("score", 0), reverse=True)
        html += f"<details><summary><b>{theme}</b> ({len(tposts)} постов)</summary>\n"
        for p in tposts_sorted[:8]:
            html += _post_row(p)
        html += "</details>\n"
    return html


def render_dashboard(
    stats: dict[str, Any],
    posts: list[dict[str, Any]],
    manifest: dict[str, Any] | None = None,
    signals: list[dict[str, Any]] | None = None,
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

    # Сводка проверенных источников (все ожидаемые + честный статус)
    source_counts: dict[str, int] = {}
    for p in posts:
        label = _source_label(p)
        source_counts[label] = source_counts.get(label, 0) + 1

    # Reddit считаем отдельно (r/*)
    reddit_total = sum(c for lbl, c in source_counts.items() if lbl.startswith("r/"))
    # Полный список ожидаемых СМИ
    expected_media = [
        "Hacker News",
        "ProductHunt",
        "BBC",
        "Guardian",
        "Reuters",
        "TechCrunch",
        "The Verge",
        "Ars Technica",
        "USA Today",
        "Fox Business",
        "Medium",
        "NYT",
        "Washington Post",
        "Financial Times",
        "Wired",
        "Time",
        "Vanity Fair",
        "New Yorker",
        "American Banker",
        "Fox News",
    ]

    html += '<h2 id="sources">📚 Проверенные источники</h2>\n'
    html += '<p class="meta">Полный список: что проверено и сколько собрано:</p>\n'
    html += "<table>\n<tr><th>Источник</th><th>Статус</th><th>Материалов</th></tr>\n"
    html += (
        f"<tr><td>Reddit (18 сабреддитов)</td>"
        f"<td>{'✅' if reddit_total else '❌ не собрано'}</td>"
        f"<td class='score'>{reddit_total or '—'}</td></tr>\n"
    )
    for src in expected_media:
        count = source_counts.get(src, 0)
        status = "✅" if count else "❌ не собрано"
        html += f"<tr><td>{src}</td><td>{status}</td><td class='score'>{count or '—'}</td></tr>\n"
    html += f"<tr><td><b>Итого</b></td><td></td><td class='score'><b>{len(posts)}</b></td></tr>\n"
    html += "</table>\n"

    # Темы и посты (LLM-разметка)
    if signals:
        posts_by_id = {p.get("post_id", ""): p for p in posts}
        html += render_themes_section(signals, posts_by_id)

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


# ── Trend Radar: полноценная страница с LLM-аналитикой ─────────────────────

_RADAR_CSS = """
:root {
  --bg: #0f0f1a; --fg: #e0e0e0; --accent: #4a9eff; --accent2: #ff6b6b;
  --green: #51cf66; --muted: #777; --card: #1a1a2e; --border: #2a2a4a;
  --gold: #ffd43b;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, 'Segoe UI', Roboto, monospace;
  background: var(--bg); color: var(--fg); line-height: 1.6;
  padding: 1.5rem; max-width: 1100px; margin: 0 auto;
}
h1 { color: var(--accent); font-size: 1.5rem; margin-bottom: 0.3rem; }
h2 {
  color: var(--fg); font-size: 1.15rem; margin: 2rem 0 0.7rem;
  border-bottom: 1px solid var(--border); padding-bottom: 0.4rem;
}
h3 { color: var(--accent); font-size: 1rem; margin: 1.2rem 0 0.5rem; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
.meta { color: var(--muted); font-size: 0.85rem; margin-bottom: 1rem; }
.topnav { margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); }
.topnav a { margin-right: 1.2rem; }

/* LLM Analysis cards */
.theme-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  padding: 1rem 1.2rem; margin: 0.7rem 0; border-left: 3px solid var(--accent);
}
.theme-card .theme-title { font-weight: bold; font-size: 1rem; color: var(--fg); }
.theme-card .theme-expl { color: var(--muted); font-size: 0.88rem; margin-top: 0.4rem; }
.idea-list { list-style: none; padding: 0; }
.idea-list li {
  background: var(--card); border: 1px solid var(--border); border-radius: 6px;
  padding: 0.6rem 1rem; margin: 0.4rem 0; font-size: 0.9rem;
}
.idea-list li::before { content: "💡 "; }
.shift-list { list-style: none; padding: 0; }
.shift-list li {
  padding: 0.4rem 0; border-bottom: 1px solid var(--border); font-size: 0.9rem;
}
.shift-list li::before { content: "🔄 "; }
.pain-grid {
  display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.5rem 0;
}
.pain-tag {
  background: rgba(255,107,107,0.12); border: 1px solid rgba(255,107,107,0.3);
  border-radius: 4px; padding: 0.2rem 0.6rem; font-size: 0.8rem; color: var(--accent2);
}

/* Stats row */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.8rem; margin: 1rem 0; }
.stat { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 0.8rem; text-align: center; }
.stat .num { font-size: 1.5rem; color: var(--accent); font-weight: bold; }
.stat .label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }

/* Tables */
table { width: 100%; border-collapse: collapse; margin: 0.5rem 0 1rem; font-size: 0.85rem; }
th { color: var(--muted); text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); font-weight: normal; }
td { padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); }
tr:hover { background: rgba(74,158,255,0.05); }
.score { color: var(--accent); font-weight: bold; }
.sub { color: var(--muted); font-size: 0.8rem; }

/* Post rows */
.post { display: flex; align-items: baseline; gap: 0.7rem; padding: 0.4rem 0; border-bottom: 1px solid var(--border); }
.post:hover { background: rgba(74,158,255,0.05); }
.post .score { min-width: 50px; text-align: right; font-size: 0.85rem; }
.post .src { color: var(--muted); font-size: 0.75rem; min-width: 90px; }
.post a { color: var(--fg); font-size: 0.9rem; } .post a:hover { color: var(--accent); }

.footer { margin-top: 2.5rem; color: var(--muted); font-size: 0.75rem; border-top: 1px solid var(--border); padding-top: 1rem; }
"""


def _parse_signals_report_sections(content: str) -> dict[str, Any]:
    """Парсит signals-report.md на секции для структурированного рендера."""
    import re

    sections: dict[str, Any] = {
        "top_themes": [],
        "column_ideas": [],
        "narrative_shifts": [],
        "pain_points": [],
    }

    current_section = ""
    current_theme: dict[str, str] | None = None

    for line in content.splitlines():
        stripped = line.strip()

        if stripped.startswith("### Топ-темы"):
            current_section = "themes"
            continue
        elif stripped.startswith("### Идеи для колонок"):
            current_section = "ideas"
            continue
        elif stripped.startswith("### Сдвиги нарратива"):
            current_section = "shifts"
            continue
        elif stripped.startswith("### Pain points"):
            current_section = "pains"
            continue
        elif stripped.startswith("### Топ-10"):
            current_section = "top10"
            continue
        elif stripped.startswith("## ") or stripped.startswith("---"):
            current_section = ""
            continue

        if current_section == "themes":
            # "1. **Theme title**" or "   explanation"
            m = re.match(r"^\d+\.\s+\*\*(.+?)\*\*", stripped)
            if m:
                if current_theme:
                    sections["top_themes"].append(current_theme)
                current_theme = {"theme": m.group(1), "explanation": ""}
            elif current_theme and stripped and not stripped.startswith("#"):
                expl = stripped.lstrip()
                current_theme["explanation"] = (
                    f"{current_theme['explanation']} {expl}".strip()
                    if current_theme["explanation"]
                    else expl
                )
        elif current_section == "ideas":
            m = re.match(r"^- (.+)", stripped)
            if m:
                sections["column_ideas"].append(m.group(1))
        elif current_section == "shifts":
            m = re.match(r"^- (.+)", stripped)
            if m:
                sections["narrative_shifts"].append(m.group(1))
        elif current_section == "pains":
            m = re.match(r"^- (.+)", stripped)
            if m:
                sections["pain_points"].append(m.group(1))

    if current_theme:
        sections["top_themes"].append(current_theme)

    return sections


def render_radar_page(snap_dir: Any, date: str) -> str:
    """Рендерит полноценную страницу Trend Radar с LLM-аналитикой и данными."""
    import json as _json
    from pathlib import Path

    snap = Path(snap_dir)

    # ── Загружаем signals.jsonl ──────────────────────────────────────────────
    signals: list[dict[str, Any]] = []
    signals_file = snap / "signals.jsonl"
    if signals_file.exists():
        for line in signals_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    signals.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue

    # ── Загружаем synthesis из signals-report.md ─────────────────────────────
    synthesis: dict[str, Any] = {
        "top_themes": [],
        "column_ideas": [],
        "narrative_shifts": [],
        "pain_points": [],
    }
    report_file = snap / "signals-report.md"
    if report_file.exists():
        synthesis = _parse_signals_report_sections(report_file.read_text(encoding="utf-8"))

    # ── Загружаем посты из всех JSONL ────────────────────────────────────────
    posts = load_posts_from_snapshot(snap)

    # ── Агрегируем темы из signals ───────────────────────────────────────────
    theme_counts: dict[str, int] = {}
    all_pains: list[str] = []
    for sig in signals:
        for t in sig.get("themes", []):
            theme_counts[t] = theme_counts.get(t, 0) + 1
        all_pains.extend(sig.get("pain_points", []))

    # Топ-10 по book_relevance
    top_by_book = sorted(signals, key=lambda s: s.get("book_relevance", 0), reverse=True)[:10]

    # ── Сила трендов и новизна ───────────────────────────────────────────────
    trends: list[Any] = []
    if signals:
        from ..trend_strength import (
            compute_trends,
            extract_themes_from_signals,
            load_theme_history,
        )

        data_dir = snap.parent.parent  # snapshots/YYYY-MM-DD → data/
        history = load_theme_history(data_dir)
        theme_snaps = extract_themes_from_signals(signals)
        for ts in theme_snaps:
            if not ts.date:
                ts.date = date
        trends = compute_trends(theme_snaps, history, date)

    # ── HTML ─────────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🤖 Trend Radar · {date}</title>
<style>{_RADAR_CSS}</style>
</head><body>
<div class="topnav">
  <a href="/dashboard">🧭 Дашборд</a>
  <a href="/runs">📁 Запуски</a>
  <a href="/runs/{date}">📊 Дашборд запуска</a>
  <a href="/docs">📖 API</a>
</div>
<h1>🤖 Trend Radar · {date}</h1>
"""

    # Stats row
    n_posts = len(posts)
    n_signals = len(signals)
    n_themes = len(theme_counts)
    n_pains = len(set(all_pains))
    html += '<div class="stats">\n'
    html += f'  <div class="stat"><div class="num">{n_posts}</div><div class="label">Постов</div></div>\n'
    html += f'  <div class="stat"><div class="num">{n_signals}</div><div class="label">LLM-сигналов</div></div>\n'
    html += (
        f'  <div class="stat"><div class="num">{n_themes}</div><div class="label">Тем</div></div>\n'
    )
    html += f'  <div class="stat"><div class="num">{n_pains}</div><div class="label">Pain points</div></div>\n'
    html += "</div>\n"

    # ── LLM Analysis section ─────────────────────────────────────────────────
    has_analysis = (
        synthesis["top_themes"]
        or synthesis["column_ideas"]
        or synthesis["narrative_shifts"]
        or signals
    )

    if has_analysis:
        html += '<h2 id="analysis">🧠 LLM-анализ (Qwen)</h2>\n'

        # Top themes
        if synthesis["top_themes"]:
            html += "<h3>🎯 Топ-темы дня</h3>\n"
            for theme in synthesis["top_themes"]:
                title = theme.get("theme", "")
                expl = theme.get("explanation", "")
                html += '<div class="theme-card">\n'
                html += f'  <div class="theme-title">{title}</div>\n'
                if expl:
                    html += f'  <div class="theme-expl">{expl}</div>\n'
                html += "</div>\n"

        # Column ideas
        if synthesis["column_ideas"]:
            html += "<h3>💡 Идеи для колонок</h3>\n"
            html += '<ul class="idea-list">\n'
            for idea in synthesis["column_ideas"]:
                html += f"  <li>{idea}</li>\n"
            html += "</ul>\n"

        # Narrative shifts
        if synthesis["narrative_shifts"]:
            html += "<h3>🔄 Сдвиги нарратива</h3>\n"
            html += '<ul class="shift-list">\n'
            for shift in synthesis["narrative_shifts"]:
                html += f"  <li>{shift}</li>\n"
            html += "</ul>\n"

        # Pain points (tags)
        unique_pains = list(dict.fromkeys(all_pains))[:20]
        if unique_pains:
            html += "<h3>🔥 Pain points</h3>\n"
            html += '<div class="pain-grid">\n'
            for pain in unique_pains:
                html += f'  <span class="pain-tag">{pain}</span>\n'
            html += "</div>\n"

        # Top-10 by book relevance
        if top_by_book:
            html += "<h3>📚 Топ-10 по релевантности для книги</h3>\n"
            html += "<table>\n<tr><th>#</th><th>Источник</th><th>Заголовок</th><th>📚</th><th>💼</th><th>Темы</th></tr>\n"
            for i, sig in enumerate(top_by_book, 1):
                sub = sig.get("subreddit", "")
                title = sig.get("title", "")[:70]
                book = sig.get("book_relevance", 0)
                biz = sig.get("business_relevance", 0)
                themes = ", ".join(sig.get("themes", [])[:2])
                src_label = f"r/{sub}" if sub else "—"
                html += (
                    f"<tr><td>{i}</td><td class='sub'>{src_label}</td>"
                    f"<td>{title}</td>"
                    f"<td class='score'>{book}</td><td class='score'>{biz}</td>"
                    f"<td class='sub'>{themes}</td></tr>\n"
                )
            html += "</table>\n"

        # Theme cloud from signals
        if theme_counts:
            sorted_themes = sorted(theme_counts.items(), key=lambda x: -x[1])[:15]
            html += "<h3>🏷 Облако тем (LLM-разметка)</h3>\n"
            html += '<div class="pain-grid">\n'
            for theme, count in sorted_themes:
                html += f'  <span class="pain-tag" style="border-color:rgba(74,158,255,0.3);color:var(--accent);background:rgba(74,158,255,0.1)">{theme} ({count})</span>\n'
            html += "</div>\n"

        # Trend strength + novelty
        if trends:
            html += "<h3>📈 Сила трендов</h3>\n"
            html += "<table>\n<tr><th>#</th><th>Тренд</th><th>Сила</th><th>Новизна</th><th>Постов</th><th>Источники</th><th>Динамика</th></tr>\n"
            dir_labels = {
                "growing": "📈 растёт",
                "stable": "→ стабилен",
                "fading": "📉 падает",
                "new": "🆕 новый",
            }
            for i, t in enumerate(trends[:20], 1):
                src_str = ", ".join(t.sources[:3])
                dir_label = dir_labels.get(t.direction, t.direction)
                html += (
                    f"<tr><td>{i}</td>"
                    f"<td>{t.theme}</td>"
                    f"<td class='score'>{t.strength_label} {t.strength}</td>"
                    f"<td>{t.novelty_label}</td>"
                    f"<td class='score'>{t.count}</td>"
                    f"<td class='sub'>{src_str}</td>"
                    f"<td class='sub'>{dir_label}</td></tr>\n"
                )
            html += "</table>\n"

    else:
        html += '<h2 id="analysis">🧠 LLM-анализ</h2>\n'
        html += '<p class="meta">LLM-анализ не выполнен для этого запуска. '
        html += "Запустите <code>reddit-compass signals</code> для генерации.</p>\n"

    # ── Data tables: Mega-trends ─────────────────────────────────────────────
    if posts:
        all_sorted = sorted(posts, key=lambda p: p.get("score", 0), reverse=True)
        html += '<h2 id="mega">🔥 Мега-тренды (топ через все источники)</h2>\n'
        for p in all_sorted[:20]:
            html += _post_row(p)

    html += f"""
<div class="footer">
  reddit-compass v0.2 · <a href="/docs">API</a> · <a href="/health">health</a>
  · {date} · {n_posts} постов · {n_signals} LLM-сигналов
</div>
</body></html>"""

    return html
