"""Run limits resolution, admit echo, preamble, and specimen stop-cause replay."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble
from services.git_integration_worker.cursor_sdk_run_limits import (
    RunLimitsRefusal,
    classify_stop_cause,
    classify_stop_from_closeout_row,
    extract_tool_call_budget_from_packet,
    format_run_limits_preamble_line,
    resolve_run_limits,
    undeclared_run_limits,
)


def test_specimen_replay_stop_cause_executor_ended() -> None:
    rows = (
        {
            "tool_call_count": 79,
            "stream_tool_call_count": 79,
            "status": "finished",
            "record_json": json.dumps({"run_budget_control": {}}),
        },
        {
            "tool_call_count": 121,
            "stream_tool_call_count": 121,
            "status": "finished",
            "record_json": json.dumps({"run_budget_control": {}}),
        },
    )
    for row in rows:
        assert classify_stop_from_closeout_row(row) == "executor_ended"


def test_admit_echoes_run_limits_undeclared() -> None:
    limits = resolve_run_limits(packet_text="", native_helper_enforcement="enforced")
    assert limits.tool_call_budget is None
    assert limits.to_record()["tool_call_budget"] is None


def test_preamble_includes_run_limits_line() -> None:
    undeclared = undeclared_run_limits(native_helper_enforcement="enforced")
    line = format_run_limits_preamble_line(undeclared)
    for handoff in ("implement", "bare", "freeform"):
        preamble = resolve_prompt_preamble(
            handoff_contract=handoff,
            prompt_preamble=None,
            inferred_contract=handoff,
            run_limits_line=line,
        )
        assert line in preamble, handoff
    declared = resolve_run_limits(
        packet_text="tool_call_budget: 40\n",
        native_helper_enforcement="enforced",
    )
    declared_line = format_run_limits_preamble_line(declared)
    for handoff in ("implement", "bare", "freeform"):
        preamble_declared = resolve_prompt_preamble(
            handoff_contract=handoff,
            prompt_preamble=None,
            inferred_contract=handoff,
            run_limits_line=declared_line,
        )
        assert "tool_call_budget=40" in preamble_declared, handoff


def test_budget_bounds_refuse_no_clamp() -> None:
    with pytest.raises(RunLimitsRefusal):
        resolve_run_limits(
            packet_text="tool_call_budget: 99999\n",
            native_helper_enforcement="enforced",
        )
    with pytest.raises(RunLimitsRefusal):
        resolve_run_limits(
            packet_text="tool_call_budget: not-int\n",
            native_helper_enforcement="enforced",
        )
    admitted = resolve_run_limits(
        packet_text="tool_call_budget: 80\n",
        native_helper_enforcement="enforced",
    )
    assert admitted.tool_call_budget == 80


def test_stop_cause_taxonomy_survives_shrink() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.bus_body_budget import (
        finalize_closeout_body,
    )

    payload = {
        "schema_version": 1,
        "status": "partial",
        "work_outcome": "unverified",
        "summary": "x" * 9000,
        "source_ref": "todo:test",
        "stop_cause": "executor_ended",
        "run_limits": {"tool_call_budget": None, "warn_at": None},
        "native_helper_enforcement": "enforced",
        "native_helper_attempts": [{"kind": "generalPurpose", "denied": True}],
    }
    body = finalize_closeout_body(json.dumps(payload))
    parsed = json.loads(body)
    assert parsed["stop_cause"] == "executor_ended"
    assert parsed["run_limits"]["tool_call_budget"] is None
    assert parsed["native_helper_enforcement"] == "enforced"


def test_extract_tool_call_budget_from_packet() -> None:
    assert extract_tool_call_budget_from_packet("tool_call_budget: 12\n") == "12"
    assert extract_tool_call_budget_from_packet("no budget line") is None


def test_classify_stop_cause_budget_exhausted() -> None:
    assert (
        classify_stop_cause(
            park_reason="tool-call-budget-exhausted",
            budget_exhausted=True,
        )
        == "budget_exhausted"
    )
