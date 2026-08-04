"""Стоимостная маршрутизация Qwen-вызовов: скидки, подписка, бесплатные квоты.

Провайдер не отдаёт остаток квот через API (в ответе только ``usage`` конкретного
вызова), поэтому «сколько осталось» считается локальным леджером: каждый успешный
вызов пишет токены в ``qwen_usage.db``, а размеры квот задаются конфигурацией.

Порядок предпочтения (см. ``pick_model``):

* **скидочное окно подписки** (17:00–03:00 МСК) — token-plan в это время дешевле,
  поэтому синтез/ревью идут туда;
* **бесплатные квоты pay-as-you-go** — 1M токенов на модель в течение 90 дней после
  активации; массовые прогоны (извлечение, классификация, нормализация) жгут их;
* **подписка вне окна** — только когда бесплатных квот не осталось;
* **последний резерв** — первая модель цепочки без проверки квоты, чтобы прогон не
  падал из-за конфигурации.

Явно запрошенная модель (``--model``) всегда побеждает роутер.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, time
from pathlib import Path

from .config import DEFAULT_DATA_DIR

# 17:00–03:00 МСК (UTC+3) в координатах UTC.
_OFFPEAK_START_UTC = time(14, 0)
_OFFPEAK_END_UTC = time(0, 0)

# Бесплатный грант pay-as-you-go: 1M токенов на модель, 90 дней после активации.
DEFAULT_PAYG_FREE_TOKENS = 1_000_000

# Цепочки кандидатов (модель, эндпоинт) в порядке качества для задачи.
# bulk — массовое извлечение/классификация: сперва бесплатные квоты payg,
# подписка — только резерв.
BULK_CHAIN: tuple[tuple[str, str], ...] = (
    ("qwen3-235b-a22b-instruct-2507", "payg"),
    ("qwen3.6-flash", "payg"),
    ("qwen3.5-flash", "payg"),
    ("qwen-flash", "payg"),
    ("qwen3.6-flash", "token-plan"),
)
# synth — сложный синтез: в скидочное окно подписка, вне окна — бесплатные,
# подписка вне окна — когда бесплатных не осталось.
SYNTH_CHAIN: tuple[tuple[str, str], ...] = (
    ("qwen3.8-max-preview", "token-plan"),
    ("qwen3-max", "payg"),
    ("qwen3-235b-a22b-instruct-2507", "payg"),
)


def ledger_path() -> Path:
    override = os.environ.get("RC_QWEN_LEDGER_PATH", "")
    return Path(override) if override else DEFAULT_DATA_DIR / "qwen_usage.db"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS qwen_usage (
            model            TEXT NOT NULL,
            endpoint         TEXT NOT NULL,
            prompt_tokens    INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            created_at       TEXT NOT NULL
        )"""
    )
    return conn


def record_usage(
    *,
    model: str,
    endpoint: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Пишет расход вызова в леджер. Сбой леджера не роняет сам вызов."""
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(path)
        with conn:
            conn.execute(
                "INSERT INTO qwen_usage "
                "(model, endpoint, prompt_tokens, completion_tokens, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    model,
                    endpoint,
                    int(prompt_tokens),
                    int(completion_tokens),
                    datetime.now(UTC).isoformat(),
                ),
            )
        conn.close()
    except (OSError, sqlite3.Error):
        # Леджер — наблюдение, а не условие работы: диск не должен останавливать прогон.
        return


def usage_totals(path: Path | None = None) -> dict[tuple[str, str], int]:
    """Суммарные токены по (модель, эндпоинт)."""
    target = path or ledger_path()
    if not target.exists():
        return {}
    conn = _connect(target)
    rows = conn.execute(
        """SELECT model, endpoint, SUM(prompt_tokens + completion_tokens)
           FROM qwen_usage GROUP BY model, endpoint"""
    ).fetchall()
    conn.close()
    return {(str(model), str(endpoint)): int(total) for model, endpoint, total in rows}


def token_plan_quota() -> int | None:
    """Общий лимит токенов подписки token-plan; пусто — неизвестен (считаем безлимитом)."""
    raw = os.environ.get("RC_QWEN_TOKEN_PLAN_TOKENS", "").strip()
    return int(raw) if raw else None


def payg_free_quota() -> int:
    raw = os.environ.get("RC_QWEN_PAYG_FREE_TOKENS", "").strip()
    return int(raw) if raw else DEFAULT_PAYG_FREE_TOKENS


def in_offpeak(now: datetime | None = None) -> bool:
    moment = (now or datetime.now(UTC)).time()
    # Окно переходит через полночь: 14:00–00:00 UTC.
    return moment >= _OFFPEAK_START_UTC or moment < _OFFPEAK_END_UTC


# Крокозябрное имя — историческое из .env.secrets; верхнее — то же, но по стандарту.
_PAYG_KEY_VARS = (
    "DASHSCOPE_API_KEY",
    "QWEN_PAY_AS_YOU_GO_PLAN_KEY",
    "QWEN_Pay_As_You_Go_PLAN_KEY",
)


def _has_key(endpoint: str) -> bool:
    if endpoint == "token-plan":
        return bool(os.environ.get("QWEN_TOKEN_PLAN_KEY", ""))
    return any(os.environ.get(var) for var in _PAYG_KEY_VARS)


def _room_left(model: str, endpoint: str, totals: dict[tuple[str, str], int]) -> bool:
    used = totals.get((model, endpoint), 0)
    if endpoint == "token-plan":
        quota = token_plan_quota()
        if quota is None:
            return True
        return sum(total for (m, e), total in totals.items() if e == "token-plan") < quota
    return used < payg_free_quota()


def pick_model(task: str, now: datetime | None = None) -> tuple[str, str, str]:
    """Выбирает (модель, эндпоинт, причина) для задачи ``bulk`` или ``synth``."""
    totals = usage_totals()
    offpeak = in_offpeak(now)
    chain = SYNTH_CHAIN if task == "synth" else BULK_CHAIN

    def ordered() -> list[tuple[str, str]]:
        if task == "bulk":
            return list(BULK_CHAIN)
        if offpeak:
            # Скидочное окно: подписка впереди бесплатных.
            return list(SYNTH_CHAIN)
        # Вне окна: сперва бесплатные квоты, подписка — резерв.
        return [c for c in SYNTH_CHAIN if c[1] == "payg"] + [
            c for c in SYNTH_CHAIN if c[1] == "token-plan"
        ]

    for model, endpoint in ordered():
        if not _has_key(endpoint):
            continue
        if _room_left(model, endpoint, totals):
            if task == "synth" and offpeak and endpoint == "token-plan":
                why = "скидочное окно подписки"
            elif endpoint == "payg":
                why = "бесплатная квота pay-as-you-go"
            else:
                why = "подписка token-plan"
            return model, endpoint, why
    # Резерв: первая доступная по ключам модель цепочки без проверки квоты.
    for model, endpoint in chain:
        if _has_key(endpoint):
            return model, endpoint, "резерв: квоты исчерпаны или неизвестны"
    raise ValueError("Нет ни одного ключа Qwen для маршрутизации")
