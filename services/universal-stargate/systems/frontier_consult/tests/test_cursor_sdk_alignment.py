"""Tests for cursor-sdk knob alignment at Stargate admission."""

from __future__ import annotations

from typing import Any

import pytest

from systems.frontier_consult.cursor_sdk_alignment import align_cursor_knobs


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    events: dict[str, list[Any]] = {"cost_risk": [], "knob_dropped": []}

    def _cost(**kwargs: Any) -> None:
        events["cost_risk"].append(kwargs)

    def _dropped(**kwargs: Any) -> None:
        events["knob_dropped"].append(kwargs)

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_alignment.emit_sdk_cost_risk_warning",
        _cost,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_alignment.emit_sdk_knob_dropped",
        _dropped,
    )
    return events


def test_opus_effort_low_accepted(captured_events: dict[str, list[Any]]) -> None:
    result = align_cursor_knobs(
        resolved_model="cursor/claude-opus-4-8",
        contract="light-bounded",
        model_knobs={"effort": "low"},
    )
    assert result.aligned_knobs == {"effort": "low"}
    assert result.knob_resolution["effort"].status == "accepted"
    assert result.knob_resolution["effort"].forwarded == "low"
    cost_warnings = [w for w in result.warnings if w.code == "sdk_cost_risk"]
    assert len(cost_warnings) == 1
    assert cost_warnings[0].suggested_knobs is None


def test_sonnet_fast_dropped_unsupported(captured_events: dict[str, list[Any]]) -> None:
    result = align_cursor_knobs(
        resolved_model="claude-sonnet-5",
        contract="light-bounded",
        model_knobs={"fast": "true"},
    )
    assert result.aligned_knobs == {}
    resolution = result.knob_resolution_as_dicts()["fast"]
    assert resolution["status"] == "dropped_unsupported"
    assert resolution["forwarded"] is None
    assert result.warnings[0].to_dict()["code"] == "knob_dropped"
    assert captured_events["knob_dropped"][0]["reason"] == "unsupported"


def test_opus_context_200k_invalid_value(captured_events: dict[str, list[Any]]) -> None:
    result = align_cursor_knobs(
        resolved_model="claude-opus-4-8",
        contract="light-bounded",
        model_knobs={"context": "200k"},
    )
    resolution = result.knob_resolution_as_dicts()["context"]
    assert resolution["status"] == "invalid_value"
    assert resolution["forwarded"] is None
    assert resolution["supported"] == ["300k", "1m"]
    assert captured_events["knob_dropped"][0]["reason"] == "invalid_value"


def test_cost_risk_structured_suggestion(captured_events: dict[str, list[Any]]) -> None:
    result = align_cursor_knobs(
        resolved_model="cursor/claude-opus-4-8",
        contract="pure-mechanical",
    )
    warning = result.warnings[0].to_dict()
    assert warning["code"] == "sdk_cost_risk"
    assert warning["suggested_knobs"] == {"effort": "low", "thinking": "false"}
    assert warning["suggested_model"] == "composer-2.5"
    assert result.aligned_knobs == {}
    assert captured_events["cost_risk"][0]["suppressed"] is False


def test_cost_risk_no_suggestion_when_explicit_effort(
    captured_events: dict[str, list[Any]],
) -> None:
    result = align_cursor_knobs(
        resolved_model="claude-opus-4-8",
        contract="light-bounded",
        model_knobs={"effort": "high"},
    )
    warning = result.warnings[0].to_dict()
    assert warning["code"] == "sdk_cost_risk"
    assert "suggested_knobs" not in warning
    assert "suggested_model" not in warning
    assert result.aligned_knobs == {"effort": "high"}


def test_cost_intent_suppresses_caller_warning(
    captured_events: dict[str, list[Any]],
) -> None:
    result = align_cursor_knobs(
        resolved_model="claude-opus-4-8",
        contract="light-bounded",
        cost_intent="deliberate_high_cost",
        cost_intent_reason="operator approved",
    )
    assert result.warnings == []
    event = captured_events["cost_risk"][0]
    assert event["suppressed"] is True
    assert event["suppression_reason"] == "cost_intent=deliberate_high_cost"
    assert event["cost_intent_reason"] == "operator approved"


def test_suppress_cost_warning_flag(captured_events: dict[str, list[Any]]) -> None:
    result = align_cursor_knobs(
        resolved_model="claude-fable-5",
        contract="pure-mechanical",
        suppress_cost_warning=True,
    )
    assert result.warnings == []
    event = captured_events["cost_risk"][0]
    assert event["suppressed"] is True
    assert event["suppression_reason"] == "suppress_cost_warning=true"


def test_align_cursor_knobs_rejects_reasoning_effort_kwarg() -> None:
    """BIND_B: reasoning_effort no longer accepted on align_cursor_knobs."""
    with pytest.raises(TypeError):
        align_cursor_knobs(  # type: ignore[call-arg]
            resolved_model="composer-2.5",
            contract="light-bounded",
            reasoning_effort="high",
        )


def test_implement_contract_no_cost_warning(
    captured_events: dict[str, list[Any]],
) -> None:
    result = align_cursor_knobs(
        resolved_model="claude-opus-4-8",
        contract="implement",
    )
    assert result.warnings == []
    assert captured_events["cost_risk"] == []


def test_wire_shapes_match_fork_c(captured_events: dict[str, list[Any]]) -> None:
    result = align_cursor_knobs(
        resolved_model="claude-opus-4-8",
        contract="light-bounded",
        model_knobs={"effort": "low", "context": "200k"},
    )
    warnings = result.warnings_as_dicts()
    knob_resolution = result.knob_resolution_as_dicts()
    assert all("code" in warning and "message" in warning for warning in warnings)
    for entry in knob_resolution.values():
        assert set(entry) == {"status", "requested", "forwarded", "supported"}
