"""MCP request lifecycle middleware — emits ``mcp.request.*`` signals for ``/mcp``.

Sits inside the auth middleware so that rejected OAuth tokens terminate before
``mcp.request.started`` fires.  Includes ``auth_mode`` from the ASGI scope
(set by ``AuthMiddleware``) in every event payload for admission-path
observability.

Tool-call extraction: for ``tools/call`` MCP methods, the middleware parses
``params.name`` from the JSON-RPC body to include the specific tool name in
both log lines and event payloads, enabling per-tool observability.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record
from request_profile import bind_request
from starlette.requests import Request

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


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
        - files tool with op 'read' and path '/a/b': 'op=read path=/a/b'
    """
    if not args:
        return ""
    if tool_name == "dispatch":
        inner = args.get("tool", "")
        return f"→{inner}" if inner else ""
    if tool_name in ("files", "context", "project"):
        op = args.get("op", "")
        path = args.get("path", "")
        return f"op={op} path={path}" if op else ""
    if tool_name == "cortex":
        inner = args.get("tool", "")
        return f"→{inner}" if inner else ""
    if tool_name == "cortex_boot":
        agent = args.get("agent", "web")
        return f"agent={agent}"
    if tool_name == "agent_bus":
        inner = args.get("tool", "")
        return f"→{inner}" if inner else ""
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
        "agent_bus": ("thread", "turn_number", "last", "status"),
    }
    for key in interesting_keys[tool_name]:
        value = nested.get(key)
        if value is not None and value != "":
            context[f"{tool_name}_{key}"] = value
    return context


class McpRequestEventsMiddleware:
    """Emit ``mcp.request.*`` signals only for authenticated ``/mcp`` HTTP traffic."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        if request.url.path != "/mcp":
            await self._app(scope, receive, send)
            return

        client_ip = request.client.host if request.client else "unknown"
        method = request.method
        auth_mode = str(scope.get("auth_mode", "unknown"))
        profile = str(scope.get("mcp_profile", "default"))
        t0 = monotonic_now()
        mcp_method = ""
        tool_name = ""
        jsonrpc_id: Any = None
        correlation_hdr = (
            request.headers.get("x-cloudproxy-correlation-id", "") or ""
        ).strip()

        if method == "POST":
            msg = await receive()
            body = msg.get("body", b"")
            jsonrpc_id = _extract_jsonrpc_id(body)
            mcp_method, tool_name, tool_args = _extract_call_metadata(body)
            request_tool_context = _extract_request_tool_context(tool_name, tool_args)
            orig_receive = receive
            body_sent = False

            async def tee_receive() -> Message:
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return msg
                return await orig_receive()

            receive = tee_receive

        started_payload: dict[str, Any] = {
            "method": method,
            "client_ip": client_ip,
            "mcp_method": mcp_method,
            "auth_mode": auth_mode,
            **({"tool_name": tool_name} if tool_name else {}),
        }

        record("mcp.request.started", **started_payload)

        transport_started: dict[str, Any] = {
            "transport": "https",
            "method": method,
            "client_ip": client_ip,
            "mcp_method": mcp_method,
            "auth_mode": auth_mode,
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

        response_bytes = 0
        stream_opened = False

        async def measuring_send(message: Message) -> None:
            nonlocal response_bytes, stream_opened
            if message.get("type") == "http.response.body":
                chunk = message.get("body", b"") or b""
                response_bytes += len(chunk)
                if not stream_opened and len(chunk) > 0:
                    stream_opened = True
                    opened_payload: dict[str, Any] = {
                        "transport": "https",
                        "client_ip": client_ip,
                        "mcp_method": mcp_method,
                        "auth_mode": auth_mode,
                    }
                    if correlation_hdr:
                        opened_payload["cloudproxy_correlation_id"] = correlation_hdr
                    if jsonrpc_id is not None:
                        opened_payload["jsonrpc_id"] = jsonrpc_id
                    if tool_name:
                        opened_payload["tool_name"] = tool_name
                    record("mcp.transport.stream.opened", **opened_payload)
            await send(message)

        with bind_request(
            profile,
            request_profile=profile,
            client_ip=client_ip,
            auth_mode=auth_mode,
            mcp_method=mcp_method,
            tool_name=tool_name or None,
            jsonrpc_id=jsonrpc_id,
            cloudproxy_correlation_id=correlation_hdr or None,
            **request_tool_context,
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
                }
                if tool_name:
                    failed_payload["tool_name"] = tool_name
                record("mcp.request.failed", **failed_payload)
                tfail = {
                    "transport": "https",
                    "client_ip": client_ip,
                    "duration_s": round(duration, 3),
                    "error": str(exc),
                    "exc_type": type(exc).__name__,
                    "auth_mode": auth_mode,
                    "mcp_method": mcp_method,
                    "response_bytes": response_bytes,
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
                    **({"tool_name": tool_name} if tool_name else {}),
                }
                record("mcp.request.completed", **completed_payload)
                tdone: dict[str, Any] = {
                    "transport": "https",
                    "client_ip": client_ip,
                    "duration_s": round(duration, 3),
                    "auth_mode": auth_mode,
                    "response_bytes": response_bytes,
                    "mcp_method": mcp_method,
                }
                if correlation_hdr:
                    tdone["cloudproxy_correlation_id"] = correlation_hdr
                if jsonrpc_id is not None:
                    tdone["jsonrpc_id"] = jsonrpc_id
                if tool_name:
                    tdone["tool_name"] = tool_name
                record("mcp.transport.request.completed", **tdone)
                if tool_name:
                    logger.info(
                        "MCP tool done: %s (%.1fs, %dB)",
                        tool_name,
                        duration,
                        response_bytes,
                    )
