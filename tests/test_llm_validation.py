"""Тесты LLM validation (intelligence/llm_schemas.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reddit_compass.intelligence.llm_schemas import (
    BatchSignalResponse,
    ItemSignalResponse,
    RunLevelSynthesis,
    validate_batch_response,
    validate_signal_response,
    validate_synthesis_response,
)


class TestItemSignalResponse:
    def test_valid_response(self):
        data = {
            "item_id": "reddit:123",
            "theme_ids": ["ai_agents", "labor"],
            "pain_points": ["Job loss anxiety"],
            "buying_intent": True,
            "goal_relevance": {"book": 80, "rbc": 60},
            "summary_ru": "Тестовый сигнал",
        }
        signal = ItemSignalResponse(**data)
        assert signal.item_id == "reddit:123"
        assert len(signal.theme_ids) == 2

    def test_invalid_relevance_range(self):
        data = {
            "item_id": "reddit:123",
            "goal_relevance": {"book": 150},
        }
        with pytest.raises(ValidationError):
            ItemSignalResponse(**data)

    def test_negative_relevance(self):
        data = {
            "item_id": "reddit:123",
            "goal_relevance": {"book": -10},
        }
        with pytest.raises(ValidationError):
            ItemSignalResponse(**data)

    def test_summary_max_length(self):
        data = {
            "item_id": "reddit:123",
            "summary_ru": "x" * 600,
        }
        with pytest.raises(ValidationError):
            ItemSignalResponse(**data)

    def test_theme_ids_max_length(self):
        data = {
            "item_id": "reddit:123",
            "theme_ids": ["a", "b", "c", "d", "e", "f"],
        }
        with pytest.raises(ValidationError):
            ItemSignalResponse(**data)

    def test_defaults(self):
        signal = ItemSignalResponse(item_id="reddit:123")
        assert signal.theme_ids == []
        assert signal.buying_intent is False
        assert signal.summary_ru == ""


class TestBatchSignalResponse:
    def test_valid_batch(self):
        data = {
            "signals": [
                {"item_id": "reddit:1"},
                {"item_id": "reddit:2"},
            ]
        }
        batch = BatchSignalResponse(**data)
        assert len(batch.signals) == 2

    def test_duplicate_item_ids(self):
        data = {
            "signals": [
                {"item_id": "reddit:1"},
                {"item_id": "reddit:1"},
            ]
        }
        with pytest.raises(ValidationError):
            BatchSignalResponse(**data)


class TestValidateSignalResponse:
    def test_valid_item_id(self):
        data = {"item_id": "reddit:123", "theme_ids": ["ai_agents"]}
        valid_ids = {"reddit:123"}
        valid_themes = {"ai_agents", "labor"}

        signal = validate_signal_response(data, valid_ids, valid_themes)
        assert signal.item_id == "reddit:123"
        assert signal.theme_ids == ["ai_agents"]

    def test_unknown_item_id(self):
        data = {"item_id": "unknown:999"}
        valid_ids = {"reddit:123"}

        with pytest.raises(ValueError, match="Unknown item_id"):
            validate_signal_response(data, valid_ids, set())

    def test_unknown_theme_moves_to_candidate(self):
        data = {"item_id": "reddit:123", "theme_ids": ["ai_agents", "unknown_theme"]}
        valid_ids = {"reddit:123"}
        valid_themes = {"ai_agents"}

        signal = validate_signal_response(data, valid_ids, valid_themes)
        assert signal.theme_ids == ["ai_agents"]
        assert "unknown_theme" in signal.candidate_themes


class TestValidateBatchResponse:
    def test_skips_invalid_signals(self):
        data = {
            "signals": [
                {"item_id": "reddit:1"},
                {"item_id": "unknown:999"},
                {"item_id": "reddit:2"},
            ]
        }
        valid_ids = {"reddit:1", "reddit:2"}

        batch = validate_batch_response(data, valid_ids, set())
        assert len(batch.signals) == 2


class TestValidateSynthesisResponse:
    def test_filters_invalid_evidence(self):
        data = {
            "top_themes": [],
            "evidence_ids": ["reddit:1", "unknown:999", "reddit:2"],
        }
        valid_evidence = {"reddit:1", "reddit:2"}

        synthesis = validate_synthesis_response(data, valid_evidence)
        assert synthesis.evidence_ids == ["reddit:1", "reddit:2"]

    def test_empty_evidence(self):
        data = {"top_themes": [], "evidence_ids": []}
        synthesis = validate_synthesis_response(data, set())
        assert synthesis.evidence_ids == []


class TestRunLevelSynthesis:
    def test_valid_synthesis(self):
        data = {
            "top_themes": [{"theme": "AI agents", "count": 10}],
            "pain_points": ["Job anxiety"],
            "column_ideas": ["AI и будущее работы"],
            "narrative_summary": "Основной нарратив недели...",
            "evidence_ids": ["reddit:1"],
        }
        synthesis = RunLevelSynthesis(**data)
        assert len(synthesis.top_themes) == 1
        assert synthesis.narrative_summary != ""

    def test_narrative_max_length(self):
        data = {
            "narrative_summary": "x" * 3000,
        }
        with pytest.raises(ValidationError):
            RunLevelSynthesis(**data)
