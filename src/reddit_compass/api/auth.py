"""OAuth2 client credentials: JWT-токены для API-клиентов."""

from __future__ import annotations

import os
import time
from typing import Any

from jose import JWTError, jwt

ALGORITHM = "HS256"
TOKEN_EXPIRY_SECONDS = 3600  # 1 час


def _get_secret() -> str:
    return os.environ.get("RC_API_SECRET", "dev-secret-change-me")


def _get_clients() -> dict[str, str]:
    """Парсит RC_API_CLIENTS="client_id:secret,other:secret2" → dict."""
    raw = os.environ.get("RC_API_CLIENTS", "")
    clients: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            cid, csecret = pair.split(":", 1)
            clients[cid.strip()] = csecret.strip()
    return clients


def authenticate_client(client_id: str, client_secret: str) -> bool:
    """Проверяет client credentials (timing-safe через hmac)."""
    import hmac

    clients = _get_clients()
    expected = clients.get(client_id)
    if expected is None:
        return False
    return hmac.compare_digest(expected, client_secret)


def create_access_token(client_id: str) -> str:
    """Создаёт JWT для клиента."""
    now = int(time.time())
    payload = {
        "sub": client_id,
        "iat": now,
        "exp": now + TOKEN_EXPIRY_SECONDS,
        "type": "access",
    }
    token: str = jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)
    return token


def verify_token(token: str) -> dict[str, Any] | None:
    """Верифицирует JWT. Возвращает payload или None."""
    try:
        payload: dict[str, Any] = jwt.decode(token, _get_secret(), algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None
