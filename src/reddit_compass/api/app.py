"""FastAPI application factory."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
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
from .v2 import router as v2_router

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
    async def add_security_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
        if request.url.path.startswith(
            (
                "/about",
                "/digest",
                "/today",
                "/news",
                "/stories",
                "/trends",
                "/projects",
                "/explore",
                "/runs",
                "/radar",
                "/engine",
            )
        ):
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

    # API v2 routes
    app.include_router(v2_router)

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
    def health() -> dict[str, Any]:
        """Жив ли сервис и не устарели ли данные.

        `status` становится `degraded` при простое конвейера: иначе отказ ночного
        расписания ничем себя не проявляет — контейнер отвечает `ok`, а UI показывает
        вчерашний выпуск. Код ответа остаётся 200: сервис исправен и отдаёт последнее,
        что у него есть; деградировали данные, а не он.
        """
        from ..intelligence.engine import DEFAULT_ENGINE_DB_PATH, open_engine_readonly
        from .health import data_freshness

        engine_path = Path(os.environ.get("RC_ENGINE_DB_PATH", str(DEFAULT_ENGINE_DB_PATH)))
        engine_conn = None
        try:
            if engine_path.exists():
                engine_conn = open_engine_readonly(engine_path)
            freshness = data_freshness(engine_conn)
        except sqlite3.Error:
            # Диагностика свежести не имеет права уронить проверку живости.
            freshness = data_freshness(None)
        finally:
            if engine_conn is not None:
                engine_conn.close()
        status = "degraded" if freshness["data_status"] == "stale" else "ok"
        return {"status": status, "service": "reddit-compass", **freshness}

    @app.get("/version", tags=["system"])
    def version() -> dict[str, Any]:
        """Что развёрнуто и на каких данных работает.

        `/health` отвечает только «жив», по нему нельзя понять, доехал ли деплой
        и какой выпуск сейчас показывает UI. Реестр отвечает на оба вопроса сразу.
        """
        from ..intelligence.engine import DEFAULT_ENGINE_DB_PATH, open_engine_readonly
        from ..versioning import version_report

        engine_path = Path(os.environ.get("RC_ENGINE_DB_PATH", str(DEFAULT_ENGINE_DB_PATH)))
        engine_conn = None
        try:
            if engine_path.exists():
                engine_conn = open_engine_readonly(engine_path)
            return version_report(engine_conn=engine_conn)
        finally:
            if engine_conn is not None:
                engine_conn.close()

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
