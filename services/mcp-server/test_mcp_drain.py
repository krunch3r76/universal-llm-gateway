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
async def test_pending_mcp_fence_without_scripts_on_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Container layout: ``scripts`` is not importable, and the fence still answers.

    A pending flag pushed into the process holds an ordinary tools/call with
    the retryable restart error. ``manage`` is not held. An ImportError or
    OSError while reading the flag lets the call through. The body is never
    a 202 result.
    """
    import importlib.abc
    import sys

    from middleware import drain

    class _BlockScripts(importlib.abc.MetaPathFinder):
        def find_spec(
            self, fullname: str, path: Any = None, target: Any = None
        ) -> None:
            if fullname == "scripts" or fullname.startswith("scripts."):
                raise ModuleNotFoundError(fullname)
            return None

    saved = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "scripts" or name.startswith("scripts.")
    }
    sys.meta_path.insert(0, _BlockScripts())
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        drain,
        "record",
        lambda signal, **payload: events.append((signal, payload)),
    )
    drain.reset_drain_for_tests()
    drain.set_pending_mcp_intent(True)

    async def _post(tool_name: str, app: Any) -> list[dict[str, Any]]:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": {}},
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
        return sent

    try:
        app_called = False

        async def app(_scope: Any, _receive: Any, _send: Any) -> None:
            nonlocal app_called
            app_called = True

        sent = await _post("fs", app)
        assert not app_called
        assert drain.in_flight_count() == 0
        assert sent[0]["status"] == 503
        headers = dict(sent[0]["headers"])
        assert headers[b"retry-after"] == b"30"
        payload = json.loads(sent[1]["body"].decode())
        assert "result" not in payload
        assert payload["error"]["code"] == -32099
        assert payload["error"]["data"]["reason"] == "server_restarting"
        assert events[-1][0] == "mcp.maintenance.request.rejected"

        manage_called = False

        async def manage_app(_scope: Any, _receive: Any, _send: Any) -> None:
            nonlocal manage_called
            manage_called = True

        await _post("manage", manage_app)
        assert manage_called

        def _boom() -> bool:
            raise OSError("intent store unreadable")

        monkeypatch.setattr(drain, "_pending_mcp_intent_set", _boom)
        opened = False

        async def open_app(_scope: Any, _receive: Any, _send: Any) -> None:
            nonlocal opened
            opened = True

        await _post("fs", open_app)
        assert opened

        def _missing() -> bool:
            raise ImportError("scripts")

        monkeypatch.setattr(drain, "_pending_mcp_intent_set", _missing)
        opened = False
        await _post("fs", open_app)
        assert opened
    finally:
        sys.meta_path.pop(0)
        sys.modules.update(saved)
        drain.reset_drain_for_tests()


@pytest.mark.asyncio
async def test_manage_result_arms_and_clears_the_admit_fence() -> None:
    """A deferred mcp intent holds the next tools/call; an mcp cancel reopens it.

    Soft-defer (no intent id) and a cancel for another service do not move
    the flag. The process is not draining; the hold comes only from the flag
    the manage result sets.
    """
    from middleware import drain
    from tools.manage import note_mcp_pending_intent

    drain.reset_drain_for_tests()

    async def app(_scope: Any, _receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def post() -> int:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "fs", "arguments": {}},
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
        return int(sent[0]["status"])

    try:
        note_mcp_pending_intent(
            "sync_restart",
            "mcp",
            {"status": "deferred", "state": "busy", "service": "mcp"},
        )
        assert await post() == 200

        note_mcp_pending_intent(
            "sync_restart",
            "mcp",
            {
                "status": "deferred",
                "state": "draining",
                "service": "mcp",
                "restart_intent_id": "intent-mcp-1",
            },
        )
        assert await post() == 503

        note_mcp_pending_intent(
            "cancel_restart_intent",
            "git_integration_worker",
            {
                "status": "cancelled",
                "service": "git_integration_worker",
                "intent_id": "intent-giw",
            },
        )
        assert await post() == 503

        note_mcp_pending_intent(
            "cancel_restart_intent",
            "mcp",
            {"status": "cancelled", "service": "mcp", "intent_id": "intent-mcp-1"},
        )
        assert await post() == 200
    finally:
        drain.reset_drain_for_tests()


@pytest.mark.asyncio
async def test_lifecycle_failure_before_stop_clears_the_admit_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed mcp intent clears the fence without a container stop.

    The import probe can fail before sync-and-restart-mcp.sh stops the
    container. The old process keeps serving, so the flag must drop when
    manage.sock reports that intent failed. The recheck runs off the
    tools/call path.
    """
    from middleware import drain

    from tools import manage as manage_mod

    drain.reset_drain_for_tests()

    async def app(_scope: Any, _receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def post() -> int:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "fs", "arguments": {}},
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
        return int(sent[0]["status"])

    def failed_status(body: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        del body, timeout
        return {
            "result": {
                "restart_intent": {
                    "restart_intent_id": "intent-mcp-fail",
                    "status": "failed",
                }
            }
        }

    try:
        manage_mod.note_mcp_pending_intent(
            "sync_restart",
            "mcp",
            {
                "status": "deferred",
                "state": "draining",
                "service": "mcp",
                "restart_intent_id": "intent-mcp-fail",
            },
        )
        assert await post() == 503
        monkeypatch.setattr(manage_mod, "_call_manage", failed_status)
        manage_mod.recheck_armed_mcp_intent()
        assert drain.pending_mcp_intent_id() is None
        assert await post() == 200
    finally:
        drain.reset_drain_for_tests()


@pytest.mark.asyncio
async def test_refused_cancel_of_terminal_intent_clears_the_admit_fence() -> None:
    """A refused cancel of an already-terminal mcp intent reopens tools/call.

    ``orchestrate_cancel_restart_intent`` returns ``status=refused`` once the
    intent has left ``pending_drain``. That refusal is the signal the intent
    is dead; the fence must not stay up.
    """
    from middleware import drain
    from tools.manage import note_mcp_pending_intent

    drain.reset_drain_for_tests()

    async def app(_scope: Any, _receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def post() -> int:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "fs", "arguments": {}},
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
        return int(sent[0]["status"])

    try:
        note_mcp_pending_intent(
            "sync_restart",
            "mcp",
            {
                "status": "deferred",
                "state": "draining",
                "service": "mcp",
                "restart_intent_id": "intent-mcp-terminal",
            },
        )
        assert await post() == 503
        note_mcp_pending_intent(
            "cancel_restart_intent",
            "mcp",
            {
                "status": "refused",
                "reason": "cancel_refused_after_final_check_or_terminal",
                "intent_id": "intent-mcp-terminal",
                "intent_status": "failed",
            },
            intent_id="intent-mcp-terminal",
        )
        assert drain.pending_mcp_intent_id() is None
        assert await post() == 200
    finally:
        drain.reset_drain_for_tests()


def test_unconfirmed_mcp_intent_hold_fails_open_at_max_age() -> None:
    """Without a live confirm, the fence drops after the max hold age.

    Covers the socket-down case: the off-request recheck cannot prove the
    intent is still pending, and the request path must not 503 forever.
    """
    from middleware import drain

    drain.reset_drain_for_tests()
    try:
        drain.set_pending_mcp_intent(True, intent_id="intent-aged")
        assert drain._pending_mcp_intent_set() is True
        drain._PENDING_ARMED_MONO = (
            drain.time.monotonic() - drain.MCP_INTENT_MAX_HOLD_S - 1
        )
        assert drain._pending_mcp_intent_set() is False
        assert drain.pending_mcp_intent_id() is None
    finally:
        drain.reset_drain_for_tests()
