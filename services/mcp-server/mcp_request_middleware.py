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
from typing import Any, TYPE_CHECKING

from mcp_events import monotonic_now, record
from request_profile import bind_profile
from starlette.requests import Request

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


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

        if method == "POST":
            msg = await receive()
            body = msg.get("body", b"")
            mcp_method, tool_name, tool_args = _extract_call_metadata(body)
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

        if tool_name:
            summary = _summarize_tool_args(tool_name, tool_args)
            log_detail = f" {summary}" if summary else ""
            logger.info("MCP tool call: %s%s", tool_name, log_detail)

        response_bytes = 0

        async def measuring_send(message: Message) -> None:
            nonlocal response_bytes
            if message.get("type") == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        with bind_profile(profile):
            try:
                await self._app(scope, receive, measuring_send)
            except Exception as exc:
                duration = monotonic_now() - t0
                record(
                    "mcp.request.failed",
                    method=method,
                    client_ip=client_ip,
                    duration_s=round(duration, 3),
                    error=str(exc),
                    exc_type=type(exc).__name__,
                    auth_mode=auth_mode,
                    mcp_method=mcp_method,
                    tool_name=tool_name,
                    response_bytes=response_bytes,
                )
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
                }
                completed_payload = {
                    "method": method,
                    "client_ip": client_ip,
                    "duration_s": round(duration, 3),
                    "auth_mode": auth_mode,
                    "response_bytes": response_bytes,
                    **({"tool_name": tool_name} if tool_name else {}),
                }
                record("mcp.request.completed", **completed_payload)
                if tool_name:
                    logger.info(
                        "MCP tool done: %s (%.1fs, %dB)",
                        tool_name,
                        duration,
                        response_bytes,
                    )
