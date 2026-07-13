"""Proxy-side MCP tool executor for providers without native MCP support.

Used by Google and OpenRouter on ``/v1/chat/completions`` when a ``-mcp``
model is requested.  Fetches tool schemas from the live MCP server at
startup (JSON-RPC ``tools/list``), injects them as OpenAI function tools,
and executes tool calls locally via JSON-RPC ``tools/call``.

Providers with native MCP (Anthropic ``mcp_servers``, OpenAI/xAI Responses
API ``type: "mcp"``) bypass this entirely — the provider connects back to
the MCP server and runs its own tool loop.

System-prompt boot directive: when the first system message contains a
``cortex_brief(...)`` call with primary params (``seat`` / alias ``agent`` and/or
``family`` / ``platform`` / optional ``role``), the executor pre-calls
the MCP tool and replaces the directive span with the briefing card so
the model starts with operational context rather than an opaque
instruction. Resolution precedence matches the MCP ``cortex_brief`` tool:
``seat`` / ``agent`` override explicit ``family`` / ``platform`` when the slug
parses as ``{family}-{platform}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from copy import deepcopy
from typing import Any

import httpx
from llm_adapters._tool_schema import sanitize_tool_parameters

from services.universal_cloud_proxy.boot_directive import (
    boot_tool_arguments,
    parse_boot_directive,
)

logger = logging.getLogger(__name__)

_MAX_TOOL_CALL_TIMEOUT = 20 * 60.0
_MAX_LOOP_TIMEOUT = 300.0
_DEFAULT_MAX_ITERATIONS = 10
_JSONRPC_VERSION = "2.0"
_RESTART_ERROR_CODE = -32099
_RESTART_ERROR_REASON = "server_restarting"
_RESTART_ERROR_MESSAGE = "MCP server is restarting; retry in 30s"
_RESTART_RETRY_DELAYS_S = (5.0, 15.0)

_DISPATCH_COMPAT_TOOL_DEFS: dict[str, dict[str, Any]] = {
    "web_fetch": {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch a public HTTP(S) URL and return extracted readable content "
                "or the raw response body."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                    "start_offset": {"type": "integer"},
                    "method": {"type": "string"},
                    "headers": {"type": "object", "properties": {}},
                    "body": {"type": "string"},
                    "raw": {"type": "boolean"},
                },
                "required": ["url"],
            },
        },
    }
}


def _boot_seat_slug(kwargs: dict[str, str]) -> str | None:
    seat = kwargs.get("seat") or kwargs.get("agent")
    if seat:
        from agent_seat.registry import normalize_agent_slug

        return normalize_agent_slug(seat)
    family = (kwargs.get("family") or "claude").lower()
    platform = (kwargs.get("platform") or "cursor").lower()
    return f"{family}-{platform}"


def _is_web_seat(seat: str) -> bool:
    from agent_seat.profiles import load_profiles

    parts = seat.split("-", 1)
    if len(parts) != 2:
        return False
    profile = load_profiles().get((parts[0], parts[1]))
    return profile is not None and profile.platform == "web"


async def _append_web_invariant_bodies(content: str, seat: str) -> str:
    """Web invariant append retired — skills operator-attached in claude.ai UI."""
    del seat
    return content


def _jsonrpc_request(
    method: str, params: dict[str, Any], req_id: int = 1
) -> dict[str, Any]:
    return {
        "jsonrpc": _JSONRPC_VERSION,
        "method": method,
        "params": params,
        "id": req_id,
    }


def _parse_sse_json(text: str) -> dict[str, Any]:
    """Extract the JSON-RPC result from an SSE response body.

    MCP Streamable HTTP transport wraps JSON-RPC responses in SSE:
        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{...}}
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            payload = stripped[len("data:") :].strip()
            if payload and payload != "[DONE]":
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _restart_error_payload() -> dict[str, Any]:
    return {
        "error": {
            "code": _RESTART_ERROR_CODE,
            "message": _RESTART_ERROR_MESSAGE,
            "data": {
                "reason": _RESTART_ERROR_REASON,
                "retry_after_s": 30,
            },
        }
    }


def _payload_is_restart_error(payload: dict[str, Any]) -> bool:
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    data = error.get("data")
    return (
        error.get("code") == _RESTART_ERROR_CODE
        and isinstance(data, dict)
        and data.get("reason") == _RESTART_ERROR_REASON
    )


def _is_restart_transport_error(exc: Exception) -> bool:
    """True when the failure looks like MCP died mid-TLS read during restart."""
    if isinstance(
        exc,
        httpx.RemoteProtocolError
        | httpx.ConnectError
        | httpx.ConnectTimeout
        | httpx.ReadError,
    ):
        return True
    text = str(exc)
    return (
        "UNEXPECTED_EOF_WHILE_READING" in text
        or "EOF occurred in violation of protocol" in text
    )


def _response_is_restart_error(resp: httpx.Response) -> bool:
    if resp.status_code != 503:
        return False
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        payload = _parse_sse_json(resp.text)
    return _payload_is_restart_error(payload)


def _mcp_schema_to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an MCP tool schema to OpenAI function tool format."""
    input_schema = tool.get("inputSchema", {})
    params: dict[str, Any] = {"type": "object"}
    if "properties" in input_schema:
        params["properties"] = input_schema["properties"]
    if "required" in input_schema:
        params["required"] = input_schema["required"]
    params = sanitize_tool_parameters(params)
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": params,
        },
    }


def _compat_dispatch_tool_defs(discovered_names: set[str]) -> list[dict[str, Any]]:
    """Return synthetic tool defs for known overflow tools hidden from tools/list."""
    return [
        deepcopy(defn)
        for name, defn in _DISPATCH_COMPAT_TOOL_DEFS.items()
        if name not in discovered_names
    ]


class McpToolExecutor:
    """Proxy-side MCP client: discovers tools at startup, executes them during the agentic loop."""

    def __init__(self, *, mcp_url: str, auth_token: str | None = None) -> None:
        self._mcp_url = mcp_url
        self._auth_token = auth_token
        self._tools: list[dict[str, Any]] = []
        self._openai_defs: list[dict[str, Any]] = []
        self._dispatch_compat_names: set[str] = set()
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        """Fetch tool schemas from the MCP server via JSON-RPC ``tools/list``."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
        )
        try:
            resp = await self._client.post(
                self._mcp_url,
                json=_jsonrpc_request("tools/list", {}),
                headers=self._headers(),
            )
            resp.raise_for_status()
            body = _parse_sse_json(resp.text)
            tools = body.get("result", {}).get("tools", [])
            self._tools = [t for t in tools if isinstance(t, dict) and "name" in t]
            self._openai_defs = [_mcp_schema_to_openai_tool(t) for t in self._tools]
            discovered_names = {
                t["name"] for t in self._tools if isinstance(t.get("name"), str)
            }
            compat_defs = _compat_dispatch_tool_defs(discovered_names)
            self._dispatch_compat_names = {
                d["function"]["name"]
                for d in compat_defs
                if isinstance(d.get("function"), dict)
                and isinstance(d["function"].get("name"), str)
            }
            self._openai_defs.extend(compat_defs)
            logger.info(
                "McpToolExecutor: discovered %d tools from %s (+%d dispatch-compat)",
                len(self._tools),
                self._mcp_url,
                len(self._dispatch_compat_names),
            )
        except Exception:
            logger.warning(
                "McpToolExecutor: failed to fetch tools from %s — proxy tool loop unavailable",
                self._mcp_url,
                exc_info=True,
            )

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def available(self) -> bool:
        return bool(self._openai_defs)

    def get_openai_tool_defs(self) -> list[dict[str, Any]]:
        return list(self._openai_defs)

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a single tool call via JSON-RPC ``tools/call``."""
        if not self._client:
            return json.dumps({"error": "MCP executor not initialized"})
        target_name = name
        target_arguments = arguments
        if name in self._dispatch_compat_names:
            target_name = "dispatch"
            target_arguments = {"tool": name, "arguments": arguments}
        request_body = _jsonrpc_request(
            "tools/call",
            {"name": target_name, "arguments": target_arguments},
        )
        for attempt_index in range(len(_RESTART_RETRY_DELAYS_S) + 1):
            try:
                resp = await self._client.post(
                    self._mcp_url,
                    json=request_body,
                    headers=self._headers(),
                    timeout=_MAX_TOOL_CALL_TIMEOUT,
                )
                if _response_is_restart_error(resp):
                    raise httpx.RemoteProtocolError(_RESTART_ERROR_MESSAGE)
                resp.raise_for_status()
                body = _parse_sse_json(resp.text)
                result = body.get("result", {})
                structured = result.get("structuredContent")
                if isinstance(structured, dict):
                    return json.dumps(structured)
                content_blocks = result.get("content", [])
                parts = [
                    str(b.get("text", ""))
                    for b in content_blocks
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                return "\n".join(parts) if parts else json.dumps(result)
            except Exception as exc:
                if not _is_restart_transport_error(exc):
                    logger.error("McpToolExecutor tool %s failed: %s", name, exc)
                    return json.dumps({"error": f"Tool execution failed: {exc}"})
                if attempt_index < len(_RESTART_RETRY_DELAYS_S):
                    delay_s = _RESTART_RETRY_DELAYS_S[attempt_index]
                    logger.info(
                        "McpToolExecutor tool %s saw MCP restart (%s); retrying in %.0fs",
                        name,
                        exc,
                        delay_s,
                    )
                    await asyncio.sleep(delay_s)
                    continue
                logger.error(
                    "McpToolExecutor tool %s failed during restart: %s",
                    name,
                    exc,
                )
                return json.dumps(_restart_error_payload())
        return json.dumps(_restart_error_payload())

    async def _resolve_boot_directive(self, messages: list[dict[str, Any]]) -> None:
        """Pre-execute ``cortex_brief(...)`` when primary params appear in system prompt.

        Supported directive shapes mirror the MCP tool's primary params:
        ``agent="<seat-slug>"`` (hyphenated slugs), and/or
        ``family="..."``, ``platform="..."``, optional ``role="..."``.
        Unrecognized or param-less ``cortex_brief(...)`` spans are left unchanged.
        """
        if not messages:
            return
        first = messages[0]
        if first.get("role") != "system":
            return
        content = first.get("content") or ""
        parsed = parse_boot_directive(content)
        if not parsed:
            return
        matched_span, directive_kwargs = parsed
        boot_args = boot_tool_arguments(directive_kwargs)
        logger.info("McpToolExecutor: resolving boot directive %s", boot_args)
        result = await self.execute_tool("cortex_brief", boot_args)
        try:
            boot_data = json.loads(result)
            briefing = boot_data.get("briefing_card", result)
        except (json.JSONDecodeError, AttributeError):
            briefing = result
        updated = content.replace(matched_span, briefing, 1)
        seat = _boot_seat_slug(directive_kwargs)
        if seat:
            updated = await _append_web_invariant_bodies(updated, seat)
        first["content"] = updated

    async def run_tool_loop(
        self,
        forward_fn: Any,
        body: dict[str, Any],
        *,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    ) -> dict[str, Any]:
        """Run the proxy-side agentic tool loop.

        Injects dynamic tool defs, forwards to the provider, executes
        tool_calls locally, appends results, and re-submits until the
        model produces a final response or max_iterations is reached.

        ``forward_fn`` is an async callable: (body) -> dict (non-streaming
        chat completion JSON).
        """
        existing_tools = body.get("tools") or []
        body["tools"] = existing_tools + self.get_openai_tool_defs()
        original_stream = body.get("stream", False)
        body["stream"] = False

        await self._resolve_boot_directive(body.get("messages", []))

        loop_start = time.monotonic()
        response: dict[str, Any] = {}

        for iteration in range(max_iterations):
            if time.monotonic() - loop_start > _MAX_LOOP_TIMEOUT:
                logger.warning(
                    "McpToolExecutor: loop timeout after %d iterations", iteration
                )
                break

            response = await forward_fn(body)
            choices = response.get("choices", [])
            if not choices:
                break

            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                break

            body["messages"].append(message)

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                result = await self.execute_tool(name, args)
                body["messages"].append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result,
                    }
                )

            logger.info(
                "McpToolExecutor: iteration %d, %d tool calls executed",
                iteration + 1,
                len(tool_calls),
            )

        if original_stream:
            body["stream"] = True

        return response

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-MCP-Structured-Capable": "1",
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers
