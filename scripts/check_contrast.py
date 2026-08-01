#!/usr/bin/env python3
"""Контраст токенов палитры по WCAG 2.1.

Палитра живёт в CSS и до сих пор проверялась только на глаз — так в неё попал
``--text-muted`` с отношением 2.4:1, применённый к футеру, вторичной навигации
и мета-строкам карточек. Скрипт читает токены прямо из ``app.css`` и считает
контраст для тех пар, которые реально встречаются в разметке, чтобы регресс
ловился до выкатки, а не по жалобе на «мелко и бледно».

Пороги WCAG 2.1: 4.5:1 — текст, 3:1 — крупный текст и границы интерактивных
элементов (1.4.11 Non-text Contrast).

    uv run python scripts/check_contrast.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CSS_PATH = Path(__file__).resolve().parents[1] / "src/reddit_compass/api/static/app.css"

TEXT_MIN = 4.5
NON_TEXT_MIN = 3.0

# (токен переднего плана, токен фона, минимум, где встречается)
PAIRS: list[tuple[str, str, float, str]] = [
    # Основной текст
    ("--text", "--bg", TEXT_MIN, "body"),
    ("--text", "--surface", TEXT_MIN, "карточки"),
    ("--text", "--surface-2", TEXT_MIN, "чипы, кнопки"),
    ("--text", "--surface-3", TEXT_MIN, "бейджи"),
    # Вторичный текст
    ("--text-secondary", "--bg", TEXT_MIN, "навигация, лид"),
    ("--text-secondary", "--surface", TEXT_MIN, "описания в карточках"),
    ("--text-secondary", "--surface-2", TEXT_MIN, "бейджи типа сигнала"),
    ("--text-secondary", "--surface-3", TEXT_MIN, "signal-type-badge"),
    # Тихий текст: футер, мета-строки, заголовки таблиц
    ("--text-muted", "--bg", TEXT_MIN, "футер, вторичная навигация"),
    ("--text-muted", "--surface", TEXT_MIN, "мета карточек, eyebrow"),
    ("--text-muted", "--surface-2", TEXT_MIN, "заголовки таблиц, kpi-label"),
    ("--text-muted", "--surface-3", TEXT_MIN, "pulse-self, pulse-flair"),
    # Акценты как текст ссылок
    ("--accent-bright", "--bg", TEXT_MIN, "ссылки"),
    ("--accent-bright", "--surface", TEXT_MIN, "ссылки в карточках"),
    ("--accent-2-bright", "--bg", TEXT_MIN, "ссылки при наведении"),
    ("--accent-2-bright", "--surface", TEXT_MIN, "topic-cloud-count"),
    # Статусы: бейджи набраны мелким, поэтому порог текстовый
    ("--success", "--surface", TEXT_MIN, "status-complete"),
    ("--warning", "--surface", TEXT_MIN, "status-partial"),
    ("--error", "--surface", TEXT_MIN, "status-error"),
    ("--success", "--surface-2", TEXT_MIN, "direction-up"),
    ("--warning", "--surface-2", TEXT_MIN, "status-note strong"),
    ("--error", "--surface-2", TEXT_MIN, "chip-pain"),
    # Границы: 1.4.11 — интерактивные элементы должны быть различимы
    ("--border-interactive", "--bg", NON_TEXT_MIN, "рамки полей и кнопок"),
    ("--border-interactive", "--surface", NON_TEXT_MIN, "рамки внутри карточек"),
]

_TOKEN_RE = re.compile(r"^\s*(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;", re.MULTILINE)


def _block(css: str, selector: str) -> str:
    start = css.index(selector)
    open_brace = css.index("{", start)
    depth, i = 0, open_brace
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_brace + 1 : i]
        i += 1
    raise ValueError(f"незакрытый блок {selector}")


def parse_themes(css: str) -> dict[str, dict[str, str]]:
    """Токены обеих тем. Светлая наследует всё, что не переопределила."""
    dark = dict(_TOKEN_RE.findall(_block(css, ":root")))
    light = dict(dark)
    light.update(dict(_TOKEN_RE.findall(_block(css, '[data-theme="light"]'))))
    return {"dark": dark, "light": light}


def _rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def relative_luminance(value: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    lighter, darker = sorted((relative_luminance(fg), relative_luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def main() -> int:
    themes = parse_themes(CSS_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []

    for theme, tokens in themes.items():
        print(f"\n{theme}")
        for fg, bg, minimum, where in PAIRS:
            if fg not in tokens or bg not in tokens:
                failures.append(f"{theme}: токен не найден — {fg} на {bg}")
                print(f"  ??    {fg:<22} на {bg:<14} — токен отсутствует")
                continue
            ratio = contrast_ratio(tokens[fg], tokens[bg])
            ok = ratio >= minimum
            if not ok:
                failures.append(
                    f"{theme}: {fg} на {bg} = {ratio:.2f}:1 при норме {minimum}:1 ({where})"
                )
            print(f"  {'ok ' if ok else 'FAIL'} {fg:<22} на {bg:<14} {ratio:5.2f}:1  {where}")

    if failures:
        print(f"\n{len(failures)} пар ниже нормы:")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("\nВсе пары проходят WCAG 2.1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
