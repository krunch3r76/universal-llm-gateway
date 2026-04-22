"""Proxy-side MCP tool executor for providers without native MCP support.

Used by Google and OpenRouter on ``/v1/chat/completions`` when a ``-mcp``
model is requested.  Fetches tool schemas from the live MCP server at
startup (JSON-RPC ``tools/list``), injects them as OpenAI function tools,
and executes tool calls locally via JSON-RPC ``tools/call``.

Providers with native MCP (Anthropic ``mcp_servers``, OpenAI/xAI Responses
API ``type: "mcp"``) bypass this entirely — the provider connects back to
the MCP server and runs its own tool loop.

System-prompt boot directive: when the first system message matches
``cortex_boot(agent="<name>")``, the executor pre-calls the tool and
replaces the system message with the briefing card so the model starts
with full operational context rather than an opaque instruction.
"""

from __future__ import annotations

import json
import logging
import re
import time
from copy import deepcopy
from typing import Any

import httpx
from llm_adapters._tool_schema import sanitize_tool_parameters

logger = logging.getLogger(__name__)

_MAX_TOOL_CALL_TIMEOUT = 30.0
_MAX_LOOP_TIMEOUT = 300.0
_DEFAULT_MAX_ITERATIONS = 10
_JSONRPC_VERSION = "2.0"

_BOOT_DIRECTIVE_RE = re.compile(
    r"""cortex_boot\(\s*agent\s*=\s*["'](\w+)["']\s*\)""",
)

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
        try:
            resp = await self._client.post(
                self._mcp_url,
                json=_jsonrpc_request(
                    "tools/call",
                    {"name": target_name, "arguments": target_arguments},
                ),
                headers=self._headers(),
                timeout=_MAX_TOOL_CALL_TIMEOUT,
            )
            resp.raise_for_status()
            body = _parse_sse_json(resp.text)
            result = body.get("result", {})
            content_blocks = result.get("content", [])
            parts = [
                str(b.get("text", ""))
                for b in content_blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return "\n".join(parts) if parts else json.dumps(result)
        except Exception as exc:
            logger.error("McpToolExecutor tool %s failed: %s", name, exc)
            return json.dumps({"error": f"Tool execution failed: {exc}"})

    async def _resolve_boot_directive(self, messages: list[dict[str, Any]]) -> None:
        """Pre-execute ``cortex_boot(agent="...")`` if it appears in the system prompt.

        When OpenWebUI (or any client) includes ``cortex_boot(agent="oppie")``
        in the system message, the model can't reliably interpret it as a tool
        call instruction.  Instead we execute it server-side and replace the
        directive with the briefing card inline, preserving any surrounding
        system prompt content (e.g. a birth/seed prompt).
        """
        if not messages:
            return
        first = messages[0]
        if first.get("role") != "system":
            return
        content = first.get("content") or ""
        m = _BOOT_DIRECTIVE_RE.search(content)
        if not m:
            return
        agent = m.group(1)
        logger.info("McpToolExecutor: resolving boot directive for agent=%s", agent)
        result = await self.execute_tool("cortex_boot", {"agent": agent})
        try:
            boot_data = json.loads(result)
            briefing = boot_data.get("briefing_card", result)
        except (json.JSONDecodeError, AttributeError):
            briefing = result
        first["content"] = content.replace(m.group(0), briefing, 1)

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
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers
