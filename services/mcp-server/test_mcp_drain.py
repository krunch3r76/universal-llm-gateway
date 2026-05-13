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
        {"type": "http", "path": "/mcp", "method": "POST"},
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
