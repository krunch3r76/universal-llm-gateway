"""Shared helpers for frontier generation tools (grok_generate, claude_generate, frontier_generate).

Handles Stargate HTTP calls, client-side tool resolution loop, response parsing,
and event recording. Boot context assembly lives in _frontier_boot.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from llm_adapters import (
    FrontierRequest,
    effective_provider_for_model,
    resolve_llm_adapter,
)
from mcp_events import monotonic_now, record
from model_id import ModelId
from universal_logging import get_logger

from ._agent_tools import TOOL_DEFINITIONS, execute_tool
from ._frontier_boot import (
    assemble_boot_context,
    compose_system_prompt,
    normalize_boot_level,
    should_inject_tools,
)

logger = get_logger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_DEFAULT_READ_TIMEOUT = 600.0
_TIMEOUT = httpx.Timeout(
    connect=10.0, read=_DEFAULT_READ_TIMEOUT, write=30.0, pool=10.0
)
_MAX_TOOL_TURNS = 10

_PROVIDER_NATIVE_PATHS: dict[str, str] = {
    "anthropic": "/api/v1/providers/anthropic/messages",
    "xai": "/api/v1/providers/xai/responses",
    "openai": "/api/v1/providers/openai/responses",
}

XAI_SERVER_TOOL_MAP: dict[str, dict[str, str]] = {
    "web_search": {"type": "web_search"},
    "x_search": {"type": "x_search"},
    "code_execution": {"type": "code_interpreter"},
}

OPENAI_SERVER_TOOL_MAP: dict[str, dict[str, str]] = {
    "web_search": {"type": "web_search_preview"},
    "code_interpreter": {"type": "code_interpreter"},
    "file_search": {"type": "file_search"},
}

_MCP_TOOL_NAMES: set[str] = {
    td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS
}


def _execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    provider_key: str,
    turn: int,
) -> list[dict[str, Any]]:
    """Execute MCP tool calls locally and return results for the adapter."""
    results: list[dict[str, Any]] = []
    for tc in tool_calls:
        tc_name = tc.get("name", "")
        tc_args = tc.get("input")
        if tc_args is None:
            raw_args = tc.get("arguments", "{}")
            tc_args = (
                json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            )

        logger.info("frontier tool [%s] turn %d: %s", provider_key, turn, tc_name)
        record(
            "mcp.frontier.tool.executed",
            tool=tc_name,
            turn=turn,
            provider=provider_key,
        )

        result_str = execute_tool(tc_name, tc_args)
        results.append({"id": tc.get("id"), "name": tc_name, "content": result_str})
    return results


def _handle_error_response(
    resp: httpx.Response,
    model: str,
    provider_key: str,
) -> dict[str, Any]:
    """Parse and record an HTTP error response from Stargate."""
    detail_text = resp.text[:500]
    error_key = f"upstream_{resp.status_code}"
    error_msg = f"Upstream error ({resp.status_code})"

    logger.warning(
        "Frontier upstream %d for model=%s provider=%s: %s",
        resp.status_code,
        model,
        provider_key,
        error_key,
    )
    record("mcp.frontier.generate.error", error=error_key, provider=provider_key)
    return {"error": error_msg, "detail": detail_text}


def execute_frontier(
    *,
    model: str,
    req: FrontierRequest,
    include_raw: bool = False,
    tool_name: str = "frontier_generate",
    timeout: float | None = None,
) -> dict[str, Any]:
    """Resolve adapter, POST to Stargate, and run client-side tool loop if needed.

    When ``req.mcp_tool_loop`` is True, the function implements a multi-turn
    tool resolution loop: POST → detect tool_use → execute locally → POST
    results → repeat, up to ``_MAX_TOOL_TURNS``.  This replaces the former
    MCP Connector pattern (injecting ``mcp_servers`` for provider-side callback).

    ``timeout`` overrides the default read timeout (seconds). Downstream hops
    (Stargate → cloud-proxy → provider) allow up to 1800s, so callers may
    request up to that ceiling for long-running subagent dispatches.
    """
    parsed = ModelId.parse(model)
    provider_key = effective_provider_for_model(parsed.provider)
    api_model = parsed.api_model_id
    t0 = monotonic_now()
    record(
        "mcp.frontier.generate.called",
        model=model,
        tool=tool_name,
        provider=provider_key,
        has_thinking=req.thinking is not None,
        has_tools=req.tools is not None,
        has_conversation_id=req.conversation_id is not None,
        boot_level=req.boot,
        mcp_tool_loop=req.mcp_tool_loop,
    )

    native_path = _PROVIDER_NATIVE_PATHS.get(provider_key)
    if native_path is None:
        record(
            "mcp.frontier.generate.error", error="no_native_path", provider=provider_key
        )
        return {"error": f"No native endpoint for provider {provider_key}"}

    adapter = resolve_llm_adapter(parsed.provider)
    if adapter is None:
        record(
            "mcp.frontier.generate.error",
            error="missing_api_key",
            provider=provider_key,
        )
        return {"error": f"Provider {provider_key} not configured (missing API key)"}
    if not hasattr(adapter, "build_frontier_request"):
        record(
            "mcp.frontier.generate.error",
            error="adapter_missing_frontier",
            provider=provider_key,
        )
        return {"error": f"Provider {provider_key} does not support frontier requests"}

    _url, _headers, json_body = adapter.build_frontier_request(req)

    stargate_url = f"{_STARGATE_URL}{native_path}"
    effective_timeout = _TIMEOUT
    if timeout is not None:
        clamped = min(max(timeout, 30.0), 1800.0)
        effective_timeout = httpx.Timeout(
            connect=10.0, read=clamped, write=30.0, pool=10.0
        )

    max_turns = _MAX_TOOL_TURNS if req.mcp_tool_loop else 1
    tool_calls_total = 0
    result: dict[str, Any] = {}
    raw: dict[str, Any] = {}

    try:
        with httpx.Client(timeout=effective_timeout) as http:
            for turn in range(max_turns):
                resp = http.post(
                    stargate_url,
                    json=json_body,
                    headers={"Content-Type": "application/json"},
                )

                if resp.status_code >= 400:
                    return _handle_error_response(resp, model, provider_key)

                try:
                    raw = resp.json()
                except json.JSONDecodeError:
                    return {"error": "Upstream returned invalid JSON"}
                if not isinstance(raw, dict):
                    return {"error": "Upstream returned non-object JSON"}

                result = adapter.parse_frontier_response(raw)
                tool_calls = result.get("tool_calls")

                if not tool_calls or not req.mcp_tool_loop:
                    break

                tool_results = _execute_tool_calls(tool_calls, provider_key, turn)
                tool_calls_total += len(tool_results)

                if hasattr(adapter, "append_tool_round"):
                    adapter.append_tool_round(json_body, raw, tool_results)
                else:
                    logger.warning(
                        "Adapter %s lacks append_tool_round; stopping tool loop",
                        provider_key,
                    )
                    break
            else:
                if req.mcp_tool_loop:
                    result["warning"] = f"Tool loop reached max turns ({max_turns})"
    except httpx.TimeoutException:
        duration = monotonic_now() - t0
        record(
            "mcp.frontier.generate.error",
            error="timeout",
            provider=provider_key,
            duration_s=round(duration, 3),
        )
        return {"error": f"Upstream timeout after {int(duration)}s."}
    except httpx.RequestError as exc:
        logger.error("Frontier upstream request failed: %s", exc)
        record("mcp.frontier.generate.error", error="connection", provider=provider_key)
        return {"error": "Upstream connection failed"}

    duration = monotonic_now() - t0
    if include_raw:
        result["raw"] = raw
    if tool_calls_total > 0:
        result["tool_calls_made"] = tool_calls_total

    record(
        "mcp.frontier.generate.completed",
        duration_s=round(duration, 3),
        tool=tool_name,
        model=result.get("model", api_model),
        provider=result.get("provider", provider_key),
        has_thinking=result.get("thinking") is not None,
        has_tool_calls=result.get("tool_calls") is not None,
        input_tokens=result.get("usage", {}).get("input_tokens", 0),
        output_tokens=result.get("usage", {}).get("output_tokens", 0),
        tool_calls_made=tool_calls_total,
    )
    return result


def build_frontier_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    system: str,
    boot: str,
    boot_ref: str | None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    stop_sequences: list[str] | None = None,
    seed: int | None = None,
    thinking: dict[str, Any] | None = None,
    effort: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    reasoning_trace: list[dict[str, Any]] | None = None,
    provider_options: dict[str, Any] | None = None,
) -> FrontierRequest | dict[str, Any]:
    """Assemble boot context and build FrontierRequest. Returns error dict on failure.

    Tool injection is driven entirely by boot level:
    - boot="none" → no system prompt, no tools (saves tokens for pure advisory calls)
    - boot="mcp"/"team"/"full" → system prompt loaded + TOOL_DEFINITIONS injected

    Tools are always client-side function-calling definitions (provider-agnostic).
    The MCP Connector pattern (mcp_servers in body) is web-only and never used here.
    Provider-native server_tools (web_search, x_search, etc.) are passed directly
    via the ``tools`` argument and are independent of boot level.
    """
    parsed = ModelId.parse(model)
    try:
        boot_level = normalize_boot_level(boot)
        boot_context = assemble_boot_context(boot_level, boot_ref)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        record("mcp.frontier.generate.error", error="boot_context_invalid")
        return {"error": str(exc)}
    effective_system = compose_system_prompt(boot_context, system)

    merged_tools = list(tools or [])
    inject_tools = should_inject_tools(boot_level)
    if inject_tools:
        merged_tools.extend(TOOL_DEFINITIONS)

    return FrontierRequest(
        messages=messages,
        model=parsed.api_model_id,
        max_tokens=max_tokens,
        system=effective_system,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        seed=seed,
        thinking=thinking,
        effort=effort,
        tools=merged_tools or None,
        tool_choice=tool_choice,
        response_format=response_format,
        boot=boot_level,
        boot_ref=boot_ref,
        conversation_id=conversation_id,
        reasoning_trace=reasoning_trace,
        provider_options=provider_options,
        mcp_tool_loop=inject_tools,
    )
