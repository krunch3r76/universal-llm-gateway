"""Unit tests for cursor-sdk closeout validation helpers."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    count_tool_calls,
    degraded_implement_reason,
    format_closeout_body,
    infer_contract_from_text,
    resolve_prompt_preamble,
)


def _step(step_type: str) -> object:
    return type("Step", (), {"type": step_type})()


def _turn(*step_types: str) -> object:
    steps = tuple(_step(step_type) for step_type in step_types)
    agent_turn = type("AgentTurn", (), {"steps": steps})()
    return type("ConversationTurn", (), {"turn": agent_turn})()


def test_count_tool_calls() -> None:
    turns = [
        _turn("thinking", "toolCall", "assistant"),
        _turn("assistant"),
        _turn("toolCall", "toolCall"),
    ]
    assert count_tool_calls(turns) == 3


def test_degraded_implement_zero_tool_calls() -> None:
    outcome = SdkRunOutcome(
        body="Implementing",
        status="finished",
        duration_ms=100,
        tool_call_count=0,
    )
    assert degraded_implement_reason(outcome) == "zero_tool_calls"


def test_degraded_implement_bad_status() -> None:
    outcome = SdkRunOutcome(
        body="oops",
        status="error",
        duration_ms=100,
        tool_call_count=2,
    )
    assert degraded_implement_reason(outcome) == "run_status=error"


def test_infer_contract_from_frontmatter() -> None:
    text = "---\ncontract: implement\n---\n<body>"
    assert infer_contract_from_text(text) == "implement"


def test_resolve_prompt_preamble_implement_fallback() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract=None,
        prompt_preamble=None,
        inferred_contract="implement",
    )
    assert "Execute this task NOW" in preamble


def test_format_closeout_body_degraded() -> None:
    outcome = SdkRunOutcome(
        body="Implementing",
        status="finished",
        duration_ms=50,
        tool_call_count=0,
    )
    body = format_closeout_body(outcome, "zero_tool_calls")
    assert body.startswith("status: degraded\nreason: zero_tool_calls\n\nImplementing")
