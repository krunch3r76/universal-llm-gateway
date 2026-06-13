"""Unit tests for cursor-sdk closeout validation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.git_integration_worker.cursor_sdk_closeout import (
    MAX_TURN_BODY_CHARS,
    SdkRunOutcome,
    _extract_turn_number,
    build_closeout_idempotency_key,
    build_closeout_trigger_payload,
    build_implement_closeout_body,
    count_tool_calls,
    degraded_implement_reason,
    emit_implement_closeout_trigger,
    extract_source_ref_from_packet,
    format_delivery_fallback_body,
    infer_contract_from_text,
    prepare_closeout_delivery,
    resolve_prompt_preamble,
    sidecar_workspaces_ref,
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
    assert "architecture-invariants.md" in preamble


def test_build_implement_closeout_body_ok() -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=1500,
        tool_call_count=5,
    )
    sidecar_ref = sidecar_workspaces_ref("d1")
    body = build_implement_closeout_body(
        dispatch_id="d1",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="t1",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["schema_version"] == 1
    assert payload["status"] == "complete"
    assert payload["source_ref"] == sidecar_ref
    assert "5 tool calls" in payload["summary"]
    assert len(body) <= MAX_TURN_BODY_CHARS


def test_build_implement_closeout_body_degraded() -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=0,
    )
    body = build_implement_closeout_body(
        dispatch_id="d2",
        outcome=outcome,
        degraded_reason="zero_tool_calls",
        sidecar_ref=sidecar_workspaces_ref("d2"),
        result_bytes=4,
        thread_id="t2",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] == "partial"
    assert "zero_tool_calls" in payload["summary"]


def test_build_implement_closeout_body_run_failed() -> None:
    outcome = SdkRunOutcome(
        body="timeout",
        status="timeout",
        duration_ms=100,
        tool_call_count=0,
    )
    body = build_implement_closeout_body(
        dispatch_id="d3",
        outcome=outcome,
        degraded_reason="run_status=timeout",
        sidecar_ref=sidecar_workspaces_ref("d3"),
        result_bytes=7,
        thread_id="t3",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] == "failed"


def test_prepare_closeout_delivery_writes_sidecar_and_bounds_body(
    tmp_path: Path,
) -> None:
    outcome = SdkRunOutcome(
        body="x" * 8500,
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-big",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
    )
    assert len(delivery.body) <= MAX_TURN_BODY_CHARS
    assert delivery.sidecar_ref == sidecar_workspaces_ref("disp-big")
    assert delivery.sidecar_path.read_text(encoding="utf-8") == outcome.body
    assert delivery.sidecar_ref in delivery.body


def test_prepare_closeout_delivery_body_is_json(tmp_path: Path) -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-json",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
    )
    payload = json.loads(delivery.body)
    assert payload["schema_version"] == 1
    assert payload["source_ref"] == delivery.sidecar_ref


def test_prepare_closeout_delivery_degraded_sidecar(tmp_path: Path) -> None:
    outcome = SdkRunOutcome(
        body="Implementing",
        status="finished",
        duration_ms=50,
        tool_call_count=0,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-degraded",
        outcome=outcome,
        degraded_reason="zero_tool_calls",
        thread_id="t1",
        work_item_ref=None,
    )
    sidecar_text = delivery.sidecar_path.read_text(encoding="utf-8")
    assert sidecar_text.startswith("status: degraded\nreason: zero_tool_calls")
    assert "Implementing" in sidecar_text
    payload = json.loads(delivery.body)
    assert payload["status"] == "partial"
    assert "zero_tool_calls" in payload["summary"]


def test_trigger_closeout_from_turn_accepts_structured_body() -> None:
    from systems.frontier_consult.closeout_reply import trigger_closeout_from_turn

    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=1500,
        tool_call_count=5,
    )
    sidecar_ref = sidecar_workspaces_ref("d-closeout")
    body = build_implement_closeout_body(
        dispatch_id="d-closeout",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="t1",
        work_item_ref=None,
    )
    with patch(
        "systems.frontier_consult.closeout_reply.run_implement_closeout_pipeline",
        new=MagicMock(return_value={"ok": True}),
    ) as pipeline_mock:
        result = trigger_closeout_from_turn(
            thread_id="t1",
            body=body,
            tags=["contract:implement"],
        )
    pipeline_mock.assert_called_once()
    assert result == {"ok": True}
    closeout_arg = pipeline_mock.call_args[0][0]
    assert closeout_arg["schema_version"] == 1


def test_format_delivery_fallback_body() -> None:
    body = format_delivery_fallback_body(
        status_code=413,
        sidecar_ref=sidecar_workspaces_ref("disp-fail"),
        result_bytes=8375,
    )
    assert "status: delivery_failed" in body
    assert "bus_status_code: 413" in body
    assert len(body) <= MAX_TURN_BODY_CHARS


def test_extract_source_ref_from_packet_source_ref_line() -> None:
    assert (
        extract_source_ref_from_packet("---\nsource_ref: todo:foo\n---") == "todo:foo"
    )


def test_extract_source_ref_from_packet_todo_line() -> None:
    assert extract_source_ref_from_packet("---\ntodo: todo:bar\n---") == "todo:bar"


def test_extract_source_ref_from_packet_none() -> None:
    assert extract_source_ref_from_packet("---\ncontract: implement\n---\nbody") is None


def test_build_body_uses_work_item_ref() -> None:
    outcome = SdkRunOutcome(
        body="done", status="finished", duration_ms=1000, tool_call_count=3
    )
    sidecar_ref = sidecar_workspaces_ref("dx")
    body = build_implement_closeout_body(
        dispatch_id="dx",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="1865",
        work_item_ref="todo:x",
    )
    payload = json.loads(body)
    assert payload["source_ref"] == "todo:x"
    assert payload["evidence_uris"]["artifact_paths"] == [sidecar_ref]
    assert payload["evidence_uris"]["bus_threads"] == ["1865"]
    assert payload["evidence_uris"]["dispatch_ids"] == ["dx"]


def test_build_body_fallback_sidecar() -> None:
    outcome = SdkRunOutcome(
        body="done", status="finished", duration_ms=1000, tool_call_count=3
    )
    sidecar_ref = sidecar_workspaces_ref("dy")
    body = build_implement_closeout_body(
        dispatch_id="dy",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="1865",
        work_item_ref=None,
    )
    assert json.loads(body)["source_ref"] == sidecar_ref


def test_build_closeout_idempotency_key() -> None:
    key = build_closeout_idempotency_key(execution_id="E", thread_id="T", turn_number=5)
    assert key == "implement-closeout:E:T:5"


def test_build_closeout_trigger_payload() -> None:
    body_json = json.dumps({"schema_version": 1, "status": "complete"})
    payload = build_closeout_trigger_payload(
        body_json=body_json, source_ref="todo:x", idempotency_key="k"
    )
    assert payload["closeout"] == {"schema_version": 1, "status": "complete"}
    assert payload["source_ref"] == "todo:x"
    assert payload["idempotency_key"] == "k"


@pytest.mark.asyncio
async def test_emit_trigger_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("transport down")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.make_async_client",
        _boom,
    )
    result = await emit_implement_closeout_trigger(
        body_json=json.dumps({"status": "complete"}),
        source_ref="todo:x",
        idempotency_key="k",
    )
    assert result is None


def test_extract_turn_number() -> None:
    assert _extract_turn_number({"turn_number": 5}) == 5
    assert _extract_turn_number({"turn": {"turn_number": 7}}) == 7
    assert _extract_turn_number("x") is None
