"""Graceful MCP restart drain middleware.

The drain flag is flipped before uvicorn exits so new tool calls receive a
structured retryable error while in-flight work gets the graceful shutdown
window to finish.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

from mcp_events import record

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

RESTART_ERROR_CODE = -32099
RESTART_ERROR_REASON = "server_restarting"
RESTART_ERROR_MESSAGE = "MCP server is restarting; retry in 30s"
RETRY_AFTER_S = 30

_DRAINING = threading.Event()
_STATE_LOCK = threading.Lock()
_IN_FLIGHT = 0
_DRAIN_STARTED = False


def is_draining() -> bool:
    """Return whether the server is rejecting new work for shutdown."""
    return _DRAINING.is_set()


def in_flight_count() -> int:
    """Return the current count of requests already admitted by the middleware."""
    with _STATE_LOCK:
        return _IN_FLIGHT


def begin_drain(*, reason: str, timeout_s: float) -> None:
    """Enter drain mode and emit a single start event."""
    global _DRAIN_STARTED
    with _STATE_LOCK:
        _DRAINING.set()
        if _DRAIN_STARTED:
            return
        _DRAIN_STARTED = True
        in_flight = _IN_FLIGHT
    record(
        "mcp.maintenance.drain.started",
        reason=reason,
        timeout_s=timeout_s,
        in_flight=in_flight,
    )


def complete_drain(*, timed_out: bool = False) -> None:
    """Emit a drain completion event after uvicorn exits."""
    if not _DRAIN_STARTED:
        return
    record(
        "mcp.maintenance.drain.completed",
        timed_out=timed_out,
        in_flight_at_timeout=in_flight_count(),
    )


def reset_drain_for_tests() -> None:
    """Reset module state for isolated ASGI tests."""
    global _IN_FLIGHT, _DRAIN_STARTED
    with _STATE_LOCK:
        _DRAINING.clear()
        _IN_FLIGHT = 0
        _DRAIN_STARTED = False


def restart_error_payload(jsonrpc_id: Any) -> dict[str, Any]:
    """Return the canonical JSON-RPC restart error payload."""
    return {
        "jsonrpc": "2.0",
        "id": jsonrpc_id,
        "error": {
            "code": RESTART_ERROR_CODE,
            "message": RESTART_ERROR_MESSAGE,
            "data": {
                "reason": RESTART_ERROR_REASON,
                "retry_after_s": RETRY_AFTER_S,
            },
        },
    }


def is_restart_error_payload(payload: Any) -> bool:
    """Return True when *payload* carries the canonical restart error shape."""
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    data = error.get("data")
    return (
        error.get("code") == RESTART_ERROR_CODE
        and isinstance(data, dict)
        and data.get("reason") == RESTART_ERROR_REASON
    )


def _parse_jsonrpc_request(body: bytes) -> tuple[Any, str, str]:
    if not body:
        return None, "", ""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "", ""
    if not isinstance(parsed, dict):
        return None, "", ""
    method = str(parsed.get("method") or "")
    tool_name = ""
    params = parsed.get("params")
    if method == "tools/call" and isinstance(params, dict):
        tool_name = str(params.get("name") or "")
    return parsed.get("id"), method, tool_name


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    more_body = True
    while more_body:
        message = await receive()
        if message.get("type") != "http.request":
            break
        chunks.append(message.get("body", b"") or b"")
        more_body = bool(message.get("more_body", False))
    return b"".join(chunks)


async def _send_json(
    send: Send,
    *,
    status: int,
    payload: dict[str, Any],
    retry_after: bool = False,
) -> None:
    headers = [(b"content-type", b"application/json")]
    if retry_after:
        headers.extend(
            [
                (b"retry-after", str(RETRY_AFTER_S).encode("ascii")),
                (b"connection", b"close"),
            ]
        )
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class DrainMiddleware:
    """Reject new MCP tool calls with a retryable restart error during shutdown."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        method = str(scope.get("method", ""))

        # Atomic check-and-admit: under the lock either reject (drain mode) or
        # increment the in-flight counter. Prevents the TOCTOU window where a
        # request slips past the drain check and then increments _IN_FLIGHT.
        with _STATE_LOCK:
            draining = _DRAINING.is_set()
            if not draining:
                global _IN_FLIGHT
                _IN_FLIGHT += 1

        if draining:
            if path == "/health":
                await _send_json(send, status=200, payload={"status": "draining"})
                return

            from dual_endpoint_http import is_mcp_endpoint_path  # noqa: PLC0415

            if is_mcp_endpoint_path(path) and method == "POST":
                body = await _read_body(receive)
                jsonrpc_id, mcp_method, tool_name = _parse_jsonrpc_request(body)
                record(
                    "mcp.maintenance.request.rejected",
                    mcp_method=mcp_method,
                    tool_name=tool_name,
                    jsonrpc_id=jsonrpc_id,
                    retry_after_s=RETRY_AFTER_S,
                )
                await _send_json(
                    send,
                    status=503,
                    payload=restart_error_payload(jsonrpc_id),
                    retry_after=True,
                )
                return

            # Non-MCP-endpoint, non-/health request during drain — close politely.
            await _send_json(
                send,
                status=503,
                payload=restart_error_payload(None),
                retry_after=True,
            )
            return

        try:
            await self._app(scope, receive, send)
        finally:
            with _STATE_LOCK:
                _IN_FLIGHT -= 1
