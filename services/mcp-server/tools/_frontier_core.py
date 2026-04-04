"""Shared helpers for frontier generation tools (grok_generate, claude_generate, frontier_generate).

Handles boot context assembly, system prompt composition, adapter resolution,
Stargate HTTP calls, response parsing, and event recording.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from llm_adapters import (
    FrontierRequest,
    effective_provider_for_model,
    mcp_config_from_env,
    resolve_llm_adapter,
)
from mcp_events import monotonic_now, record
from model_id import ModelId
from universal_logging import get_logger

from ._file_helpers import read_file_result
from .cortex_named_tools import run_cortex_boot

logger = get_logger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
_BOOT_SEPARATOR = "\n\n---\n\n"
_VALID_BOOT_LEVELS = {"none", "mcp", "minimal", "full", "team"}

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


def _compose_system_prompt(boot_context: str, caller_system: str) -> str:
    if not boot_context:
        return caller_system
    if not caller_system:
        return boot_context
    return f"{boot_context}{_BOOT_SEPARATOR}{caller_system}"


def _read_boot_ref(boot_ref: str) -> str:
    if not boot_ref.startswith("notes/"):
        raise ValueError("boot_ref must point into the notes/ tree")
    result = read_file_result(boot_ref)
    return str(result["content"])


def _normalize_boot_level(boot: str) -> str:
    boot_level = (boot or "none").strip().lower()
    if boot_level not in _VALID_BOOT_LEVELS:
        raise ValueError(
            f"Invalid boot {boot!r}. Must be one of: {sorted(_VALID_BOOT_LEVELS)}"
        )
    if boot_level == "minimal":
        return "mcp"
    return boot_level


_SUBAGENT_PREAMBLE = """\
You are a team member consulted by the system owner.
Apply your own epistemic standards fully — if you identify errors or gaps in the supplied framing, flag them. Do not defer.

Cortex is the team's shared knowledge graph. When Cortex excerpts appear in context:
- Entities: typed nodes (`type:slug`). Assertions: claims with confidence (confirmed/believed/suspected/hypothesized).
- Absence of assertion ≠ negation — it means the information was not supplied.
- Parametric knowledge (from training) is not Cortex-grounded. Label the source when using both.

For this invocation, your Cortex grounding is the context supplied in this conversation. \
If you cannot ground a claim in the supplied context, mark it [UNGROUNDED] and note what query would resolve it.

Shared vocabulary: "Cortex" = the knowledge graph, not the service · \
"directive" = implement now · "ticket" = deferred work."""


_MCP_GROUNDING_GUARD = """\
Source discipline (invariant):
∀ factual claim about people, decisions, entities, or events: ground in Cortex \
via tool or tag [PARAMETRIC]. If Cortex has no data, state absence before \
offering parametric knowledge. No unmarked parametric claims."""


def _default_mcp_brief() -> str:
    lines = [
        "You are a frontier subagent dispatched by another frontier model.",
        "Operate only on the context supplied by the caller. "
        "If the task needs missing state, say so explicitly instead of assuming it.",
        "",
        "Tool surface orientation:",
        "- Use the direct primary tools when they are available.",
        "- Use dispatch(tool=..., arguments='{}') to reach non-primary MCP tools.",
        "",
        "Call conventions:",
        "- Dynamic project, journal, entity, and session context is caller-injected.",
        "- Do not assume hidden continuity beyond what the caller supplied.",
    ]
    return "\n".join(lines)


def _resolve_inject_mcp_default(boot_level: str, inject_mcp: bool | None) -> bool:
    if inject_mcp is not None:
        return inject_mcp
    return boot_level != "none"


def _assemble_boot_context(boot: str, boot_ref: str | None) -> str:
    boot_level = _normalize_boot_level(boot)
    if boot_level == "none":
        if boot_ref:
            raise ValueError("boot_ref requires boot='mcp' (legacy alias: 'minimal')")
        return ""
    if boot_level == "mcp":
        if boot_ref:
            seed_content = _read_boot_ref(boot_ref)
            return f"{_MCP_GROUNDING_GUARD}{_BOOT_SEPARATOR}{seed_content}"
        return _default_mcp_brief()
    if boot_level == "team":
        parts = [_SUBAGENT_PREAMBLE]
        if boot_ref:
            parts.append(_read_boot_ref(boot_ref))
        return _BOOT_SEPARATOR.join(parts)
    result = run_cortex_boot(agent="subagent")
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    narrative = result.get("boot_narrative")
    if not isinstance(narrative, str) or not narrative.strip():
        raise RuntimeError("cortex_boot returned no boot_narrative")
    parts = [_SUBAGENT_PREAMBLE]
    if boot_ref:
        parts.append(_read_boot_ref(boot_ref))
    parts.append(narrative)
    return _BOOT_SEPARATOR.join(parts)


def execute_frontier(
    *,
    model: str,
    req: FrontierRequest,
    include_raw: bool = False,
    tool_name: str = "frontier_generate",
) -> dict[str, Any]:
    """Resolve adapter, POST to Stargate provider-native endpoint, parse response."""
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

    mcp_cfg = mcp_config_from_env()
    _url, _headers, json_body = adapter.build_frontier_request(
        req, mcp_cfg if mcp_cfg else None
    )
    has_mcp_servers = bool(json_body.get("mcp_servers"))
    if has_mcp_servers:
        record(
            "mcp.frontier.generate.mcp.injected",
            model=model,
            tool=tool_name,
            provider=provider_key,
        )

    stargate_url = f"{_STARGATE_URL}{native_path}"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                stargate_url,
                json=json_body,
                headers={"Content-Type": "application/json"},
            )
    except httpx.TimeoutException:
        duration = monotonic_now() - t0
        has_mcp = bool(json_body.get("mcp_servers"))
        record(
            "mcp.frontier.generate.error",
            error="timeout",
            provider=provider_key,
            duration_s=round(duration, 3),
            has_mcp=has_mcp,
        )
        suffix = (
            " MCP tool callbacks may be hanging — check MCP server health and "
            "firewall rules for provider callback IPs."
            if has_mcp
            else ""
        )
        return {"error": f"Upstream timeout after {int(duration)}s.{suffix}"}
    except httpx.RequestError as exc:
        logger.error("Frontier upstream request failed: %s", exc)
        record("mcp.frontier.generate.error", error="connection", provider=provider_key)
        return {"error": "Upstream connection failed"}

    if resp.status_code >= 400:
        detail_text = resp.text[:500]
        error_key = f"upstream_{resp.status_code}"
        error_msg = f"Upstream error ({resp.status_code})"

        if "Connection to MCP server timed out" in detail_text:
            error_key = "mcp_server_unreachable"
            error_msg = (
                "Provider could not reach the MCP server for tool callbacks. "
                "The MCP server may be overloaded, unreachable from the provider's "
                "network, or blocked by a firewall."
            )

        logger.warning(
            "Frontier upstream %d for model=%s provider=%s: %s",
            resp.status_code,
            model,
            provider_key,
            error_key,
        )
        record(
            "mcp.frontier.generate.error",
            error=error_key,
            provider=provider_key,
        )
        return {"error": error_msg, "detail": detail_text}

    try:
        raw = resp.json()
    except json.JSONDecodeError:
        return {"error": "Upstream returned invalid JSON"}
    if not isinstance(raw, dict):
        return {"error": "Upstream returned non-object JSON"}

    duration = monotonic_now() - t0
    result = adapter.parse_frontier_response(raw)
    if include_raw:
        result["raw"] = raw

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
    )
    return result


def build_frontier_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    system: str,
    boot: str,
    boot_ref: str | None,
    inject_mcp: bool | None,
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
    """Assemble boot context and build FrontierRequest. Returns error dict on failure."""
    parsed = ModelId.parse(model)
    try:
        boot_level = _normalize_boot_level(boot)
        boot_context = _assemble_boot_context(boot_level, boot_ref)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        record("mcp.frontier.generate.error", error="boot_context_invalid")
        return {"error": str(exc)}
    effective_system = _compose_system_prompt(boot_context, system)
    effective_inject_mcp = parsed.is_mcp or _resolve_inject_mcp_default(
        boot_level, inject_mcp
    )

    return FrontierRequest(
        messages=messages,
        model=parsed.api_model_id,
        max_tokens=max_tokens,
        system=effective_system,
        inject_mcp=effective_inject_mcp,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        seed=seed,
        thinking=thinking,
        effort=effort,
        tools=tools,
        tool_choice=tool_choice,
        response_format=response_format,
        boot=boot_level,
        boot_ref=boot_ref,
        conversation_id=conversation_id,
        reasoning_trace=reasoning_trace,
        provider_options=provider_options,
    )
