"""Дизайн-система как проверяемый контракт.

CSS рос дописыванием правил под каждую новую страницу, и к моменту ревью в нём
было 23 размера шрифта (63% мельче 14px), 18 значений gap с шагами по 0.8px
и токен ``--text-primary``, которого никто никогда не объявлял. Всё это
невидимо для остальных тестов: страница рендерится, просто выглядит плохо.

Здесь проверяется то, что нельзя увидеть в HTML: значения идут через шкалу,
токены существуют, контраст проходит WCAG.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

CSS_PATH = Path(__file__).resolve().parents[1] / "src/reddit_compass/api/static/app.css"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

CSS = CSS_PATH.read_text(encoding="utf-8")

SPACING_PROPS = (
    "gap",
    "row-gap",
    "column-gap",
    "margin",
    "margin-top",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "padding",
    "padding-top",
    "padding-bottom",
    "padding-left",
    "padding-right",
)


def _declared_tokens() -> set[str]:
    return set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", CSS, re.MULTILINE))


def test_font_sizes_go_through_the_scale() -> None:
    values = [v.strip() for v in re.findall(r"^\s*font-size:\s*([^;]+);", CSS, re.MULTILINE)]
    literals = [v for v in values if not v.startswith("var(")]
    assert literals == [], f"размер шрифта мимо шкалы: {literals}"


def test_scale_stays_small() -> None:
    """Восемь ступеней — предел, за которым шкала снова перестаёт быть шкалой."""
    used = set(re.findall(r"font-size:\s*var\((--[a-z0-9-]+)\)", CSS))
    assert len(used) <= 8, f"ступеней размера {len(used)}: {sorted(used)}"


def test_spacing_goes_through_the_scale() -> None:
    pattern = rf"^\s*(?:{'|'.join(SPACING_PROPS)}):\s*[^;]*?\d\.?\d*rem"
    literals = re.findall(pattern, CSS, re.MULTILINE)
    # calc() с токеном внутри — легальный способ задать отрицательный отступ.
    literals = [line for line in literals if "var(" not in line]
    assert literals == [], f"интервалы мимо шкалы: {literals}"


def test_every_referenced_token_is_declared() -> None:
    """``--text-primary`` использовался дважды и не существовал ни разу."""
    declared = _declared_tokens()
    referenced = set(re.findall(r"var\((--[a-z0-9-]+)", CSS))
    missing = sorted(referenced - declared)
    assert missing == [], f"токены используются, но не объявлены: {missing}"


def test_no_hardcoded_fallback_palette() -> None:
    """``var(--accent, #bd39de)`` при реальном ``--accent: #446eb2``.

    Fallback не срабатывал никогда, но хранил вторую версию палитры, которая
    молча расходилась с настоящей.
    """
    fallbacks = re.findall(r"var\(--[a-z0-9-]+,\s*#[0-9a-fA-F]{3,8}\)", CSS)
    assert fallbacks == [], f"вторая палитра в fallback: {fallbacks}"


def test_contrast_passes_wcag() -> None:
    sys.path.insert(0, str(SCRIPTS))
    try:
        import check_contrast
    except ImportError:  # pragma: no cover - скрипт лежит рядом, но путь может отличаться
        pytest.skip("scripts/check_contrast.py недоступен")

    themes = check_contrast.parse_themes(CSS)
    failures = []
    for theme, tokens in themes.items():
        for fg, bg, minimum, where in check_contrast.PAIRS:
            assert fg in tokens and bg in tokens, f"{theme}: нет токена {fg} или {bg}"
            ratio = check_contrast.contrast_ratio(tokens[fg], tokens[bg])
            if ratio < minimum:
                failures.append(f"{theme}: {fg} на {bg} = {ratio:.2f}:1 < {minimum} ({where})")
    assert failures == [], "\n".join(failures)


def test_chip_cloud_does_not_explode_a_string_into_characters() -> None:
    """Партиал не должен превращать порчу данных в чипы по символу.

    На проде у тренда с испорченным ``domain_ids`` в карточке выводились
    отдельные ``"``, ``,``, ``[``, ``_`` — Jinja честно итерировала строку.
    Причина чинится в trend_discovery, но шаблон не обязан ей доверять.
    """
    from reddit_compass.api.ui import templates

    template = templates.env.get_template("components/chip_cloud.html")

    broken = template.render(domain_ids='["a","b"]', project_scores="{}")
    assert "chip" not in broken.replace('class="chip-cloud"', "")

    healthy = template.render(domain_ids=["ai_technology"], project_scores={"book": 7})
    assert "ai_technology" in healthy
    assert "book 7" in healthy


def test_source_bar_shows_composition_not_just_a_badge() -> None:
    """Состав охвата — главное отличие продукта, и он должен быть измеримым.

    Раньше это был бейдж «🔗 Reddit + СМИ»: по нему нельзя понять, событие
    держится на одном сабреддите или его подхватили четыре издания.
    """
    from reddit_compass.api.ui import templates

    template = templates.env.get_template("components/source_bar.html")
    html = template.render(
        source_clusters={"voices": 19, "mainstream": 4, "developers": 2},
        source_scope="cross_source",
    )

    # Доли считаются от суммы, а не от числа кластеров.
    assert "76.0%" in html  # 19 из 25
    assert "16.0%" in html
    # Полоса не опирается на один цвет: подписи и числа присутствуют.
    assert "🗣 Голоса" in html
    assert "<b>19</b>" in html
    assert 'title="🗣 Голоса — 19 из 25"' in html


def test_source_bar_names_the_perspective_gap() -> None:
    """Сюжет, живущий только на Reddit, — наш аналог Blindspot."""
    from reddit_compass.api.ui import templates

    template = templates.env.get_template("components/source_bar.html")
    html = template.render(source_clusters={"voices": 12}, source_scope="community_only")

    assert "крупные СМИ об этом молчат" in html
    assert "source-gap" in html


def test_source_bar_stays_silent_without_data() -> None:
    from reddit_compass.api.ui import templates

    template = templates.env.get_template("components/source_bar.html")
    assert template.render(source_clusters={}, source_scope="").strip() == ""


def test_item_row_encodes_weight_in_the_row_itself() -> None:
    """Вес события читается до текста, а не выискивается среди метаданных."""
    from reddit_compass.api.ui import templates

    template = templates.env.get_template("components/item_row.html")

    major = template.render(row_url="/t/1", row_title="Крупное", row_weight=6)
    minor = template.render(row_url="/t/2", row_title="Мелкое", row_weight=1)

    assert "item-row-major" in major
    assert "item-row-major" not in minor
    assert "item-row-notable" not in minor


def test_item_row_drops_summary_that_repeats_the_title() -> None:
    from reddit_compass.api.ui import templates

    template = templates.env.get_template("components/item_row.html")
    html = template.render(row_url="/s/1", row_title="Одно и то же", row_summary="Одно и то же")

    assert html.count("Одно и то же") == 1
    assert "item-row-summary" not in html
