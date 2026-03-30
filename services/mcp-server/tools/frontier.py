"""Frontier generation — full-fidelity vendor-native LLM calls.

Clean generation primitive: no Cortex awareness, no implicit queries, no boot
sequence. Callers (agent_consult, pipelines, UI) compose context before dispatch.

Preserves thinking traces, encrypted reasoning, server-side tools, cache
affinity, and structured outputs that llm_generate normalizes away.
"""

from __future__ import annotations

import json
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

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def register_frontier_tools(mcp: FastMCP) -> None:
    """Register the frontier_generate tool on the MCP server instance."""

    @mcp.tool()
    def frontier_generate(
        messages: list[dict[str, Any]],
        model: str = "anthropic/claude-sonnet-4",
        system: str = "",
        max_tokens: int = 4096,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: list[str] | None = None,
        seed: int | None = None,
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        thinking: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        inject_mcp: bool = True,
        provider_options: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        reasoning_trace: list[dict[str, Any]] | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Full-fidelity generation preserving vendor-native features.

        Use ``frontier_generate`` instead of ``llm_generate`` when you need:
        - **Thinking traces**: Anthropic extended thinking or xAI encrypted reasoning
        - **Server-side tools**: xAI web_search, x_search, code_interpreter
        - **Cache affinity**: xAI conversation_id for prompt cache hits
        - **Reasoning continuity**: pass encrypted blocks between turns
        - **Structured output**: JSON schema enforcement via response_format

        For simple text generation, prefer ``llm_generate`` — it's simpler and
        returns a normalized ``{content, model, usage, provider}`` shape.

        Args:
            messages: Conversation messages (list of ``{role, content}`` dicts).
            model: Routed model identifier (e.g. ``anthropic/claude-sonnet-4``,
                ``xai/grok-3-mini``).
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature. None = provider default.
            top_p: Nucleus sampling. None = provider default.
            stop_sequences: Stop strings. None = no custom stops.
            seed: Random seed (xAI/OpenAI only). None = non-deterministic.
            stream: Reserved for future streaming support.
            response_format: JSON schema enforcement (xAI/OpenAI).
            thinking: Reasoning config. Anthropic: ``{"budget_tokens": 8192}``.
                xAI: ``{"effort": "high", "include_encrypted": true}``.
            tools: Function calling and/or server-side tools.
            tool_choice: Tool selection strategy.
            inject_mcp: Inject remote MCP server as tool (default True).
            provider_options: Per-provider escape hatch
                (e.g. ``{"xai": {"deferred": true}}``).
            conversation_id: Cache affinity ID. xAI: ``x-grok-conv-id`` header.
            reasoning_trace: Encrypted reasoning blocks from prior response
                to chain multi-turn reasoning.
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
        )

        if routing == "openrouter":
            record("mcp.frontier.generate.error", error="openrouter_not_implemented")
            return {"error": "OpenRouter routing not yet implemented"}

        if stream:
            record("mcp.frontier.generate.error", error="streaming_not_implemented")
            return {"error": "Streaming not yet implemented for frontier_generate"}

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
        req = FrontierRequest(
            messages=messages,
            model=api_model,
            max_tokens=max_tokens,
            system=system,
            inject_mcp=parsed.is_mcp or inject_mcp,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
            seed=seed,
            stream=stream,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            conversation_id=conversation_id,
            reasoning_trace=reasoning_trace,
            provider_options=provider_options,
        )
        mcp_cfg = mcp_config_from_env()
        url, headers, json_body = adapter.build_frontier_request(
            req, mcp_cfg if mcp_cfg else None
        )

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(url, headers=headers, json=json_body)
        except httpx.TimeoutException:
            record(
                "mcp.frontier.generate.error", error="timeout", provider=provider_key
            )
            return {"error": "Upstream timeout"}
        except httpx.RequestError as exc:
            logger.error("Frontier upstream request failed: %s", exc)
            record(
                "mcp.frontier.generate.error", error="connection", provider=provider_key
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
            has_server_tool_calls=result.get("server_tool_calls") is not None,
            input_tokens=result.get("usage", {}).get("input_tokens", 0),
            output_tokens=result.get("usage", {}).get("output_tokens", 0),
        )
        logger.info(
            "frontier_generate completed: %.3fs, provider=%s model=%s",
            duration,
            result.get("provider", provider_key),
            model,
        )
        return result
