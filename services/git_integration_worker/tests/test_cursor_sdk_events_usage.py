"""Unit tests for frontier.sdk.worker.completed usage fields."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_sdk_events import (
    FrontierSdkWorkerCompleted,
    FrontierSdkWorkerDispatched,
    FrontierSdkWorkerQueued,
    FrontierSdkWorkerResumed,
    emit_sdk_worker_completed,
    emit_sdk_worker_failed,
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


def test_completed_event_carries_stream_forensics_fields() -> None:
    provider_status = {
        "agent_id": "agent-1",
        "run_id": "run-1",
        "status": "ERROR",
        "message": "aborted",
        "at": "2026-10-06T15:42:00Z",
    }
    event = FrontierSdkWorkerCompleted(
        dispatch_id="d1",
        thread_id="t1",
        execution_id="e1",
        duration_s=180.0,
        tool_call_count=0,
        result_bytes=0,
        outcome="degraded",
        resolved_model="cursor/composer-2.5",
        provider_status=provider_status,
        first_output_s=12.5,
        last_output_s=45.0,
        first_toolcall_s=None,
    )
    assert event.payload["provider_status"] == provider_status
    assert event.payload["first_output_s"] == 12.5
    assert event.payload["last_output_s"] == 45.0
    assert "first_toolcall_s" not in event.payload


def test_failed_event_carries_forensics_from_abort_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events._emit",
        captured.append,
    )
    forensics = {
        "cause": "ReadTimeout",
        "provider_status": {
            "agent_id": "agent-1",
            "run_id": "run-1",
            "status": "ERROR",
            "message": "canceled",
            "at": "2026-10-06T15:42:00Z",
        },
        "first_output_s": 5.0,
        "last_output_s": 170.0,
    }
    emit_sdk_worker_failed(
        dispatch_id="d1",
        thread_id="t1",
        execution_id="e1",
        error="bridge abort",
        stream_forensics=forensics,
    )
    assert len(captured) == 1
    event = captured[0]
    assert event.signal == "frontier.sdk.worker.failed"
    assert event.payload["first_output_s"] == 5.0
    assert event.payload["last_output_s"] == 170.0
    assert event.payload["provider_status"]["run_id"] == "run-1"
    assert "first_toolcall_s" not in event.payload


def test_emit_completed_wrapper_forwards_stream_forensics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events._emit",
        captured.append,
    )
    emit_sdk_worker_completed(
        dispatch_id="d1",
        thread_id="t1",
        execution_id="e1",
        duration_s=1.0,
        tool_call_count=0,
        result_bytes=0,
        outcome="ok",
        resolved_model="cursor/composer-2.5",
        first_output_s=0.5,
        first_toolcall_s=2.0,
    )
    event = captured[0]
    assert event.payload["first_output_s"] == 0.5
    assert event.payload["first_toolcall_s"] == 2.0
    assert "last_output_s" not in event.payload


def test_outcome_stream_fields_omit_raw_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """a:38390 — error outcomes must not pass provider_error into completed emit."""
    from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
        SdkRunOutcome,
    )
    from services.git_integration_worker.routes.cursor_sdk import (
        _outcome_stream_event_fields,
    )

    outcome = SdkRunOutcome(
        body="",
        status="error",
        duration_ms=141500,
        tool_call_count=0,
        provider_error="You've hit your usage limit",
        provider_status={
            "status": "ERROR",
            "message": "You've hit your usage limit",
        },
        first_output_s=1.0,
        last_output_s=2.0,
        first_toolcall_s=None,
        measures_interaction_output_offsets=True,
    )
    fields = _outcome_stream_event_fields(outcome)
    assert "provider_error" not in fields
    assert fields["provider_status"]["status"] == "ERROR"
    assert fields["first_output_s"] == 1.0

    captured: list[object] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events._emit",
        captured.append,
    )
    emit_sdk_worker_completed(
        dispatch_id="fa7fc6b6ddf5-2908a0e3",
        thread_id="15459",
        execution_id="e1",
        duration_s=141.5,
        tool_call_count=0,
        result_bytes=43,
        outcome="error",
        resolved_model="cursor/composer-2.5",
        provider_error_class="usage_limit",
        **fields,
    )
    assert len(captured) == 1
    assert captured[0].payload["provider_error_class"] == "usage_limit"
    assert "provider_error" not in captured[0].payload


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
        admitted_via="stargate",
        seat="cursor-sdk",
    )
    assert event.signal == "frontier.sdk.worker.dispatched"
    assert event.payload["request_id"] == "ledger-req-abc123"
    assert event.payload["admitted_via"] == "stargate"


def test_dispatched_event_carries_topic_and_nest_under() -> None:
    event = FrontierSdkWorkerDispatched(
        dispatch_id="req1-abc12345",
        thread_id="5867",
        execution_id="exec-1",
        request_id="ledger-req-abc123",
        admitted_via="stargate",
        topic="ULG gains a glanceable dispatch topic",
        nest_under="parent-disp",
    )
    assert event.payload["topic"] == "ULG gains a glanceable dispatch topic"
    assert event.payload["nest_under"] == "parent-disp"


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
    assert "workspace_inherited_from" not in event.payload


def test_resumed_event_records_inherited_workspace() -> None:
    """a:37533 — omitted workspace filled from parent must be visible on resume."""
    event = FrontierSdkWorkerResumed(
        dispatch_id="child",
        resume_of="parent",
        sdk_agent_id="agent-1",
        state_root="/tmp/state",
        thread_id="t1",
        execution_id="e1",
        workspace_inherited_from="parent",
    )
    assert event.payload["workspace_inherited_from"] == "parent"


def test_queued_event_records_inherited_workspace() -> None:
    omitted = FrontierSdkWorkerQueued(
        dispatch_id="d1",
        thread_id="t1",
        source_repo="/tmp/hub",
        queue_position=0,
    )
    assert "workspace_inherited_from" not in omitted.payload
    inherited = FrontierSdkWorkerQueued(
        dispatch_id="d1",
        thread_id="t1",
        source_repo="/mnt/torus/projects/cryptax",
        queue_position=0,
        workspace_inherited_from="parent",
    )
    assert inherited.payload["workspace_inherited_from"] == "parent"


def test_lane_selected_records_inherited_workspace() -> None:
    from services.git_integration_worker.cursor_sdk_events import SdkLaneSelected

    omitted = SdkLaneSelected(
        dispatch_id="d1",
        thread_id="t1",
        lane="B",
        reason="explicit",
        regime_active=True,
        contract="conductor",
        selecting_predicate="lane=B",
    )
    assert "workspace_inherited_from" not in omitted.payload
    inherited = SdkLaneSelected(
        dispatch_id="d1",
        thread_id="t1",
        lane="B",
        reason="explicit",
        regime_active=True,
        contract="conductor",
        selecting_predicate="lane=B",
        workspace_inherited_from="parent",
    )
    assert inherited.payload["workspace_inherited_from"] == "parent"


def test_emit_wrappers_forward_inherit_and_packet_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper kwargs must reach the factory — packet_kind used to be dropped."""
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_selected,
        emit_sdk_worker_queued,
        emit_sdk_worker_resumed,
    )
    from services.git_integration_worker.cursor_sdk_park_events import (
        emit_sdk_park_resume_admitted,
    )

    captured: list[object] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events._emit",
        captured.append,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_park_events.emit_frontier_event",
        captured.append,
    )
    emit_sdk_worker_queued(
        dispatch_id="d1",
        thread_id="t1",
        source_repo="/hub",
        queue_position=0,
        packet_kind="conductor",
        workspace_inherited_from="parent",
    )
    emit_sdk_worker_resumed(
        dispatch_id="d1",
        resume_of="parent",
        sdk_agent_id="agent-1",
        state_root="/tmp/state",
        thread_id="t1",
        execution_id="e1",
        workspace_inherited_from="parent",
    )
    emit_sdk_lane_selected(
        dispatch_id="d1",
        thread_id="t1",
        lane="B",
        reason="explicit",
        regime_active=True,
        contract="conductor",
        selecting_predicate="lane=B",
        workspace_inherited_from="parent",
    )
    emit_sdk_park_resume_admitted(
        parent_dispatch_id="parent",
        child_dispatch_id="d1",
        thread_id="t1",
        intent_id="i",
        code_version="v",
        attempt=1,
        workspace_inherited_from="parent",
    )
    payloads = [ev.payload for ev in captured]
    assert payloads[0]["packet_kind"] == "conductor"
    assert all(p["workspace_inherited_from"] == "parent" for p in payloads)


def test_park_resume_admitted_records_inherited_workspace() -> None:
    from services.git_integration_worker.cursor_sdk_park_events import (
        SdkParkResumeAdmitted,
    )

    hub = SdkParkResumeAdmitted(
        parent_dispatch_id="p",
        child_dispatch_id="c",
        thread_id="t",
        intent_id="i",
        code_version="v",
        attempt=1,
    )
    assert "workspace_inherited_from" not in hub.payload
    sat = SdkParkResumeAdmitted(
        parent_dispatch_id="p",
        child_dispatch_id="c",
        thread_id="t",
        intent_id="i",
        code_version="v",
        attempt=1,
        workspace_inherited_from="p",
    )
    assert sat.payload["workspace_inherited_from"] == "p"
