from reddit_compass.intelligence.entities import extract_structured_event_frame


def test_event_frame_fallback_keeps_numbers_and_drops_generic_anchors() -> None:
    frame, entities, backend = extract_structured_event_frame(
        title="OpenAI cuts 1,500 roles after $2 billion restructuring",
        excerpt="",
        event_date="2026-07-29",
        fallback_entities=["open ai", "AI", "CEO"],
        use_spacy=False,
    )

    assert backend == "deterministic_fallback"
    assert entities == ["openai"]
    assert frame["event_date"] == "2026-07-29"
    assert frame["action"] == "layoff"
    assert "1,500" in frame["numbers"]
    assert "$2 billion" in frame["numbers"]


def test_event_frame_fallback_extracts_action_and_geography_hints() -> None:
    frame, _, backend = extract_structured_event_frame(
        title="US bans new Chinese humanoid robots after security warning",
        excerpt="",
        event_date="2026-07-29",
        fallback_entities=["Chinese"],
        use_spacy=False,
    )

    assert backend == "deterministic_fallback"
    assert frame["action"] == "ban"
    assert "china" in frame["geography"]
    assert "united states" in frame["geography"]
