"""MCP request lifecycle middleware — emits ``mcp.request.*`` signals for ``/mcp``.

Sits inside the auth middleware so that rejected OAuth tokens terminate before
``mcp.request.started`` fires.  Includes ``auth_mode`` from the ASGI scope
(set by ``AuthMiddleware``) in every event payload for admission-path
observability.

Tool-call extraction: for ``tools/call`` MCP methods, the middleware parses
``params.name`` from the JSON-RPC body to include the specific tool name in
both log lines and event payloads, enabling per-tool observability.

``seat_class`` field: derived from the request path and included in every event
payload and in the request-scoped metadata (via ``bind_request``).  Allows
per-endpoint event queries, e.g.
``scripts/query-events --op mcp.tool.call.error --filter seat_class=claude``.
  /mcp       → ``seat_class: "claude"``  (Cursor / Anthropic API surface)
  other      → ``seat_class: "unknown"``
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from dual_endpoint_http import is_mcp_endpoint_path
from endpoint_surface import surface_from_path
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE
from mcp_events import monotonic_now, record
from mcp_tools_call_log import ToolsCallIsErrorScan
from request_profile import bind_request
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from tool_access import (
    oauth_client_denial_reason,
    oauth_client_tool_allowed,
    restricted_oauth_client_unknown_call_denied,
)
from universal_logging import get_logger

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = get_logger(__name__)
_SUSPECTED_TIMEOUT_MIN_DURATION_S = 25.0
_SUSPECTED_TIMEOUT_MAX_RESPONSE_BYTES = 100

# Paths handled by this middleware; everything else is passed through unchanged.
_MCP_SEAT_BY_PATH = {
    "/mcp/life": "claude",
    "/mcp/code": "cursor",
    "/mcp": "claude",  # legacy bare path (absent after dual-endpoint cutover)
}


def _seat_class_from_path(path: str) -> str:
    """Return the seat_class tag for the given request path.

    /mcp/life → "claude" (claude.ai toys / life surface)
    /mcp/code → "cursor" (Cursor IDE / code surface)
    other     → "unknown"
    """
    if not path:
        return "unknown"
    normalized = path.rstrip("/") or "/"
    exact = _MCP_SEAT_BY_PATH.get(normalized)
    if exact is not None:
        return exact
    from endpoint_surface import surface_from_path

    surface = surface_from_path(normalized)
    if surface == "life":
        return "claude"
    if surface == "code":
        return "cursor"
    return "unknown"


def _extract_jsonrpc_id(body: bytes) -> Any:
    """Return JSON-RPC ``id`` field when present (may be str, int, or null)."""
    if not body:
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
        if isinstance(parsed, dict) and "id" in parsed:
            return parsed.get("id")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def _extract_call_metadata(body: bytes) -> tuple[str, str, dict[str, Any]]:
    """Extract MCP method, tool name, and tool arguments from a JSON-RPC body.

    Returns:
        (mcp_method, tool_name, tool_args) — empty strings/dict when absent.
    """
    mcp_method = ""
    tool_name = ""
    tool_args: dict[str, Any] = {}
    if not body:
        return mcp_method, tool_name, tool_args
    try:
        parsed = json.loads(body.decode("utf-8"))
        mcp_method = parsed.get("method", "") or ""
        if mcp_method == "tools/call":
            params = parsed.get("params", {})
            tool_name = params.get("name", "") or ""
            raw_args = params.get("arguments", {})
            if isinstance(raw_args, dict):
                tool_args = raw_args
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return mcp_method, tool_name, tool_args


def _summarize_tool_args(tool_name: str, args: dict[str, Any]) -> str:
    """Generates a concise, one-line summary of tool arguments for log readability.

    Examples:
        - dispatch tool with inner tool 'foo': '→foo'
        - cortex tool with inner tool 'entity_get': '→entity_get'
        - files tool with op 'read' and path '/a/b': 'op=read path=/a/b'
    """
    if not args:
        return ""
    if tool_name == "dispatch":
        inner = args.get("tool", "")
        return f"→{inner}" if inner else ""
    if tool_name in ("cortex", "agent_bus"):
        inner = args.get("tool", "")
        return f"→{inner}" if inner else ""
    if tool_name in ("files", "project", "workspaces"):
        op = args.get("op", "")
        path = args.get("path", "")
        return f"op={op} path={path}" if op else ""
    if tool_name == "cortex_brief":
        seat = args.get("seat") or "?"
        return f"seat={seat}"
    if tool_name == "web_search":
        q = str(args.get("query", ""))[:60]
        return f"q={q}" if q else ""
    return ""


def _extract_request_tool_context(
    tool_name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Return request metadata that helps correlate dispatch-style tool calls."""
    context: dict[str, Any] = {}
    if tool_name not in {"cortex", "agent_bus"}:
        return context

    selector = str(args.get("tool", "") or "").strip()
    if selector:
        context[f"{tool_name}_tool"] = selector

    nested_raw = args.get("arguments")
    if not isinstance(nested_raw, str) or not nested_raw.strip():
        return context

    try:
        nested = json.loads(nested_raw)
    except json.JSONDecodeError:
        return context
    if not isinstance(nested, dict):
        return context

    interesting_keys = {
        "cortex": ("entity_id", "type", "limit", "review_status", "assertion_id"),
        "agent_bus": ("thread", "turn_number", "last", "status", "tags"),
    }
    for key in interesting_keys[tool_name]:
        value = nested.get(key)
        if value is not None and value != "":
            context[f"{tool_name}_{key}"] = value
    return context


async def _buffer_mcp_post_body(
    receive: Receive,
    *,
    max_body_size: int,
) -> tuple[bytes | None, list[Message]]:
    """Read the full POST body up to ``max_body_size`` (413 ⇒ ``body is None``)."""
    messages: list[Message] = []
    body_parts: list[bytes] = []
    more_body = True
    while more_body:
        message = await receive()
        if message.get("type") != "http.request":
            messages.append(message)
            break
        chunk = message.get("body", b"") or b""
        current_len = sum(len(part) for part in body_parts)
        if current_len + len(chunk) > max_body_size:
            return None, messages
        body_parts.append(chunk)
        messages.append(message)
        more_body = bool(message.get("more_body", False))
    return b"".join(body_parts), messages


def _is_suspected_fs_timeout(
    tool_name: str,
    duration_s: float,
    response_bytes: int,
) -> bool:
    """Return True for provider-side MCP cutoffs that otherwise look successful."""
    return (
        tool_name == "fs"
        and duration_s >= _SUSPECTED_TIMEOUT_MIN_DURATION_S
        and 0 < response_bytes <= _SUSPECTED_TIMEOUT_MAX_RESPONSE_BYTES
    )


class McpRequestEventsMiddleware:
    """Emit ``mcp.request.*`` signals for authenticated ``/mcp`` traffic."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        if not is_mcp_endpoint_path(path):
            await self._app(scope, receive, send)
            return

        seat_class = _seat_class_from_path(path)
        surface = str(scope.get("mcp_surface") or surface_from_path(path) or "")

        client_ip = request.client.host if request.client else "unknown"
        method = request.method
        auth_mode = str(scope.get("auth_mode", "unknown"))
        profile = str(scope.get("mcp_profile", "default"))
        caller_identity = str(scope.get("mcp_caller_identity", ""))
        oauth_client_id = str(scope.get("oauth_client_id", ""))
        # Mcp-Session-Id: client-side session token (stable across tool calls
        # within one session even in stateless_http=True mode).
        mcp_session_id = (request.headers.get("mcp-session-id", "") or "").strip()
        is_error_scan = ToolsCallIsErrorScan(
            path=path,
            protocol=(request.headers.get("mcp-protocol-version") or "").strip(),
            user_agent=(request.headers.get("user-agent") or "").strip(),
            caller_id=caller_identity or oauth_client_id,
        )
        structured_capable = (
            request.headers.get("x-mcp-structured-capable", "") or ""
        ).strip().lower() in {"1", "true", "yes"}
        t0 = monotonic_now()
        mcp_method = ""
        tool_name = ""
        tool_args: dict[str, Any] = {}
        request_tool_context: dict[str, Any] = {}
        jsonrpc_id: Any = None
        correlation_hdr = (
            request.headers.get("x-cloudproxy-correlation-id", "") or ""
        ).strip()

        if method == "POST":
            max_body_size = DEFAULT_MAX_REQUEST_BODY_SIZE
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    pass
                else:
                    if declared_size > max_body_size:
                        response = Response("Request body too large", status_code=413)
                        await response(scope, receive, send)
                        return

            body, buffered_messages = await _buffer_mcp_post_body(
                receive, max_body_size=max_body_size
            )
            if body is None:
                response = Response("Request body too large", status_code=413)
                await response(scope, receive, send)
                return

            jsonrpc_id = _extract_jsonrpc_id(body)
            mcp_method, tool_name, tool_args = _extract_call_metadata(body)
            if mcp_method == "tools/call":
                is_error_scan.arm(tool_name)
            request_tool_context = _extract_request_tool_context(tool_name, tool_args)
            if surface == "life" and mcp_method == "tools/call":
                from middleware.drain import note_life_tools_activity  # noqa: PLC0415

                note_life_tools_activity()
            orig_receive = receive
            replay_index = 0

            async def tee_receive() -> Message:
                nonlocal replay_index
                if replay_index < len(buffered_messages):
                    message = buffered_messages[replay_index]
                    replay_index += 1
                    return message
                return await orig_receive()

            receive = tee_receive

        started_payload: dict[str, Any] = {
            "method": method,
            "client_ip": client_ip,
            "mcp_method": mcp_method,
            "auth_mode": auth_mode,
            "seat_class": seat_class,
            **({"surface": surface} if surface else {}),
            **({"tool_name": tool_name} if tool_name else {}),
            **({"caller_identity": caller_identity} if caller_identity else {}),
        }

        record("mcp.request.started", role="coordination", **started_payload)

        transport_started: dict[str, Any] = {
            "transport": "https",
            "method": method,
            "client_ip": client_ip,
            "mcp_method": mcp_method,
            "auth_mode": auth_mode,
            "seat_class": seat_class,
            **({"surface": surface} if surface else {}),
        }
        if correlation_hdr:
            transport_started["cloudproxy_correlation_id"] = correlation_hdr
        if jsonrpc_id is not None:
            transport_started["jsonrpc_id"] = jsonrpc_id
        if tool_name:
            transport_started["tool_name"] = tool_name
        record("mcp.transport.request.started", **transport_started)

        if tool_name:
            summary = _summarize_tool_args(tool_name, tool_args)
            log_detail = f" {summary}" if summary else ""
            logger.info("MCP tool call: %s%s", tool_name, log_detail)

        if restricted_oauth_client_unknown_call_denied(
            oauth_client_id or None, mcp_method, tool_name
        ):
            reason = oauth_client_denial_reason(
                oauth_client_id,
                tool_name or mcp_method or "unknown",
            )
            record(
                "mcp.oauth.client.tool.denied",
                role="coordination",
                oauth_client_id=oauth_client_id,
                tool_name=tool_name or "",
                reason=reason,
                seat_class=seat_class,
                client_ip=client_ip,
            )
            logger.warning(
                "MCP oauth client call denied (unknown): client=%s method=%s tool=%s",
                oauth_client_id,
                mcp_method,
                tool_name,
            )
            deny_unknown: dict[str, Any] = {
                "jsonrpc": "2.0",
                "error": {"code": -32001, "message": reason},
            }
            if jsonrpc_id is not None:
                deny_unknown["id"] = jsonrpc_id
            response = JSONResponse(deny_unknown, status_code=200)
            await response(scope, receive, send)
            return

        # Server-side OAuth-client allowlist (grok-connector read-only).
        # Must run before FastMCP handles tools/call — client-side
        # allowed_tools is advisory only.
        if (
            mcp_method == "tools/call"
            and tool_name
            and not oauth_client_tool_allowed(
                oauth_client_id or None, tool_name, tool_args
            )
        ):
            reason = oauth_client_denial_reason(oauth_client_id, tool_name)
            record(
                "mcp.oauth.client.tool.denied",
                role="coordination",
                oauth_client_id=oauth_client_id,
                tool_name=tool_name,
                reason=reason,
                seat_class=seat_class,
                client_ip=client_ip,
            )
            logger.warning(
                "MCP oauth client tool denied: client=%s tool=%s",
                oauth_client_id,
                tool_name,
            )
            deny_body: dict[str, Any] = {
                "jsonrpc": "2.0",
                "error": {"code": -32001, "message": reason},
            }
            if jsonrpc_id is not None:
                deny_body["id"] = jsonrpc_id
            response: Response = JSONResponse(deny_body, status_code=200)
            await response(scope, receive, send)
            return

        response_bytes = 0
        stream_opened = False

        async def measuring_send(message: Message) -> None:
            nonlocal response_bytes, stream_opened
            if message.get("type") == "http.response.start":
                is_error_scan.note_status(int(message.get("status") or 0))
                is_error_scan.note_start(message.get("headers") or [])
            if message.get("type") == "http.response.body":
                chunk = message.get("body", b"") or b""
                response_bytes += len(chunk)
                is_error_scan.feed(chunk)
                if not stream_opened and len(chunk) > 0:
                    stream_opened = True
                    opened_payload: dict[str, Any] = {
                        "transport": "https",
                        "client_ip": client_ip,
                        "mcp_method": mcp_method,
                        "auth_mode": auth_mode,
                        "seat_class": seat_class,
                    }
                    if correlation_hdr:
                        opened_payload["cloudproxy_correlation_id"] = correlation_hdr
                    if jsonrpc_id is not None:
                        opened_payload["jsonrpc_id"] = jsonrpc_id
                    if tool_name:
                        opened_payload["tool_name"] = tool_name
                    record("mcp.transport.stream.opened", **opened_payload)
            await send(message)

        bind_kwargs: dict[str, Any] = {
            "request_profile": profile,
            "client_ip": client_ip,
            "auth_mode": auth_mode,
            "mcp_method": mcp_method,
            "tool_name": tool_name or None,
            "seat_class": seat_class,
            "surface": surface or None,
            "jsonrpc_id": jsonrpc_id,
            "cloudproxy_correlation_id": correlation_hdr or None,
            "caller_identity": caller_identity or None,
            "oauth_client_id": oauth_client_id or None,
            "mcp_session_id": mcp_session_id or None,
            **request_tool_context,
        }
        ua_header = (request.headers.get("user-agent") or "").strip()
        if ua_header:
            bind_kwargs["user_agent"] = ua_header
        with bind_request(
            profile,
            structured_capable=structured_capable,
            **bind_kwargs,
        ):
            try:
                await self._app(scope, receive, measuring_send)
            except Exception as exc:
                duration = monotonic_now() - t0
                failed_payload: dict[str, Any] = {
                    "method": method,
                    "client_ip": client_ip,
                    "duration_s": round(duration, 3),
                    "error": str(exc),
                    "exc_type": type(exc).__name__,
                    "auth_mode": auth_mode,
                    "mcp_method": mcp_method,
                    "response_bytes": response_bytes,
                    "seat_class": seat_class,
                    **({"surface": surface} if surface else {}),
                }
                if tool_name:
                    failed_payload["tool_name"] = tool_name
                record("mcp.request.failed", role="coordination", **failed_payload)
                tfail = {
                    "transport": "https",
                    "client_ip": client_ip,
                    "duration_s": round(duration, 3),
                    "error": str(exc),
                    "exc_type": type(exc).__name__,
                    "auth_mode": auth_mode,
                    "mcp_method": mcp_method,
                    "response_bytes": response_bytes,
                    "seat_class": seat_class,
                }
                if correlation_hdr:
                    tfail["cloudproxy_correlation_id"] = correlation_hdr
                if jsonrpc_id is not None:
                    tfail["jsonrpc_id"] = jsonrpc_id
                if tool_name:
                    tfail["tool_name"] = tool_name
                record("mcp.transport.request.failed", **tfail)
                if tool_name:
                    logger.warning(
                        "MCP tool FAILED: %s (%.1fs, %dB) %s",
                        tool_name,
                        duration,
                        response_bytes,
                        type(exc).__name__,
                    )
                raise
            else:
                duration = monotonic_now() - t0
                completed_payload: dict[str, Any] = {
                    "method": method,
                    "client_ip": client_ip,
                    "duration_s": round(duration, 3),
                    "auth_mode": auth_mode,
                    "response_bytes": response_bytes,
                    "seat_class": seat_class,
                    **({"surface": surface} if surface else {}),
                    **({"tool_name": tool_name} if tool_name else {}),
                    **({"caller_identity": caller_identity} if caller_identity else {}),
                }
                record(
                    "mcp.request.completed", role="coordination", **completed_payload
                )
                tdone: dict[str, Any] = {
                    "transport": "https",
                    "client_ip": client_ip,
                    "duration_s": round(duration, 3),
                    "auth_mode": auth_mode,
                    "response_bytes": response_bytes,
                    "mcp_method": mcp_method,
                    "seat_class": seat_class,
                }
                if correlation_hdr:
                    tdone["cloudproxy_correlation_id"] = correlation_hdr
                if jsonrpc_id is not None:
                    tdone["jsonrpc_id"] = jsonrpc_id
                if tool_name:
                    tdone["tool_name"] = tool_name
                record("mcp.transport.request.completed", **tdone)
                if _is_suspected_fs_timeout(tool_name, duration, response_bytes):
                    record(
                        "fs.timeout.suspected",
                        role="coordination",
                        tool_name=tool_name,
                        duration_s=round(duration, 3),
                        response_bytes=response_bytes,
                        client_ip=client_ip,
                        auth_mode=auth_mode,
                        mcp_method=mcp_method,
                    )
                if tool_name:
                    logger.info(
                        "MCP tool done: %s (%.1fs, %dB)",
                        tool_name,
                        duration,
                        response_bytes,
                    )
                is_error_scan.emit(logger)
