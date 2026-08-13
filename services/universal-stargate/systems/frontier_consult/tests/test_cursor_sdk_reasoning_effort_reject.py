"""BIND_B — hard-422 non-empty reasoning_effort on cursor-sdk prepare."""

from __future__ import annotations

from typing import Any

import pytest

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cursor_sdk_reasoning_effort_reject import (
    reject_nonempty_reasoning_effort,
)


@pytest.fixture
def captured_rejects(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def _emit(**kwargs: Any) -> None:
        events.append(kwargs)

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_reasoning_effort_reject"
        ".emit_sdk_reasoning_effort_rejected",
        _emit,
    )
    return events


def test_empty_and_none_admit(captured_rejects: list[dict[str, Any]]) -> None:
    reject_nonempty_reasoning_effort(
        request_id="r1",
        resolved_model="cursor/claude-opus-5",
        reasoning_effort=None,
    )
    reject_nonempty_reasoning_effort(
        request_id="r1",
        resolved_model="cursor/claude-opus-5",
        reasoning_effort="",
    )
    reject_nonempty_reasoning_effort(
        request_id="r1",
        resolved_model="cursor/claude-opus-5",
        reasoning_effort="   ",
    )
    assert captured_rejects == []


def test_opus_low_suggests_effort(captured_rejects: list[dict[str, Any]]) -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        reject_nonempty_reasoning_effort(
            request_id="r1",
            resolved_model="cursor/claude-opus-5",
            reasoning_effort="low",
        )
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "reasoning_effort_not_supported"
    assert err.field == "reasoning_effort"
    assert "model_knobs" in err.reason
    assert err.details is not None
    assert err.details["suggested_model_knobs"] == {"effort": "low"}
    assert err.details["knob"] == "effort"
    assert err.details["model"] == "claude-opus-5"
    assert captured_rejects == [{"model_id": "claude-opus-5", "requested": "low"}]


def test_gpt_sol_remedy_names_reasoning(
    captured_rejects: list[dict[str, Any]],
) -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        reject_nonempty_reasoning_effort(
            request_id="r1",
            resolved_model="cursor/gpt-5.6-sol",
            reasoning_effort="low",
        )
    details = exc_info.value.details
    assert details is not None
    assert details["knob"] == "reasoning"
    assert details["suggested_model_knobs"] == {"reasoning": "low"}
    assert captured_rejects[0]["model_id"] == "gpt-5.6-sol"


def test_grok_max_lists_supported_no_forward(
    captured_rejects: list[dict[str, Any]],
) -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        reject_nonempty_reasoning_effort(
            request_id="r1",
            resolved_model="cursor/grok-4.6",
            reasoning_effort="max",
        )
    details = exc_info.value.details
    assert details is not None
    assert details["supported"] == ["low", "medium", "high"]
    assert details["suggested_model_knobs"] == {}
    assert "effort" not in details["suggested_model_knobs"]
    assert captured_rejects[0]["requested"] == "max"


def test_composer_and_gemini_empty_suggested(
    captured_rejects: list[dict[str, Any]],
) -> None:
    for model in ("cursor/composer-2.5", "cursor/gemini-3.6-flash"):
        with pytest.raises(FrontierEndpointError) as exc_info:
            reject_nonempty_reasoning_effort(
                request_id="r1",
                resolved_model=model,
                reasoning_effort="low",
            )
        details = exc_info.value.details
        assert details is not None
        assert details["suggested_model_knobs"] == {}
        assert details["knob"] is None
        assert "no effort-like knob" in exc_info.value.reason
    assert len(captured_rejects) == 2


def test_aligned_knobs_never_gain_mapped_key() -> None:
    """Reject path does not call align; ensure details never imply mapping."""
    with pytest.raises(FrontierEndpointError) as exc_info:
        reject_nonempty_reasoning_effort(
            request_id="r1",
            resolved_model="cursor/claude-opus-5",
            reasoning_effort="high",
        )
    details = exc_info.value.details
    assert details is not None
    assert details["use"] == "model_knobs"
    assert "aligned_knobs" not in details
