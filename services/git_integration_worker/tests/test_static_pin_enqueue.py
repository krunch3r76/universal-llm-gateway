"""Fail-fast static pin refusal at enqueue (arc 6899 half 1)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.liveness import get_registry
from services.git_integration_worker.cursor_auto.queue import (
    AutoJob,
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_auto.static_pin_refusal import (
    assess_static_pin_refusal,
)


@pytest.fixture
def cursor_auto_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    reset_queue_for_tests(durable=False)
    return TestClient(create_app())


def test_assess_static_pin_refusal_model_payload_matches_handler_shape() -> None:
    refusal = assess_static_pin_refusal(
        desired_model="cursor/claude-sonnet-4",
        desired_effort="medium",
        escalation=None,
        contract="implement",
        body="TYPE: DIRECTIVE\nvision: test\n",
    )
    assert refusal is not None
    assert refusal.reason == "model_pin_refused"
    assert refusal.payload["reason"] == "model_pin_refused"
    assert refusal.payload["summary"] == refusal.summary
    assert "bindable" in refusal.payload
    assert refusal.payload["requested_model"] == "cursor/claude-sonnet-4"


def test_process_job_and_enqueue_assess_same_model_payload() -> None:
    kwargs = dict(
        desired_model="cursor/claude-sonnet-4",
        desired_effort="medium",
        escalation=None,
        contract="implement",
        body="TYPE: DIRECTIVE\ndensity: dense\nvision: test\n",
    )
    enqueue_refusal = assess_static_pin_refusal(**kwargs)
    assert enqueue_refusal is not None

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    job = AutoJob(
        job_id="j-dispatch-path",
        thread_id="6899",
        turn_number=1,
        subject="bad model",
        from_agent="web-anthropic",
        to_agent="cursor",
        **kwargs,
    )
    dispatch_result = asyncio.run(process_job(job, bus=bus))
    assert dispatch_result["terminal_status"] == "status:blocked"
    dispatch_payload = json.loads(bus.reply.await_args.kwargs["body"])
    assert dispatch_payload == enqueue_refusal.payload


def test_enqueue_static_pin_refused_does_not_persist_job(
    cursor_auto_client: TestClient,
) -> None:
    get_registry().register("6899-test-handler")
    queue = get_queue()
    before_total = queue.snapshot()["total"]

    with patch(
        "services.git_integration_worker.routes.cursor_auto.CursorBusClient"
    ) as client_cls:
        bus = AsyncMock()
        bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
        client_cls.return_value = bus

        resp = cursor_auto_client.post(
            "/api/v1/git/cursor-auto/enqueue",
            json={
                "thread_id": "6899",
                "turn_number": 1,
                "subject": "bad model pin",
                "body": "TYPE: DIRECTIVE\nvision: test\n",
                "from_agent": "web-anthropic",
                "to_agent": "cursor",
                "desired_model": "cursor/claude-sonnet-4",
                "desired_effort": "medium",
                "contract": "implement",
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["handler_status"] == "static-pin-refused"
    assert body["static_refusal"] is True
    assert body["reason"] == "model_pin_refused"
    assert queue.snapshot()["total"] == before_total
    assert queue.snapshot()["pending"] == 0
    posted = json.loads(bus.reply.await_args.kwargs["body"])
    assert posted["reason"] == "model_pin_refused"


def test_enqueue_continuity_hop_skips_static_pin_check(
    cursor_auto_client: TestClient,
) -> None:
    """Free hop carve-out: hops bypass model pin refusal (handler parity)."""
    get_registry().register("6899-hop-handler")
    queue = get_queue()
    before_total = queue.snapshot()["total"]

    with patch(
        "services.git_integration_worker.routes.cursor_auto.run_continuity_hop_concurrent",
        new_callable=AsyncMock,
        return_value={"ok": True},
    ):
        resp = cursor_auto_client.post(
            "/api/v1/git/cursor-auto/enqueue",
            json={
                "thread_id": "6899",
                "turn_number": 1,
                "subject": "hop cadence",
                "body": "TYPE: CONTINUITY_HANDOFF\n",
                "from_agent": "web-anthropic",
                "to_agent": "cursor",
                "desired_model": "cdp/opus-5",
                "desired_effort": "high",
                "contract": "light-bounded",
                "continuity_hop": True,
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["handler_status"] == "auto-admit-armed"
    assert body.get("static_refusal") is not True
    assert queue.snapshot()["total"] == before_total + 1
