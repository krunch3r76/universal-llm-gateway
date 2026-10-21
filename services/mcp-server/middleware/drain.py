"""Graceful MCP restart drain middleware.

The drain flag is flipped before uvicorn exits so new tool calls receive a
structured retryable error while in-flight work gets the graceful shutdown
window to finish.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import TYPE_CHECKING, Any

from mcp_events import record

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

RESTART_ERROR_CODE = -32099
RESTART_ERROR_REASON = "server_restarting"
RESTART_ERROR_MESSAGE = "MCP server is restarting; retry in 30s"
RETRY_AFTER_S = 30

_PROBE_PATHS = frozenset({"/health", "/active-work"})

_DRAINING = threading.Event()
_STATE_LOCK = threading.Lock()
_IN_FLIGHT = 0
_DRAIN_STARTED = False
# Life-surface tools/call timestamps — Cowork is "hot" between POSTs, so HTTP
# in-flight alone undercounts. A short TTL bridges the thinking gap without
# parking MCP restarts for an entire CSE lifetime (cdp_ask covers that).
_LIFE_ACTIVITY_TTL_S = float(os.environ.get("MCP_LIFE_ACTIVITY_TTL_S", "180"))
_LAST_LIFE_TOOLS_MONO: float | None = None


def is_draining() -> bool:
    """Return whether the server is rejecting new work for shutdown."""
    return _DRAINING.is_set()


def in_flight_count() -> int:
    """Return the current count of requests already admitted by the middleware."""
    with _STATE_LOCK:
        return _IN_FLIGHT


def note_life_tools_activity() -> None:
    """Record that a life-surface ``tools/call`` was admitted (session still hot)."""
    global _LAST_LIFE_TOOLS_MONO
    with _STATE_LOCK:
        _LAST_LIFE_TOOLS_MONO = time.monotonic()


def active_work_snapshot() -> dict[str, Any]:
    """Restart-drain projection for manage ``busy_status`` / MCP restart gate."""
    with _STATE_LOCK:
        in_flight = _IN_FLIGHT
        last_life = _LAST_LIFE_TOOLS_MONO
        draining = _DRAINING.is_set()
    now = time.monotonic()
    life_idle_s: float | None
    life_hot = False
    if last_life is None:
        life_idle_s = None
    else:
        life_idle_s = max(0.0, now - last_life)
        life_hot = life_idle_s < _LIFE_ACTIVITY_TTL_S
    busy = in_flight > 0 or life_hot
    return {
        "busy": busy,
        "in_flight": in_flight,
        "life_hot": life_hot,
        "life_idle_s": None if life_idle_s is None else round(life_idle_s, 1),
        "life_activity_ttl_s": _LIFE_ACTIVITY_TTL_S,
        "draining": draining,
    }


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
    """Reset module state for isolated ASGI tests.

    The liveness watch is disabled so a test that arms the fence does not
    open a manage.sock poll against the host.
    """
    global _IN_FLIGHT, _DRAIN_STARTED, _LAST_LIFE_TOOLS_MONO, _LIVENESS_WATCH_ENABLED
    with _STATE_LOCK:
        _DRAINING.clear()
        _clear_pending_intent_locked()
        _IN_FLIGHT = 0
        _DRAIN_STARTED = False
        _LAST_LIFE_TOOLS_MONO = None
        _LIVENESS_WATCH_ENABLED = False


# In-process only. The container image has no ``scripts/`` tree, and a store
# read on the request path would 500 every POST when that import fails.
# The id is the restart intent that armed the flag. A live confirm refreshes
# the hold; without one the flag fail-opens at ``MCP_INTENT_MAX_HOLD_S`` so a
# failed intent cannot fence the process after the container was never stopped.
_PENDING_MCP_INTENT = threading.Event()
_PENDING_MCP_INTENT_ID: str | None = None
_PENDING_ARMED_MONO: float | None = None
_PENDING_CONFIRMED_MONO: float | None = None
_LIVENESS_WATCH_ENABLED = True
MCP_INTENT_MAX_HOLD_S = 180.0
_LIVE_RESTART_STATUSES = frozenset({"pending_drain", "drained_restarting"})


def _clear_pending_intent_locked() -> None:
    global _PENDING_MCP_INTENT_ID, _PENDING_ARMED_MONO, _PENDING_CONFIRMED_MONO
    _PENDING_MCP_INTENT.clear()
    _PENDING_MCP_INTENT_ID = None
    _PENDING_ARMED_MONO = None
    _PENDING_CONFIRMED_MONO = None


def _hold_expired_locked(now: float) -> bool:
    """True when the flag has outlived both its arm time and its last confirm.

    A confirm means manage.sock still reports this intent live, so a long
    drain does not fail open. No confirm — socket down, or the watch never
    ran — and the flag drops after ``MCP_INTENT_MAX_HOLD_S``.
    """
    if _PENDING_ARMED_MONO is None:
        return False
    if now - _PENDING_ARMED_MONO <= MCP_INTENT_MAX_HOLD_S:
        return False
    if _PENDING_CONFIRMED_MONO is None:
        return True
    return now - _PENDING_CONFIRMED_MONO > MCP_INTENT_MAX_HOLD_S


def set_pending_mcp_intent(pending: bool, *, intent_id: str | None = None) -> None:
    """Record a pending mcp restart intent pushed into this process.

    The request path does not import the host intent store. Nothing is held
    until a caller in this process sets the flag. ``intent_id`` is stored
    with the flag so a later manage read can clear that intent only.
    Re-arming the same id does not refresh the hold clock. ``False`` reopens
    admits and drops the id.
    """
    global _PENDING_MCP_INTENT_ID, _PENDING_ARMED_MONO, _PENDING_CONFIRMED_MONO
    with _STATE_LOCK:
        if not pending:
            _clear_pending_intent_locked()
            return
        _PENDING_MCP_INTENT.set()
        if intent_id and intent_id != _PENDING_MCP_INTENT_ID:
            _PENDING_MCP_INTENT_ID = intent_id
            _PENDING_ARMED_MONO = time.monotonic()
            _PENDING_CONFIRMED_MONO = None
        elif _PENDING_ARMED_MONO is None:
            _PENDING_ARMED_MONO = time.monotonic()


def confirm_pending_mcp_intent(intent_id: str) -> None:
    """Record that manage.sock still reports ``intent_id`` as live.

    A confirm for a different id is ignored so a stale read cannot keep a
    newer fence past the hold age.
    """
    global _PENDING_CONFIRMED_MONO
    with _STATE_LOCK:
        if not _PENDING_MCP_INTENT.is_set() or _PENDING_MCP_INTENT_ID != intent_id:
            return
        _PENDING_CONFIRMED_MONO = time.monotonic()


def pending_mcp_intent_id() -> str | None:
    """Return the armed intent id, or None when the fence is down."""
    with _STATE_LOCK:
        if not _PENDING_MCP_INTENT.is_set():
            return None
        return _PENDING_MCP_INTENT_ID


def liveness_watch_enabled() -> bool:
    """Return whether an off-request watch may poll manage.sock.

    Tests clear this in ``reset_drain_for_tests``. The serving process leaves
    it on so a failed intent is cleared without a later manage tools/call.
    """
    return _LIVENESS_WATCH_ENABLED


def intent_status_still_pending(status: str | None) -> bool:
    """Return whether ``status`` is a live restart-intent status.

    ``None`` and every terminal or unknown status are not pending. The set
    is inlined because this process does not import the host intent store.
    """
    return status in _LIVE_RESTART_STATUSES


def _pending_mcp_intent_set() -> bool:
    """Return the in-process flag. An expired unconfirmed hold fail-opens.

    Callers treat any error as not pending.
    """
    with _STATE_LOCK:
        if not _PENDING_MCP_INTENT.is_set():
            return False
        if _hold_expired_locked(time.monotonic()):
            _clear_pending_intent_locked()
            return False
        return True


def _tools_call_held_for_pending_intent(tool_name: str) -> bool:
    """Hold a tools/call while a pending mcp intent was pushed in.

    ``manage`` stays open so force and cancel can still reach the server.
    Import and I/O failures fail open: a broken flag must not 500 the POST.
    """
    if tool_name == "manage":
        return False
    try:
        return _pending_mcp_intent_set()
    except Exception:
        return False


def _replay_receive(body: bytes, receive: Receive) -> Receive:
    """Return a receive callable that yields ``body`` once, then the original."""
    pending = {"type": "http.request", "body": body, "more_body": False}
    sent = False

    async def inner() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return pending
        return await receive()

    return inner


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

        # Pending mcp intent is an in-process flag, not a scripts import.
        # Held tools/call get the same retryable 503 as a live drain, and do
        # not count as in-flight. manage is exempt. A flag error fails open.
        try:
            pending_mcp_intent = _pending_mcp_intent_set()
        except Exception:
            pending_mcp_intent = False
        if method == "POST" and not _DRAINING.is_set() and pending_mcp_intent:
            from dual_endpoint_http import is_mcp_endpoint_path  # noqa: PLC0415

            if is_mcp_endpoint_path(path):
                body = await _read_body(receive)
                jsonrpc_id, mcp_method, tool_name = _parse_jsonrpc_request(body)
                if mcp_method == "tools/call" and _tools_call_held_for_pending_intent(
                    tool_name
                ):
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
                receive = _replay_receive(body, receive)

        # Atomic check-and-admit: under the lock either reject (drain mode) or
        # increment the in-flight counter. Prevents the TOCTOU window where a
        # request slips past the drain check and then increments _IN_FLIGHT.
        with _STATE_LOCK:
            draining = _DRAINING.is_set()
            if not draining:
                global _IN_FLIGHT
                _IN_FLIGHT += 1

        if draining:
            if path in _PROBE_PATHS:
                if path == "/active-work":
                    payload = active_work_snapshot()
                    payload["status"] = "draining"
                    await _send_json(send, status=200, payload=payload)
                    return
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

            # Non-MCP-endpoint, non-probe request during drain — close politely.
            await _send_json(
                send,
                status=503,
                payload=restart_error_payload(None),
                retry_after=True,
            )
            return

        # Probe endpoints must not inflate the in-flight counter that gates stop.
        if path in _PROBE_PATHS and method == "GET":
            with _STATE_LOCK:
                _IN_FLIGHT -= 1
            if path == "/active-work":
                await _send_json(send, status=200, payload=active_work_snapshot())
                return
            # /health continues into AuthMiddleware's short-circuit payload.
            await self._app(scope, receive, send)
            return

        try:
            await self._app(scope, receive, send)
        finally:
            with _STATE_LOCK:
                _IN_FLIGHT -= 1
