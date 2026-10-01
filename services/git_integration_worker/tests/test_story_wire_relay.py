"""Tests for frontier.sdk.closeout.relayed and relay-path failure isolation."""

from __future__ import annotations


def test_completed_event_carries_optional_association_fields() -> None:
    from services.git_integration_worker.cursor_sdk_events import (
        FrontierSdkWorkerCompleted,
    )

    bare = FrontierSdkWorkerCompleted(
        dispatch_id="d1",
        thread_id="t1",
        execution_id="e1",
        duration_s=1.0,
        tool_call_count=0,
        result_bytes=0,
        outcome="ok",
        resolved_model="cursor/composer-2.5",
    )
    assert "asked_by" not in bare.payload

    stamped = FrontierSdkWorkerCompleted(
        dispatch_id="req1-abcd1234",
        thread_id="t1",
        execution_id="e1",
        duration_s=1.0,
        tool_call_count=0,
        result_bytes=0,
        outcome="ok",
        resolved_model="cursor/composer-2.5",
        asked_by="cursor",
        purpose="(unstated)",
        story_id="req1",
    )
    assert stamped.payload["story_id"] == "req1"
    assert stamped.payload["asked_by"] == "cursor"
    assert stamped.payload["purpose"] == "(unstated)"
