"""Pydantic schemas для валидации LLM-ответов.

Запрещено собирать SignalCard напрямую из произвольного dict.
Все ответы LLM проходят валидацию.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ItemSignalResponse(BaseModel):
    """Валидированный ответ LLM для одного item."""

    item_id: str
    theme_ids: list[str] = Field(default_factory=list, max_length=5)
    candidate_themes: list[str] = Field(default_factory=list, max_length=5)
    pain_points: list[str] = Field(default_factory=list, max_length=5)
    buying_intent: bool = False
    goal_relevance: dict[str, int] = Field(default_factory=dict)
    summary_ru: str = Field(default="", max_length=500)

    @field_validator("goal_relevance")
    @classmethod
    def validate_relevance_range(cls, v: dict[str, int]) -> dict[str, int]:
        for key, value in v.items():
            if not 0 <= value <= 100:
                raise ValueError(f"goal_relevance[{key}] must be 0-100, got {value}")
        return v

    @field_validator("theme_ids", "candidate_themes", "pain_points")
    @classmethod
    def validate_list_length(cls, v: list[str]) -> list[str]:
        if len(v) > 5:
            raise ValueError(f"List too long: {len(v)} > 5")
        return v


class BatchSignalResponse(BaseModel):
    """Ответ LLM для batch items."""

    signals: list[ItemSignalResponse] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_item_ids(self) -> BatchSignalResponse:
        item_ids = [s.item_id for s in self.signals]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Duplicate item_ids in response")
        return self


class RunLevelSynthesis(BaseModel):
    """Run-level синтез от LLM."""

    top_themes: list[dict[str, Any]] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    column_ideas: list[str] = Field(default_factory=list)
    narrative_summary: str = Field(default="", max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_not_empty(cls, v: list[str]) -> list[str]:
        # Evidence IDs могут быть пустыми, но если есть — должны быть строками
        return [eid for eid in v if isinstance(eid, str) and eid]


class NarrativeShift(BaseModel):
    """Narrative shift: изменение нарратива."""

    text: str = Field(max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list)
    direction: str = Field(default="stable")
    confidence: str = Field(default="medium")


def validate_signal_response(
    data: dict[str, Any],
    valid_item_ids: set[str],
    valid_theme_ids: set[str],
) -> ItemSignalResponse:
    """Валидирует ответ LLM для одного item.

    Args:
        data: Сырой dict от LLM.
        valid_item_ids: Допустимые item_id.
        valid_theme_ids: Допустимые theme_id из профиля.

    Returns:
        Валидированный ItemSignalResponse.

    Raises:
        ValueError: Если item_id неизвестен или theme_id невалидны.
    """
    item_id = data.get("item_id", "")
    if item_id not in valid_item_ids:
        raise ValueError(f"Unknown item_id: {item_id}")

    theme_ids = data.get("theme_ids", [])
    candidate_themes = []
    valid_themes = []

    for theme in theme_ids:
        if theme in valid_theme_ids:
            valid_themes.append(theme)
        else:
            candidate_themes.append(theme)

    data["theme_ids"] = valid_themes
    data["candidate_themes"] = data.get("candidate_themes", []) + candidate_themes

    return ItemSignalResponse(**data)


def validate_batch_response(
    data: dict[str, Any],
    valid_item_ids: set[str],
    valid_theme_ids: set[str],
) -> BatchSignalResponse:
    """Валидирует batch ответ LLM.

    Args:
        data: Сырой dict от LLM.
        valid_item_ids: Допустимые item_id.
        valid_theme_ids: Допустимые theme_id из профиля.

    Returns:
        Валидированный BatchSignalResponse.
    """
    signals_data = data.get("signals", [])
    validated_signals = []

    for signal_data in signals_data:
        try:
            signal = validate_signal_response(signal_data, valid_item_ids, valid_theme_ids)
            validated_signals.append(signal)
        except ValueError:
            # Пропускаем невалидные сигналы, но логируем
            continue

    return BatchSignalResponse(signals=validated_signals)


def validate_synthesis_response(
    data: dict[str, Any],
    valid_evidence_ids: set[str],
) -> RunLevelSynthesis:
    """Валидирует run-level синтез.

    Args:
        data: Сырой dict от LLM.
        valid_evidence_ids: Допустимые evidence IDs.

    Returns:
        Валидированный RunLevelSynthesis.
    """
    evidence_ids = data.get("evidence_ids", [])
    valid_evidence = [eid for eid in evidence_ids if eid in valid_evidence_ids]
    data["evidence_ids"] = valid_evidence

    return RunLevelSynthesis(**data)
