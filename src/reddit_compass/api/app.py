"""FastAPI application factory."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from ..db import get_db, query_posts, query_signals, query_snapshots, query_stats
from .auth import authenticate_client, create_access_token, verify_token
from .schemas import (
    PaginatedPosts,
    PostOut,
    SignalOut,
    SnapshotOut,
    StatsOut,
    TokenRequest,
    TokenResponse,
)
from .ui import router as ui_router

security = HTTPBearer(auto_error=False)

STATIC_DIR = Path(__file__).parent / "static"


def _get_db() -> Generator[sqlite3.Connection, None, None]:
    db_path = Path(os.environ.get("RC_DB_PATH", "data/compass.db"))
    conn = get_db(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    sub: str = payload.get("sub", "unknown")
    return sub


def create_app() -> FastAPI:
    app = FastAPI(
        title="reddit-compass API",
        description="Compass for Reddit trends — API для потребителей данных",
        version="0.2.0",
    )

    # Security headers middleware
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
        if request.url.path.startswith(("/today", "/stories", "/explore", "/runs")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; "
                "style-src 'self'; script-src 'self'; frame-ancestors 'none'"
            )
        return response

    # Static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # UI routes
    app.include_router(ui_router)

    # CORS
    origins_raw = os.environ.get("RC_API_CORS_ORIGINS", "")
    origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # ── Health ──────────────────────────────────────────────────────────────

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "reddit-compass"}

    # ── Dashboard (read-only, без auth) ─────────────────────────────────────

    @app.get("/dashboard", response_class=HTMLResponse, tags=["system"])
    def dashboard(db: sqlite3.Connection = Depends(_get_db)) -> str:
        from .dashboard import render_dashboard

        stats = query_stats(db)
        posts = query_posts(db, limit=1000)

        # Загружаем манифест последнего snapshot
        manifest_data = None
        latest = stats.get("latest_snapshot")
        if latest:
            from pathlib import Path as _Path

            from ..manifest import load_manifest

            snap_dir = _Path(os.environ.get("DATA_DIR", "data")) / "snapshots" / latest
            m = load_manifest(snap_dir)
            if m:
                manifest_data = m.to_dict()

        return render_dashboard(stats, posts, manifest_data)

    # ── Runs: история запусков ────────────────────────────────────────────────

    @app.get("/runs", response_class=HTMLResponse, tags=["system"])
    def runs_page(db: sqlite3.Connection = Depends(_get_db)) -> str:
        from pathlib import Path as _Path

        from ..manifest import load_manifest

        data_dir = _Path(os.environ.get("DATA_DIR", "data"))
        snapshots_dir = data_dir / "snapshots"

        # Собираем все snapshot-даты
        runs: list[dict[str, Any]] = []
        if snapshots_dir.exists():
            for d in sorted(snapshots_dir.iterdir(), reverse=True):
                if not d.is_dir():
                    continue
                date = d.name
                manifest = load_manifest(d)
                # Считаем файлы
                files = {f.name: 0 for f in d.glob("*.jsonl")}
                for f in d.glob("*.jsonl"):
                    files[f.name] = sum(1 for line in f.read_text().splitlines() if line.strip())
                has_radar = (d / "signals-report.md").exists() or (d / "trend-radar.md").exists()

                runs.append(
                    {
                        "date": date,
                        "manifest": manifest.to_dict() if manifest else None,
                        "files": files,
                        "total": sum(files.values()),
                        "has_radar": has_radar,
                    }
                )

        # Рендерим HTML
        html = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🧭 Запуски — reddit-compass</title>
<style>
:root { --bg:#0f0f1a; --fg:#e0e0e0; --accent:#4a9eff; --muted:#777; --card:#1a1a2e; --border:#2a2a4a; --green:#51cf66; --red:#ff6b6b; }
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,'Segoe UI',Roboto,monospace; background:var(--bg); color:var(--fg); padding:1.5rem; max-width:1000px; margin:0 auto; line-height:1.5; }
h1 { color:var(--accent); margin-bottom:1rem; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
.run { background:var(--card); border:1px solid var(--border); border-radius:8px; margin:1rem 0; padding:1rem; }
.run-header { display:flex; justify-content:space-between; align-items:center; cursor:pointer; }
.run-date { font-size:1.1rem; font-weight:bold; }
.run-status { font-size:0.85rem; }
.ok { color:var(--green); } .err { color:var(--red); } .warn { color:#ffd43b; }
.run-details { margin-top:0.8rem; font-size:0.85rem; color:var(--muted); }
.run-details table { width:100%; border-collapse:collapse; margin-top:0.5rem; }
.run-details td, .run-details th { padding:0.3rem 0.5rem; border-bottom:1px solid var(--border); text-align:left; }
.run-details th { color:var(--muted); font-weight:normal; }
.badge { display:inline-block; background:var(--border); border-radius:4px; padding:0.1rem 0.5rem; margin:0.1rem; font-size:0.75rem; }
.nav { margin-bottom:1rem; } .nav a { margin-right:1rem; }
details summary { cursor:pointer; color:var(--accent); }
</style></head><body>
<h1>🧭 Запуски</h1>
<div class="nav"><a href="/dashboard">← Дашборд</a> <a href="/docs">API</a></div>
"""
        for run in runs:
            m = run["manifest"]
            if m:
                status = m.get("status", "?")
                icon = {"done": "✅", "partial": "⚠️", "running": "⏳"}.get(status, "❓")
                started = m.get("started_at", "")[:16].replace("T", " ")
                duration = f"{m.get('duration_sec', 0):.0f}с"
                status_cls = (
                    "ok" if status == "done" else ("warn" if status == "partial" else "err")
                )
            else:
                icon = "📁"
                started = run["date"]
                duration = "—"
                status_cls = ""

            html += f'<div class="run">\n'
            html += f'<div class="run-header"><span class="run-date">{icon} {run["date"]}</span>'
            html += f'<span class="run-status {status_cls}">{started} · {duration} · {run["total"]} items</span></div>\n'

            # Детали (разворачиваемые)
            html += '<details><summary>Подробнее</summary><div class="run-details">\n'

            # Файлы
            html += "<table><tr><th>Файл</th><th>Строк</th></tr>\n"
            for fname, count in sorted(run["files"].items()):
                html += f"<tr><td>{fname}</td><td>{count}</td></tr>\n"
            html += "</table>\n"

            # Манифест (источники)
            if m and m.get("sources"):
                html += "<table><tr><th>Источник</th><th>Статус</th><th>Собрано</th><th>Время</th></tr>\n"
                for s in m["sources"]:
                    sicon = {"ok": "✅", "error": "❌", "empty": "⚠️", "skipped": "⏭"}.get(
                        s.get("status", ""), "❓"
                    )
                    html += (
                        f"<tr><td>{s.get('name', '')}</td><td>{sicon} {s.get('status', '')}</td>"
                        f"<td>{s.get('count', 0)}</td><td>{s.get('duration_sec', 0):.0f}с</td></tr>\n"
                    )
                html += "</table>\n"

            # Ссылки: дашборд запуска (операционный) + trend radar (аналитика)
            html += (
                f'<p style="margin-top:0.5rem">'
                f'<a href="/runs/{run["date"]}">📊 Дашборд запуска (посты) →</a>'
            )
            if run["has_radar"]:
                html += f' &nbsp; <a href="/runs/{run["date"]}/radar">🤖 Trend Radar (анализ) →</a>'
            html += "</p>\n"

            html += "</div></details>\n</div>\n"

        html += "</body></html>"
        return html

    @app.get("/runs/{date}", response_class=HTMLResponse, tags=["system"])
    def run_detail(date: str) -> str:
        """Полный дашборд по конкретному запуску (из JSONL-файлов)."""
        from pathlib import Path as _Path

        from ..manifest import load_manifest
        from .dashboard import (
            load_posts_from_snapshot,
            load_signals_from_snapshot,
            render_dashboard,
        )

        data_dir = _Path(os.environ.get("DATA_DIR", "data"))
        snap_dir = data_dir / "snapshots" / date
        if not snap_dir.exists():
            return (
                f"<html><body><h1>404</h1><p>Snapshot {date} не найден.</p>"
                f"<a href='/runs'>← Запуски</a></body></html>"
            )

        posts = load_posts_from_snapshot(snap_dir)
        signals = load_signals_from_snapshot(snap_dir)
        manifest = load_manifest(snap_dir)
        manifest_data = manifest.to_dict() if manifest else None

        # Статистика из файлов
        sources: dict[str, int] = {}
        for p in posts:
            src = p.get("source", "reddit")
            sources[src] = sources.get(src, 0) + 1

        stats = {
            "total_posts": len(posts),
            "total_snapshots": 1,
            "total_signals": len(signals),
            "latest_snapshot": date,
            "top_subreddits": [],
        }

        html = render_dashboard(stats, posts, manifest_data, signals)
        # Добавляем навигацию назад
        html = html.replace(
            "<h1>🧭 reddit-compass</h1>",
            f"<div style='margin-bottom:0.5rem'><a href='/runs' style='color:#4a9eff'>← Запуски</a></div>"
            f"<h1>🧭 reddit-compass · {date}</h1>",
        )
        return html

    @app.get("/runs/{date}/radar", response_class=HTMLResponse, tags=["system"])
    def run_radar(date: str) -> str:
        """Trend radar: LLM-аналитика + данные по всем источникам."""
        from pathlib import Path as _Path

        from .dashboard import render_radar_page

        data_dir = _Path(os.environ.get("DATA_DIR", "data"))
        snap = data_dir / "snapshots" / date
        if not snap.exists():
            return (
                f"<html><body><h1>404</h1><p>Radar for {date} not found.</p>"
                f"<a href='/runs'>← Back</a></body></html>"
            )

        return render_radar_page(snap, date)

    @app.post("/oauth/token", response_model=TokenResponse, tags=["auth"])
    def oauth_token(body: TokenRequest) -> TokenResponse:
        if body.grant_type != "client_credentials":
            raise HTTPException(status_code=400, detail="Unsupported grant_type")
        if not authenticate_client(body.client_id, body.client_secret):
            raise HTTPException(status_code=401, detail="Invalid client credentials")
        token = create_access_token(body.client_id)
        return TokenResponse(access_token=token)

    # ── API v1 ──────────────────────────────────────────────────────────────

    @app.get("/api/v1/snapshots", response_model=list[SnapshotOut], tags=["data"])
    def list_snapshots(
        limit: int = 30,
        db: sqlite3.Connection = Depends(_get_db),
        _client: str = Depends(_require_auth),
    ) -> list[dict[str, Any]]:
        return query_snapshots(db, limit=min(limit, 100))

    @app.get("/api/v1/posts", response_model=PaginatedPosts, tags=["data"])
    def list_posts(
        date: str | None = None,
        subreddit: str | None = None,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
        db: sqlite3.Connection = Depends(_get_db),
        _client: str = Depends(_require_auth),
    ) -> PaginatedPosts:
        items = query_posts(
            db,
            date=date,
            subreddit=subreddit,
            source=source,
            limit=min(limit, 200),
            offset=offset,
        )
        total_sql = "SELECT COUNT(*) FROM posts WHERE 1=1"
        params: list[Any] = []
        if date:
            total_sql += " AND snapshot_id IN (SELECT id FROM snapshots WHERE date = ?)"
            params.append(date)
        if subreddit:
            total_sql += " AND subreddit = ?"
            params.append(subreddit)
        if source:
            total_sql += " AND source = ?"
            params.append(source)
        total: int = db.execute(total_sql, params).fetchone()[0]
        return PaginatedPosts(
            items=[PostOut(**r) for r in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/signals", response_model=list[SignalOut], tags=["data"])
    def list_signals(
        date: str | None = None,
        db: sqlite3.Connection = Depends(_get_db),
        _client: str = Depends(_require_auth),
    ) -> list[dict[str, Any]]:
        return query_signals(db, date=date)

    @app.get("/api/v1/stats", response_model=StatsOut, tags=["data"])
    def get_stats(
        db: sqlite3.Connection = Depends(_get_db),
        _client: str = Depends(_require_auth),
    ) -> dict[str, Any]:
        return query_stats(db)

    return app
