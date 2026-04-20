"""Shared helpers for the unified ``frontier_generate`` tool.

Thin MCP-side wrapper around ``libs/agent_seat/native_loop.run_native_tool_loop``.
Owns MCP-specific concerns: Stargate HTTP transport, event recording, boot
context assembly (via ``_frontier_boot``), termination-shadow detection, and
synchronous bridging (MCP tool handlers run in a threadpool, so the async
native loop is dispatched via ``asyncio.run`` at the tool boundary).

Boot context assembly lives in ``_frontier_boot``; the tool-resolution loop
and response parsing live in ``libs/agent_seat/native_loop``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from agent_seat import TEAM_TOOL_DEFINITIONS, TOOL_DEFINITIONS
from agent_seat.native_loop import (
    NATIVE_PATHS,
    NativeLoopResult,
    run_native_tool_loop,
)
from llm_adapters import (
    FrontierRequest,
    effective_provider_for_model,
)
from mcp_events import monotonic_now, record
from model_id import ModelId
from universal_logging import get_logger

from ._frontier_boot import (
    assemble_boot_context,
    compose_system_prompt,
    normalize_boot_level,
    should_inject_tools,
)

logger = get_logger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_DEFAULT_READ_TIMEOUT = 600.0
_MAX_TOOL_TURNS = 10

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

GOOGLE_SERVER_TOOL_MAP: dict[str, dict[str, Any]] = {
    "google_search": {"google_search": {}},
    "code_execution": {"code_execution": {}},
}


def _build_mcp_tool_event_callback(
    provider_key: str,
) -> Any:
    """Translate native_loop tool events to MCP-side ``mcp.frontier.tool.executed`` records."""

    def _cb(signal: str, payload: dict[str, Any]) -> None:
        record(
            "mcp.frontier.tool.executed",
            tool=str(payload.get("tool_name", "")),
            turn=int(payload.get("turn", 0)),
            provider=provider_key,
            ok=bool(payload.get("ok", signal.endswith(".called"))),
            elapsed_ms=float(payload.get("elapsed_ms", 0.0)),
        )

    return _cb


def _build_stargate_sender(timeout_seconds: float) -> Any:
    """Return an async ``send_native(path, body) -> dict`` that posts to Stargate.

    Constructs a fresh ``httpx.AsyncClient`` per loop run so the client's
    lifecycle is scoped to the synchronous MCP tool call — no shared client
    to clean up across threads.
    """
    clamped = min(max(timeout_seconds, 30.0), 1800.0)
    timeout = httpx.Timeout(connect=10.0, read=clamped, write=30.0, pool=10.0)

    async def _send(path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        url = f"{_STARGATE_URL}{path}"
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                url,
                json=json_body,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                detail = resp.text[:500] if resp.text else ""
                raise _UpstreamHTTPError(resp.status_code, detail)
            return resp.json()

    return _send


class _UpstreamHTTPError(Exception):
    """Raised by the MCP send_native closure on >=400 response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"upstream {status_code}: {detail[:200]}")
        self.status_code = status_code
        self.detail = detail


def execute_frontier(
    *,
    model: str,
    req: FrontierRequest,
    include_raw: bool = False,
    tool_name: str = "frontier",
    timeout: float | None = None,
) -> dict[str, Any]:
    """Dispatch an MCP ``frontier_generate`` call through ``run_native_tool_loop``.

    The MCP tool runs synchronously on a threadpool worker; this function
    bridges to the async native loop via ``asyncio.run``. Emits the existing
    ``mcp.frontier.generate.*`` event family so the observability surface
    stays stable.

    ``timeout`` overrides the default read timeout (seconds). Downstream hops
    (Stargate → cloud-proxy → provider) allow up to 1800s.
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

    if provider_key not in NATIVE_PATHS:
        record(
            "mcp.frontier.generate.error",
            error="no_native_path",
            provider=provider_key,
        )
        return {"error": f"No native endpoint for provider {provider_key}"}

    effective_timeout = timeout if timeout is not None else _DEFAULT_READ_TIMEOUT
    send_native = _build_stargate_sender(effective_timeout)
    on_tool_event = _build_mcp_tool_event_callback(provider_key)
    max_turns = _MAX_TOOL_TURNS if req.mcp_tool_loop else 1

    try:
        loop_result: NativeLoopResult = asyncio.run(
            run_native_tool_loop(
                model=model,
                req=req,
                send_native=send_native,
                max_turns=max_turns,
                on_tool_event=on_tool_event,
            )
        )
    except _UpstreamHTTPError as exc:
        error_key = f"upstream_{exc.status_code}"
        logger.warning(
            "Frontier upstream %d for model=%s provider=%s",
            exc.status_code,
            model,
            provider_key,
        )
        record(
            "mcp.frontier.generate.error",
            error=error_key,
            provider=provider_key,
        )
        return {
            "error": f"Upstream error ({exc.status_code})",
            "detail": exc.detail,
        }
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
        record(
            "mcp.frontier.generate.error",
            error="connection",
            provider=provider_key,
        )
        return {"error": "Upstream connection failed"}
    except ValueError as exc:
        # Provider misconfiguration: no native path, no API key, or adapter
        # lacks build_frontier_request. Classify as structural.
        logger.warning(
            "Frontier native loop misconfigured for provider=%s: %s",
            provider_key,
            exc,
        )
        record(
            "mcp.frontier.generate.error",
            error="adapter_misconfigured",
            provider=provider_key,
        )
        return {"error": str(exc)}

    result: dict[str, Any] = {
        "content": loop_result.content,
        "thinking": loop_result.reasoning,
        "usage": loop_result.usage,
        "provider": loop_result.provider,
        "model": (
            (loop_result.raw or {}).get("model") or api_model
            if isinstance(loop_result.raw, dict)
            else api_model
        ),
        "finish_reason": loop_result.finish_reason,
        "block_reason": loop_result.block_reason,
    }
    if loop_result.reasoning is None:
        result.pop("thinking", None)
    if loop_result.finish_reason is None:
        result.pop("finish_reason", None)
    if loop_result.block_reason is None:
        result.pop("block_reason", None)
    if include_raw and loop_result.raw is not None:
        result["raw"] = loop_result.raw
    tool_calls_total = loop_result.tool_calls_made
    if tool_calls_total > 0:
        result["tool_calls_made"] = tool_calls_total
    if loop_result.exhausted and req.mcp_tool_loop:
        result["warning"] = f"Tool loop reached max turns ({max_turns})"

    duration = monotonic_now() - t0
    output_tokens = result.get("usage", {}).get("output_tokens", 0)
    finish_reason = result.get("finish_reason")
    block_reason = result.get("block_reason")
    record(
        "mcp.frontier.generate.completed",
        duration_s=round(duration, 3),
        tool=tool_name,
        model=result.get("model", api_model),
        provider=result.get("provider", provider_key),
        has_thinking="thinking" in result,
        has_tool_calls=tool_calls_total > 0,
        input_tokens=result.get("usage", {}).get("input_tokens", 0),
        output_tokens=output_tokens,
        tool_calls_made=tool_calls_total,
        finish_reason=finish_reason,
        block_reason=block_reason,
    )
    # NOTE: ``mcp.frontier.output.short`` and
    # ``mcp.frontier.thought.termination.shadow`` were hoisted to the
    # ``frontier_dispatch_v1`` pipeline handler in Task-7 Phase 1. Callers
    # on the MCP ``frontier_generate`` surface still benefit transparently
    # once ``frontier_generate`` is collapsed onto the pipeline in Phase 2+;
    # until then, only pipeline-originated dispatches emit these anomalies.
    if "error" not in result and req.boot in ("team", "full"):
        result["_next"] = (
            "If this consultation surfaced a decision, insight, or correction "
            "worth remembering: cortex assert or observe with "
            "evidence_uris pointing to the agent-bus thread. "
            "If Cortex lacked context this consultation needed, "
            "record that gap via cortex observe."
        )
    return result


def build_frontier_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    system: str,
    boot: str,
    boot_ref: str | None,
    agent: str = "",
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
    remote_mcp: bool = False,
) -> FrontierRequest | dict[str, Any]:
    """Assemble boot context and build FrontierRequest. Returns error dict on failure.

    Tool injection is driven by boot level:
    - boot="none" → no system prompt, no tools
    - boot!="none" + remote_mcp=False → client-side TOOL_DEFINITIONS injected;
      execute_frontier runs the tool resolution loop locally.
    - boot!="none" + remote_mcp=True → remote MCP entry injected pointing at
      the public MCP server; the provider calls our MCP server directly and
      manages the tool loop server-side. mcp_tool_loop=False.

    ``remote_mcp`` is caller-driven — the calling tool decides when remote MCP
    is appropriate (e.g. frontier="grok" for team/full boot where Oppie needs the
    full MCP surface including fs).

    When ``agent`` is provided, ``team`` and ``full`` boot levels prepend the
    agent's birth prompt (identity, role, values) before operational context.

    Provider-native server_tools (web_search, x_search, etc.) are passed directly
    via the ``tools`` argument and are independent of boot level.
    """
    parsed = ModelId.parse(model)
    try:
        boot_level = normalize_boot_level(boot)
        boot_context = assemble_boot_context(boot_level, boot_ref, agent=agent)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        record("mcp.frontier.generate.error", error="boot_context_invalid")
        return {"error": str(exc)}
    effective_system = compose_system_prompt(boot_context, system)

    merged_tools = list(tools or [])
    inject_tools = should_inject_tools(boot_level)
    use_remote_mcp = inject_tools and remote_mcp

    if inject_tools and not use_remote_mcp:
        merged_tools.extend(TOOL_DEFINITIONS)
        if boot_level in ("team", "full"):
            merged_tools.extend(TEAM_TOOL_DEFINITIONS)

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
        mcp_tool_loop=inject_tools and not use_remote_mcp,
        remote_mcp=use_remote_mcp,
    )
