"""Regression tests for MCP graceful drain behavior."""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_drain_rejects_new_tool_calls_with_restart_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from middleware import drain

    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        drain,
        "record",
        lambda signal, **payload: events.append((signal, payload)),
    )
    drain.reset_drain_for_tests()
    drain.begin_drain(reason="test", timeout_s=25)

    app_called = False

    async def app(_scope: Any, _receive: Any, _send: Any) -> None:
        nonlocal app_called
        app_called = True

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 17,
            "method": "tools/call",
            "params": {"name": "fs", "arguments": {"op": "list"}},
        }
    ).encode()
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await drain.DrainMiddleware(app)(
        {"type": "http", "path": "/mcp/code", "method": "POST"},
        receive,
        send,
    )

    assert not app_called
    assert sent[0]["status"] == 503
    headers = dict(sent[0]["headers"])
    assert headers[b"retry-after"] == b"30"
    payload = json.loads(sent[1]["body"].decode())
    assert payload["id"] == 17
    assert payload["error"]["code"] == -32099
    assert payload["error"]["data"]["reason"] == "server_restarting"
    assert events[-1][0] == "mcp.maintenance.request.rejected"

    drain.reset_drain_for_tests()


@pytest.mark.asyncio
async def test_pending_mcp_intent_queues_next_tools_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pending mcp intent: tools/call queues and does not count as in-flight.

    ``force_requested`` is not pending, so the same call starts.
    """
    from middleware import drain

    from scripts.model_manager.ui.controller.restart_intent_states import (
        STATUS_FORCE_REQUESTED,
        STATUS_PENDING_DRAIN,
    )

    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        drain,
        "record",
        lambda signal, **payload: events.append((signal, payload)),
    )
    drain.reset_drain_for_tests()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.intent_admit_fence.pending_service_intent_status",
        lambda service: STATUS_PENDING_DRAIN if service == "mcp" else None,
    )

    app_called = False

    async def app(_scope: Any, _receive: Any, _send: Any) -> None:
        nonlocal app_called
        app_called = True

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "fs", "arguments": {"op": "list"}},
        }
    ).encode()
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await drain.DrainMiddleware(app)(
        {"type": "http", "path": "/mcp/code", "method": "POST"},
        receive,
        send,
    )

    assert not app_called
    assert drain.in_flight_count() == 0
    assert sent[0]["status"] == 202
    payload = json.loads(sent[1]["body"].decode())
    assert payload["result"]["status"] == "queued"
    assert payload["result"]["lane"] == "mcp"
    assert events[-1][0] == "mcp.maintenance.request.queued"

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.intent_admit_fence.pending_service_intent_status",
        lambda _service: STATUS_FORCE_REQUESTED,
    )
    started = {"n": 0}

    async def app_start(_scope: Any, _receive: Any, _send: Any) -> None:
        started["n"] += 1

    messages_force = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive_force() -> dict[str, Any]:
        return messages_force.pop(0)

    await drain.DrainMiddleware(app_start)(
        {"type": "http", "path": "/mcp/code", "method": "POST"},
        receive_force,
        send,
    )
    assert started["n"] == 1
    drain.reset_drain_for_tests()
