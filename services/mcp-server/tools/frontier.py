"""Frontier generation — full-fidelity vendor-native LLM calls.

Routes through Stargate's provider-native endpoints
(``/api/v1/providers/{provider}/...``) so all cloud traffic is observed and
controlled centrally.  The ``llm_adapters`` layer builds the provider-native
request body and parses the response; Stargate → cloud-proxy adds auth
injection and event publishing.

Preserves thinking traces, encrypted reasoning, server-side tools, cache
affinity, and structured outputs that llm_generate normalizes away.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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

from ._cortex_relay import _cx
from ._file_helpers import read_file_result
from .cortex_v2 import run_cortex_boot

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://host.docker.internal:9999")
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
_BOOT_SEPARATOR = "\n\n---\n\n"
_VALID_BOOT_LEVELS = {"none", "minimal", "full"}

_PROVIDER_NATIVE_PATHS: dict[str, str] = {
    "anthropic": "/api/v1/providers/anthropic/messages",
    "xai": "/api/v1/providers/xai/responses",
    "openai": "/api/v1/providers/openai/responses",
}


def _compose_system_prompt(boot_context: str, caller_system: str) -> str:
    """Prepend boot context to caller's system prompt with a visual separator.

    Three cases:
    - Both present → boot_context + separator + caller_system
    - Only boot_context → boot_context alone
    - No boot_context → caller_system unchanged (common case until #8 lands)
    """
    if not boot_context:
        return caller_system
    if not caller_system:
        return boot_context
    return f"{boot_context}{_BOOT_SEPARATOR}{caller_system}"


def _read_boot_ref(boot_ref: str) -> str:
    """Read a curated notes profile from the MCP files sandbox."""
    if not boot_ref.startswith("notes/"):
        raise ValueError("boot_ref must point into the notes/ tree")
    result = read_file_result(boot_ref)
    return str(result["content"])


def _default_minimal_brief() -> str:
    """Identity + deadlines + clock for lightweight frontier dispatch."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"Current datetime: {now}",
        "You are a frontier subagent dispatched by another frontier model.",
        "Complete the assigned task using only the context you were given. If that context is insufficient, say so explicitly.",
    ]

    deadlines = _cx("GET", "/deadlines")
    if "error" in deadlines:
        return "\n".join(lines)

    items = deadlines.get("items")
    if not isinstance(items, list) or not items:
        return "\n".join(lines)

    lines.append("")
    lines.append("Active deadlines:")
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        matter = str(
            item.get("matter_name") or item.get("matter_id") or "Unknown matter"
        )
        deadline = str(item.get("deadline_name") or "Unnamed deadline")
        deadline_date = item.get("deadline_date")
        suffix = f" by {deadline_date}" if deadline_date else ""
        lines.append(f"- {matter}: {deadline}{suffix}")
    return "\n".join(lines)


def _assemble_boot_context(boot: str, boot_ref: str | None) -> str:
    """Resolve explicit boot context for frontier-to-frontier dispatch."""
    boot_level = (boot or "none").strip().lower()
    if boot_level not in _VALID_BOOT_LEVELS:
        raise ValueError(
            f"Invalid boot {boot!r}. Must be one of: {sorted(_VALID_BOOT_LEVELS)}"
        )

    if boot_level == "none":
        if boot_ref:
            raise ValueError("boot_ref requires boot='minimal'")
        return ""

    if boot_level == "minimal":
        if boot_ref:
            return _read_boot_ref(boot_ref)
        return _default_minimal_brief()

    if boot_ref:
        raise ValueError("boot_ref is only supported with boot='minimal'")

    result = run_cortex_boot(agent="subagent")
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    narrative = result.get("boot_narrative")
    if not isinstance(narrative, str) or not narrative.strip():
        raise RuntimeError("cortex_boot returned no boot_narrative")
    return narrative


def register_frontier_tools(mcp: FastMCP) -> None:
    """Register the frontier_generate tool on the MCP server instance."""

    @mcp.tool()
    def frontier_generate(
        messages: list[dict[str, Any]],
        model: str = "anthropic/claude-sonnet-4",
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: list[str] | None = None,
        seed: int | None = None,
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        thinking: dict[str, Any] | None = None,
        effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        inject_mcp: bool = True,
        provider_options: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        reasoning_trace: list[dict[str, Any]] | None = None,
        boot: str = "none",
        boot_ref: str | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Full-fidelity generation preserving vendor-native features.

        Routes through Stargate's provider-native endpoints so all traffic
        is centrally observed.  Use ``frontier_generate`` instead of
        ``llm_generate`` when you need:
        - **Thinking traces**: Anthropic extended thinking or xAI encrypted reasoning
        - **Server-side tools**: xAI web_search, x_search, code_interpreter
        - **Cache affinity**: xAI conversation_id for prompt cache hits
        - **Reasoning continuity**: pass encrypted blocks between turns
        - **Structured output**: JSON schema enforcement via response_format
        - **Explicit boot context**: dispatch another frontier with minimal or full boot

        For simple text generation, prefer ``llm_generate`` — it's simpler and
        returns a normalized ``{content, model, usage, provider}`` shape.

        Args:
            messages: Conversation messages (list of ``{role, content}`` dicts).
            model: Routed model identifier (e.g. ``anthropic/claude-sonnet-4``,
                ``xai/grok-3-mini``).
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate. None = provider default (no cap).
            temperature: Sampling temperature. None = provider default.
            top_p: Nucleus sampling. None = provider default.
            stop_sequences: Stop strings. None = no custom stops.
            seed: Random seed (xAI/OpenAI only). None = non-deterministic.
            stream: Reserved for future streaming support.
            response_format: JSON schema enforcement. Anthropic maps this to
                ``output_config.format``; Responses adapters map it to ``text.format``.
            thinking: Reasoning config. Anthropic supports adaptive or extended
                thinking. xAI uses ``{"effort": "high", "include_encrypted": true}``.
            effort: Anthropic output effort (``max`` | ``high`` | ``medium`` | ``low``).
            tools: Function calling and/or server-side tools.
            tool_choice: Tool selection strategy.
            inject_mcp: Inject remote MCP server as tool (default True).
            provider_options: Per-provider escape hatch
                (e.g. ``{"xai": {"deferred": true}}``).
            conversation_id: Cache affinity ID. xAI: ``x-grok-conv-id`` header.
            reasoning_trace: Encrypted reasoning blocks from prior response
                to chain multi-turn reasoning.
            boot: Explicit boot depth for subagent dispatch
                (``none`` | ``minimal`` | ``full``).
            boot_ref: Optional curated notes profile under ``notes/`` for
                ``boot="minimal"`` requests.
            include_raw: If True, include full provider response in ``raw`` field.

        Returns:
            Structured response with content, thinking, tool_calls, usage,
            response_id, and optionally raw provider response. Returns
            ``{"error": "..."}`` on failure.
        """
        parsed = ModelId.parse(model)
        routing = parsed.routing_layer
        provider_key = effective_provider_for_model(parsed.provider)

        t0 = monotonic_now()
        record(
            "mcp.frontier.generate.called",
            model=model,
            routing=routing,
            provider=provider_key,
            has_thinking=thinking is not None,
            has_tools=tools is not None,
            has_conversation_id=conversation_id is not None,
            boot_level=boot,
        )

        if routing == "openrouter":
            record(
                "mcp.frontier.generate.error",
                error="openrouter_not_implemented",
            )
            return {"error": "OpenRouter routing not yet implemented"}

        if stream:
            record(
                "mcp.frontier.generate.error",
                error="streaming_not_implemented",
            )
            return {"error": "Streaming not yet implemented for frontier_generate"}

        native_path = _PROVIDER_NATIVE_PATHS.get(provider_key)
        if native_path is None:
            record(
                "mcp.frontier.generate.error",
                error="no_native_path",
                provider=provider_key,
            )
            return {"error": f"No native endpoint for provider {provider_key}"}

        adapter = resolve_llm_adapter(parsed.provider)
        if adapter is None:
            record(
                "mcp.frontier.generate.error",
                error="missing_api_key",
                provider=provider_key,
            )
            return {
                "error": f"Provider {provider_key} not configured (missing API key)",
            }

        if not hasattr(adapter, "build_frontier_request"):
            record(
                "mcp.frontier.generate.error",
                error="adapter_missing_frontier",
                provider=provider_key,
            )
            return {
                "error": f"Provider {provider_key} does not support frontier requests"
            }

        api_model = parsed.api_model_id

        try:
            boot_context = _assemble_boot_context(boot, boot_ref)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            record(
                "mcp.frontier.generate.error",
                error="boot_context_invalid",
                provider=provider_key,
            )
            return {"error": str(exc)}
        effective_system = _compose_system_prompt(boot_context, system)

        req = FrontierRequest(
            messages=messages,
            model=api_model,
            max_tokens=max_tokens,
            system=effective_system,
            inject_mcp=parsed.is_mcp or inject_mcp,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
            seed=seed,
            stream=stream,
            thinking=thinking,
            effort=effort,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            boot=boot,
            boot_ref=boot_ref,
            conversation_id=conversation_id,
            reasoning_trace=reasoning_trace,
            provider_options=provider_options,
        )
        mcp_cfg = mcp_config_from_env()
        _url, _headers, json_body = adapter.build_frontier_request(
            req, mcp_cfg if mcp_cfg else None
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
            record(
                "mcp.frontier.generate.error",
                error="timeout",
                provider=provider_key,
            )
            return {"error": "Upstream timeout"}
        except httpx.RequestError as exc:
            logger.error("Frontier upstream request failed: %s", exc)
            record(
                "mcp.frontier.generate.error",
                error="connection",
                provider=provider_key,
            )
            return {"error": "Upstream connection failed"}

        if resp.status_code >= 400:
            logger.warning(
                "Frontier upstream returned %d for model=%s provider=%s",
                resp.status_code,
                model,
                provider_key,
            )
            record(
                "mcp.frontier.generate.error",
                error=f"upstream_{resp.status_code}",
                provider=provider_key,
            )
            return {
                "error": f"Upstream error ({resp.status_code})",
                "detail": resp.text[:500],
            }

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
            model=result.get("model", api_model),
            provider=result.get("provider", provider_key),
            has_thinking=result.get("thinking") is not None,
            has_tool_calls=result.get("tool_calls") is not None,
            has_server_tool_calls=(result.get("server_tool_calls") is not None),
            input_tokens=result.get("usage", {}).get("input_tokens", 0),
            output_tokens=result.get("usage", {}).get("output_tokens", 0),
        )
        logger.info(
            "frontier_generate completed: %.3fs, provider=%s model=%s via stargate",
            duration,
            result.get("provider", provider_key),
            model,
        )
        return result
