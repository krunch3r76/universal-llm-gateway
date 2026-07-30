"""Regression tests for enqueue wire-skew tolerance (agent-bus:6333)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.liveness import get_registry
from services.git_integration_worker.cursor_auto.queue import get_queue
from services.git_integration_worker.cursor_auto.wire_skew_events import (
    get_wire_skew_aggregate,
    reset_wire_skew_state_for_tests,
)
from services.git_integration_worker.routes.cursor_auto import EnqueueBody


def test_extra_field_accepted_not_forbidden():
    reset_wire_skew_state_for_tests()
    body = EnqueueBody(
        thread_id="6333",
        turn_number=1,
        subject="wire skew",
        body="",
        from_agent="mcp-server",
        future_sender_field="additive-only",
    )
    dumped = body.model_dump()
    assert "future_sender_field" not in dumped
    assert body.thread_id == "6333"


def test_wire_skew_aggregate_and_latch():
    reset_wire_skew_state_for_tests()
    with patch(
        "services.git_integration_worker.cursor_auto.wire_skew_events.emit_frontier_event"
    ) as emit:
        for _ in range(5):
            EnqueueBody(
                thread_id="1",
                turn_number=1,
                subject="s",
                body="",
                from_agent="mcp-server",
                dropped_key="v1",
            )
        assert emit.call_count == 1
    assert get_wire_skew_aggregate()["mcp→giw/enqueue"] == 5


@pytest.fixture
def cursor_auto_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def test_new_sender_old_receiver_enqueue_200_job_admitted_skew_logged(
    cursor_auto_client: TestClient,
) -> None:
    """6333 regression: extra wire field must not 422; job admitted with skew logged."""
    reset_wire_skew_state_for_tests()
    get_registry().register("6333-test-handler")
    payload = {
        "thread_id": "6333",
        "turn_number": 1,
        "subject": "wire skew regression",
        "body": "TYPE: DIRECTIVE\n",
        "from_agent": "mcp-server",
        "to_agent": "cursor",
        "desired_model": "auto",
        "desired_effort": "medium",
        "contract": "implement",
        "future_sender_field": "additive-only",
    }
    resp = cursor_auto_client.post("/api/v1/git/cursor-auto/enqueue", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["handler_status"] == "auto-admit-armed"
    job_id = body["job_id"]
    job = get_queue().get(job_id)
    assert job is not None
    assert job.thread_id == "6333"
    assert job.status in {"queued", "claimed"}
    assert get_wire_skew_aggregate().get("mcp→giw/enqueue", 0) >= 1
