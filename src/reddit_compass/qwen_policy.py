"""Стоимостная маршрутизация Qwen-вызовов через pay-as-you-go API.

Провайдер не отдаёт остаток квот через API (в ответе только ``usage`` конкретного
вызова), поэтому «сколько осталось» считается локальным леджером: каждый успешный
вызов пишет токены в ``qwen_usage.db``, а размеры квот задаются конфигурацией.

Если владелец *явно* подтвердил бесплатный грант через
``RC_QWEN_PAYG_FREE_TOKENS``, он расходуется первым. Без этой конфигурации сервис
никогда не называет pay-as-you-go бесплатным и остаётся на его обычном list price.
Token Plan не является endpoint'ом сервиса: это отдельный интерактивный продукт.

Явно запрошенная модель (``--model``) всегда побеждает роутер.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from .config import DEFAULT_DATA_DIR

logger = logging.getLogger("reddit_compass")

# 17:00–03:00 МСК (UTC+3) в координатах UTC.
_OFFPEAK_START_UTC = time(14, 0)
_OFFPEAK_END_UTC = time(0, 0)

# Международный прайс не обещает бесплатный грант. Его можно включить только явной
# конфигурацией после проверки в Model Studio console; нельзя превращать предположение
# в финансовое решение по умолчанию.
DEFAULT_PAYG_FREE_TOKENS = 0
PAYG_GRANT_DAYS = 90
# Ждать освобождения леджера, а не падать: конкурент по записи — норма, а не сбой.
_LEDGER_BUSY_TIMEOUT_SECONDS = 30.0

# Модель под сложность задачи, а не наоборот. Профиль нагрузки снят с боевого движка
# 5 августа 2026 (`trend_engine.db` на VPS):
#
#   стадия                     вызовов   что решает                      класс
#   извлечение схем            ~1 020    (актор, действие, объект)       bulk
#     (10 195 заголовков / батч 10)      из одного заголовка
#   нормализация акторов          ~41    «Linkedin» → «LinkedIn»         bulk
#     (1 638 различных акторов / батч 40)
#   классификация Pulse       батчами    pain points, релевантность      bulk
#   ревью пары сюжетов            629    «это одно событие?»             средний
#   трендовое ревью               171    когерентность 20 сюжетов        synth
#   синтез                    единицы    темы, сдвиги нарратива          synth
#
# Массовые стадии — это извлечение полей из одной строки, и на них нужна самая дешёвая
# модель, а не самая сильная. Прежняя цепочка открывалась `qwen3-235b-a22b-instruct-2507`:
# 235B на тысяче вызовов «разбери заголовок» — ровно та инверсия, из-за которой
# бесплатный грант выгорал за один ночной прогон.
#
# Актуальные list prices Model Studio для Singapore/international (5 августа 2026),
# за 1M input/output: qwen3.7-flash ¥0.225/¥0.974, qwen3.8-max ¥14.988/¥44.965.
# То есть Flash дешевле Max примерно в 67× по input и 46× по output. Даже когда
# бесплатный грант исчерпан, regular bounded JSON review экономичнее держать на Flash.
BULK_CHAIN: tuple[tuple[str, str], ...] = (
    ("qwen3.7-flash", "payg"),
    ("qwen3.6-flash", "payg"),
    ("qwen3.5-flash", "payg"),
    ("qwen-flash", "payg"),
)
# synth — только действительно свободный сложный синтез: мало вызовов, большие промпты,
# где качество решает. Обычные pair/trend review теперь не относятся к этому классу.
# Обычная цена Max применяется только к единичному synthesis; массовые review сюда не
# относятся. Внешние скидки проверяются в консоли, но не меняют service routing.
SYNTH_CHAIN: tuple[tuple[str, str], ...] = (
    ("qwen3.8-max", "payg"),
    ("qwen3.7-max", "payg"),
    ("qwen3.5-plus", "payg"),
)


def ledger_path() -> Path:
    override = os.environ.get("RC_QWEN_LEDGER_PATH", "")
    return Path(override) if override else DEFAULT_DATA_DIR / "qwen_usage.db"


def _connect(path: Path) -> sqlite3.Connection:
    # Ночной цикл пишет в леджер из нескольких контейнеров сразу, а стадии идут с
    # параллельностью 6–8. С дефолтными пятью секундами конкурент получал
    # `database is locked`, и его расход тихо терялся — а леджер единственный источник
    # правды об остатке квоты, по нему роутер решает, есть ли ещё бесплатное место.
    conn = sqlite3.connect(path, timeout=_LEDGER_BUSY_TIMEOUT_SECONDS)
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
    except (OSError, sqlite3.Error) as exc:
        # Леджер — наблюдение, а не условие работы: диск не должен останавливать прогон.
        # Но и глотать сбой целиком нельзя: недосчитанный расход роутер прочтёт как
        # свободную квоту, поэтому потеря обязана оставить след.
        logger.warning(
            "Qwen ledger write failed (%s: %s); %d+%d tokens for %s/%s not recorded",
            type(exc).__name__,
            exc,
            prompt_tokens,
            completion_tokens,
            model,
            endpoint,
        )


def usage_totals(
    path: Path | None = None, *, since: datetime | None = None
) -> dict[tuple[str, str], int]:
    """Суммарные токены по (модель, эндпоинт); ``since`` — не считать расход раньше.

    Отсечка нужна бесплатному гранту: он живёт 90 дней, а леджер копится вечно, и без
    неё расход прошлого гранта навсегда закрывал бы модель в новом.
    """
    target = path or ledger_path()
    if not target.exists():
        return {}
    # Леджер — наблюдение, а не предусловие маршрутизации: `record_usage` уже глотает
    # свои сбои, но `pick_model`/`pick_endpoint` зовут эту функцию первой, причём
    # вызывающие делают это *вне* своих try-блоков. Незакрытый `database is locked`
    # под конкурентностью 6–8 убивал bulk-стадию целиком. Недоступный леджер означает
    # «расход неизвестен» — это ноль известного расхода, а не отказ стадии.
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(target)
        query = """SELECT model, endpoint, SUM(prompt_tokens + completion_tokens)
                   FROM qwen_usage"""
        if since is None:
            rows = conn.execute(f"{query} GROUP BY model, endpoint").fetchall()
        else:
            rows = conn.execute(
                f"{query} WHERE created_at >= ? GROUP BY model, endpoint",
                (since.isoformat(),),
            ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Не удалось прочитать леджер Qwen (%s); считаем расход нулевым", exc)
        return {}
    finally:
        if conn is not None:
            conn.close()
    return {(str(model), str(endpoint)): int(total) for model, endpoint, total in rows}


def _int_env(name: str, default: int | None) -> int | None:
    """Целое из переменной окружения; мусор и отрицательное — предупреждение и default.

    Голый `int(raw)` ронял стадию трейсбеком на `1M`, `1e6` или `1,000,000` — записях,
    которые оператор набирает естественно. Отрицательное значение принималось молча и
    означало «нет квоты», что неотличимо от опечатки.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r — не целое число; берём значение по умолчанию %r", name, raw, default)
        return default
    if value < 0:
        logger.warning("%s=%d отрицательно; берём значение по умолчанию %r", name, value, default)
        return default
    return value


def token_plan_quota() -> int | None:
    """Общий лимит токенов подписки token-plan; пусто — неизвестен (считаем безлимитом)."""
    return _int_env("RC_QWEN_TOKEN_PLAN_TOKENS", None)


def payg_grant_start() -> datetime | None:
    """Дата активации бесплатного гранта из ``RC_QWEN_PAYG_GRANT_START`` (YYYY-MM-DD).

    Провайдер её не отдаёт, вывести из леджера нельзя (первый вызов мог случиться
    сильно позже активации), поэтому это конфигурация. Не задана — считаем грант
    бессрочно активным и меряем расход по всей истории: так вело себя первое
    поколение роутера, и молча ужесточать поведение по неизвестной дате нельзя.
    """
    raw = os.environ.get("RC_QWEN_PAYG_GRANT_START", "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        # Битую дату нельзя проглатывать молча: `payg_grant_expired` тогда навсегда
        # отвечает «не истёк», и оператор, набравший `05.08.2026`, продолжает получать
        # «подтверждённая бесплатная квота» после 90-го дня. Более строгая настройка
        # деградировала в самую разрешительную.
        logger.warning(
            "RC_QWEN_PAYG_GRANT_START=%r не разбирается как дата (ожидается YYYY-MM-DD); "
            "срок гранта не проверяется",
            raw,
        )
        return None
    # Смещение отбрасывать нельзя: `.replace(tzinfo=UTC)` над `…+03:00` сдвигал окно на
    # три часа. Naive-значение считаем UTC, aware — приводим к UTC.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def payg_grant_expired(now: datetime | None = None) -> bool:
    """Истёк ли 90-дневный грант. Неизвестная дата активации — не истёк."""
    start = payg_grant_start()
    if start is None:
        return False
    return (now or datetime.now(UTC)) >= start + timedelta(days=PAYG_GRANT_DAYS)


def payg_free_quota() -> int:
    value = _int_env("RC_QWEN_PAYG_FREE_TOKENS", DEFAULT_PAYG_FREE_TOKENS)
    return DEFAULT_PAYG_FREE_TOKENS if value is None else value


def payg_grant_is_per_model() -> bool:
    """Даётся ли бесплатный грант каждой модели отдельно, а не общим пулом на аккаунт.

    Провайдер этого не документирует, а разница дорогая: при общем пуле «свободные»
    модели ниже по цепочке свободными не являются. Включать только после проверки в
    Model Studio console — ровно как и сам ``RC_QWEN_PAYG_FREE_TOKENS``.
    """
    return os.environ.get("RC_QWEN_PAYG_GRANT_PER_MODEL", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def in_offpeak(now: datetime | None = None) -> bool:
    """Идёт ли сейчас скидочное окно подписки.

    Окно задано в МСК и в координатах UTC переходит через полночь, поэтому проверка
    двусторонняя. Сейчас конец приходится ровно на 00:00 UTC, и второе условие всегда
    ложно — но выражать это как `moment >= start` нельзя: любой сдвиг конца окна
    сломал бы функцию молча, а границы у провайдера не наши.
    """
    moment = (now or datetime.now(UTC)).time()
    if _OFFPEAK_START_UTC <= _OFFPEAK_END_UTC:
        return _OFFPEAK_START_UTC <= moment < _OFFPEAK_END_UTC
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


def _room_left(
    model: str,
    endpoint: str,
    totals: dict[tuple[str, str], int],
    *,
    grant_expired: bool = False,
) -> bool:
    if endpoint == "token-plan":
        quota = token_plan_quota()
        if quota is None:
            return True
        return sum(total for (m, e), total in totals.items() if e == "token-plan") < quota
    # Грант истёк — бесплатного места нет ни при каком расходе, и делать вид, что оно
    # есть, значит выставлять счёт там, где роутер обещал бесплатно.
    if grant_expired:
        return False
    # Считать грант отдельным для каждой модели можно только если это подтверждено:
    # если пул на самом деле общий, то после его исчерпания роутер уверен, что впереди
    # ещё три бесплатные модели, и уводит извлечение (~1020 вызовов) с `qwen3.7-flash`
    # на `qwen3.6-flash`, который по списку цен дороже в 8.3× по input и 11.5× по
    # output — продолжая при этом писать в лог «подтверждённая бесплатная квота».
    # Провайдер семантику гранта не документирует, поэтому по умолчанию считаем пул
    # общим: ошибка в эту сторону оставляет нас на самой дешёвой модели, ошибка в
    # обратную — платит по восьмикратному тарифу.
    if payg_grant_is_per_model():
        return totals.get((model, endpoint), 0) < payg_free_quota()
    spent = sum(total for (_, e), total in totals.items() if e == "payg")
    return spent < payg_free_quota()


def pick_model(task: str, now: datetime | None = None) -> tuple[str, str, str]:
    """Выбирает (модель, endpoint, причина) для задачи ``bulk`` или ``synth``.

    Сначала выбирается любая явно настроенная бесплатная квота. Когда её нет или она
    исчерпана, остаёмся на первой (самой дешёвой/подходящей) модели pay-as-you-go, а
    не уходим на Token Plan с другой моделью и другой моделью биллинга.
    """
    # Расход считаем с начала гранта: чужой, уже истёкший грант не должен закрывать
    # модель в текущем. Дата не задана — меряем по всей истории (см. `payg_grant_start`).
    totals = usage_totals(since=payg_grant_start())
    grant_expired = payg_grant_expired(now)
    chain = SYNTH_CHAIN if task == "synth" else BULK_CHAIN
    for model, endpoint in chain:
        if not _has_key(endpoint):
            continue
        if _room_left(model, endpoint, totals, grant_expired=grant_expired):
            return model, endpoint, "подтверждённая бесплатная квота pay-as-you-go"
    # Нет подтверждённого бесплатного места: держим модель класса задачи на её обычном
    # API-тарифе. Это предсказуемее и дешевле, чем неявно подменять её Token Plan.
    for model, endpoint in chain:
        if _has_key(endpoint):
            return model, endpoint, "pay-as-you-go list price (free grant unavailable)"
    raise ValueError("Нет ни одного ключа Qwen для маршрутизации")


def pick_endpoint(model: str, now: datetime | None = None) -> tuple[str, str]:
    """Выбирает (эндпоинт, причина) для **заданной** модели.

    Для ревью модель менять нельзя: она входит в ключ кэша ``llm_reviews``, и её смена
    обнуляет накопленные решения. Эндпоинт в ключ не входит — значит по нему выбор
    свободен, и одну и ту же модель можно взять там, где сейчас дешевле. Раньше этот
    выбор не делался вовсе, и ревью всегда шло по эвристике `_get_api_config`.

    Для сервиса endpoint всегда pay-as-you-go. Сначала отмечаем явно настроенный
    бесплатный грант; иначе возвращаем тот же endpoint с честной причиной list price.

    Модели нет ни на одном настроенном ключе — возвращаем пустой эндпоинт: пусть
    решает `_get_api_config`, а провайдер скажет о проблеме своим кодом ответа.
    """
    totals = usage_totals(since=payg_grant_start())
    grant_expired = payg_grant_expired(now)
    if _has_key("payg"):
        if _room_left(model, "payg", totals, grant_expired=grant_expired):
            return "payg", "подтверждённая бесплатная квота pay-as-you-go"
        return "payg", "pay-as-you-go list price (free grant unavailable)"
    return "", "ключей Qwen нет"
