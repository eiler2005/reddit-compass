"""Минимальный read-only дашборд (HTML, editorial-стиль).

Сервится из FastAPI по GET /dashboard. Без JS-фреймворков — серверный рендер.
Спокойный, статусный стиль: тёмный фон, моноширинный, минимум цвета.
"""

from __future__ import annotations

from typing import Any

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>reddit-compass — дашборд</title>
<style>
:root {{
  --bg: #1a1a2e;
  --fg: #e0e0e0;
  --accent: #4a9eff;
  --muted: #888;
  --card-bg: #16213e;
  --border: #2a2a4a;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.6;
  padding: 2rem;
  max-width: 900px;
  margin: 0 auto;
}}
h1 {{ color: var(--accent); font-size: 1.4rem; margin-bottom: 0.5rem; }}
h2 {{ color: var(--fg); font-size: 1.1rem; margin: 1.5rem 0 0.5rem; border-bottom: 1px solid var(--border); padding-bottom: 0.3rem; }}
.meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin: 1rem 0; }}
.stat {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; text-align: center; }}
.stat .num {{ font-size: 1.8rem; color: var(--accent); }}
.stat .label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; margin: 0.5rem 0; }}
th {{ text-align: left; color: var(--muted); font-weight: normal; padding: 0.4rem; border-bottom: 1px solid var(--border); }}
td {{ padding: 0.4rem; border-bottom: 1px solid var(--border); }}
.score {{ color: var(--accent); font-weight: bold; }}
.sub {{ color: var(--muted); }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.footer {{ margin-top: 2rem; color: var(--muted); font-size: 0.75rem; }}
</style>
</head>
<body>
<h1>🧭 reddit-compass</h1>
<p class="meta">Компас по трендам Reddit — показывает, куда смотреть</p>

<div class="stats">
  <div class="stat"><div class="num">{total_snapshots}</div><div class="label">Snapshots</div></div>
  <div class="stat"><div class="num">{total_posts}</div><div class="label">Постов</div></div>
  <div class="stat"><div class="num">{total_signals}</div><div class="label">Сигналов</div></div>
  <div class="stat"><div class="num">{latest}</div><div class="label">Последний</div></div>
</div>

<h2>Топ сабреддитов</h2>
<table>
<tr><th>Subreddit</th><th>Постов</th><th>Avg score</th></tr>
{subreddit_rows}
</table>

<h2>Последние посты (топ по score)</h2>
<table>
<tr><th>r/</th><th>Title</th><th>Score</th><th>Комм.</th></tr>
{post_rows}
</table>

<div class="footer">
  reddit-compass v0.2 · <a href="/docs">API docs</a> · <a href="/health">health</a>
</div>
</body>
</html>"""


def render_dashboard(stats: dict[str, Any], posts: list[dict[str, Any]]) -> str:
    """Рендерит HTML-дашборд из stats и posts."""
    subreddit_rows = ""
    for s in stats.get("top_subreddits", [])[:10]:
        subreddit_rows += (
            f"<tr><td class='sub'>r/{s['subreddit']}</td>"
            f"<td>{s['cnt']}</td>"
            f"<td class='score'>{s['avg_score']:.0f}</td></tr>\n"
        )

    post_rows = ""
    for p in posts[:15]:
        title = p.get("title", "")[:70]
        post_rows += (
            f"<tr><td class='sub'>r/{p.get('subreddit', '')}</td>"
            f"<td>{title}</td>"
            f"<td class='score'>{p.get('score', 0)}</td>"
            f"<td>{p.get('num_comments', 0)}</td></tr>\n"
        )

    return DASHBOARD_TEMPLATE.format(
        total_snapshots=stats.get("total_snapshots", 0),
        total_posts=stats.get("total_posts", 0),
        total_signals=stats.get("total_signals", 0),
        latest=stats.get("latest_snapshot", "—"),
        subreddit_rows=subreddit_rows or "<tr><td colspan=3>Нет данных</td></tr>",
        post_rows=post_rows or "<tr><td colspan=4>Нет данных</td></tr>",
    )
