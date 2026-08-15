"""Unit tests for frontier.sdk.worker.completed usage fields."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_events import (
    FrontierSdkWorkerCompleted,
    FrontierSdkWorkerDispatched,
    FrontierSdkWorkerResumed,
)


def test_completed_event_carries_usage_and_knobs() -> None:
    event = FrontierSdkWorkerCompleted(
        dispatch_id="d1",
        thread_id="t1",
        execution_id="e1",
        duration_s=12.5,
        tool_call_count=3,
        result_bytes=4096,
        outcome="ok",
        resolved_model="cursor/composer-2.5",
        model_knobs_requested={"fast": "true"},
        usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        usage_capture_status="captured",
    )
    assert event.signal == "frontier.sdk.worker.completed"
    assert event.payload["resolved_model"] == "cursor/composer-2.5"
    assert event.payload["model_knobs_requested"] == {"fast": "true"}
    assert event.payload["usage"]["total_tokens"] == 120
    assert event.payload["usage_capture_status"] == "captured"


def test_completed_event_null_usage_is_explicit() -> None:
    event = FrontierSdkWorkerCompleted(
        dispatch_id="d1",
        thread_id="t1",
        execution_id="e1",
        duration_s=1.0,
        tool_call_count=0,
        result_bytes=0,
        outcome="ok",
        resolved_model="cursor/composer-2.5",
        usage=None,
        usage_capture_status="missing",
    )
    assert event.payload["usage"] is None
    assert event.payload["usage_capture_status"] == "missing"
    assert "model_knobs_requested" not in event.payload


def test_dispatched_event_carries_request_id() -> None:
    event = FrontierSdkWorkerDispatched(
        dispatch_id="req1-abc12345",
        thread_id="5867",
        execution_id="exec-1",
        request_id="ledger-req-abc123",
        admitted_via="cursor-auto",
        seat="cursor-sdk",
    )
    assert event.signal == "frontier.sdk.worker.dispatched"
    assert event.payload["request_id"] == "ledger-req-abc123"
    assert event.payload["admitted_via"] == "cursor-auto"


def test_resumed_event_factory() -> None:
    event = FrontierSdkWorkerResumed(
        dispatch_id="child",
        resume_of="parent",
        sdk_agent_id="agent-1",
        state_root="/tmp/state",
        thread_id="t1",
        execution_id="e1",
    )
    assert event.signal == "frontier.sdk.worker.resumed"
    assert event.payload["resume_of"] == "parent"
    assert event.role == "observation"
