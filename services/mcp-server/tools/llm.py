"""LLM generation tool — model-routed API call with credential injection.

Routes LLM generation through model routing so sandbox clients (e.g. Cortex
Workbench on claude.ai) get privacy-by-default via ``anthropic/...``,
with optional ``xai/...`` and ``openai/...``. The API key never
leaves the server.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
from llm_adapters import (
    LLMRequest,
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

_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


def _call_anthropic(
    payload: dict[str, Any], *, requested_model: str | None = None
) -> dict[str, Any]:
    """Compatibility wrapper for Anthropic-only MCP tools.

    Older OCR/finance helpers still build native Anthropic Messages payloads and
    expect an Anthropic-shaped JSON response. Keep that contract while the rest
    of the MCP server migrates to model-routed adapters.
    """
    adapter = resolve_llm_adapter("anthropic")
    if adapter is None:
        logger.error("Anthropic API key missing — Anthropic-only MCP tool unavailable")
        return {"error": "Anthropic API key not configured"}

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return {"error": "Invalid Anthropic payload: messages must be a list"}

    max_tokens = payload.get("max_tokens", 4096)
    if not isinstance(max_tokens, int) or max_tokens < 1:
        max_tokens = 4096

    system = payload.get("system", "")
    if not isinstance(system, str):
        system = str(system)

    model = requested_model or str(payload.get("model") or "")
    if not model:
        return {"error": "Anthropic payload missing model"}

    req = LLMRequest(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        system=system,
        inject_mcp=False,
    )
    url, headers, json_body = adapter.build_request(req, None)

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=json_body)
    except httpx.TimeoutException:
        return {"error": "Upstream timeout"}
    except httpx.RequestError as exc:
        logger.error("Anthropic upstream request failed: %s", exc)
        return {"error": "Upstream connection failed"}

    if resp.status_code >= 400:
        logger.warning(
            "Anthropic upstream returned %d for model=%s",
            resp.status_code,
            model,
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
    return raw


def register_llm_tools(mcp: FastMCP) -> None:
    """Register the llm_generate tool on the MCP server instance."""

    @mcp.tool()
    def llm_generate(
        messages: list[dict[str, Any]],
        system: str = "",
        model: str = "anthropic/claude-sonnet-4",
        max_tokens: int = 4096,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: list[str] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Generate text via LLM proxy with model routing.

        Supports routed model strings for provider selection:
        - ``anthropic/claude-sonnet-4`` — Anthropic Messages API (default, no remote MCP)
        - ``anthropic/claude-sonnet-4-mcp`` — same model with remote MCP (requires
          ``MCP_SERVER_URL`` / token on server)
        - ``anthropic/claude-opus-4`` — Anthropic (higher capability)
        - ``xai/grok-4-1-fast-reasoning`` — xAI Responses API; add ``-mcp`` for remote MCP
        - ``xai/grok-4.20-reasoning`` — xAI Responses API
        - ``openai/gpt-5`` — OpenAI Responses API; add ``-mcp`` for remote MCP
        - ``openrouter/...`` — not implemented (returns error)

        Args:
            messages: Conversation messages (list of ``{role, content}`` dicts).
            system: Optional system prompt (plain string).
            model: Routed model identifier (default: anthropic/claude-sonnet-4).
            max_tokens: Maximum tokens to generate (maps to ``max_tokens`` /
                ``max_output_tokens`` per provider).
            temperature: Sampling temperature (0.0–1.0+ depending on provider).
                None = use provider default.
            top_p: Nucleus sampling probability mass. None = use provider default.
            stop_sequences: List of stop strings (``stop_sequences`` for Anthropic,
                ``stop`` for xAI/OpenAI — adapter maps automatically).
                None = no custom stop sequences.
            seed: Random seed for reproducible outputs (xAI / OpenAI only;
                silently ignored for Anthropic). None = non-deterministic.

        Returns:
            ``{"content": str, "model": str, "usage": {input_tokens, output_tokens},
            "provider": str}``, or ``{"error": "..."}`` on failure.
        """
        parsed = ModelId.parse(model)
        routing = parsed.routing_layer
        provider_key = effective_provider_for_model(parsed.provider)

        t0 = monotonic_now()
        record(
            "mcp.llm.generate.called",
            model=model,
            routing=routing,
            provider=provider_key,
        )

        if routing == "openrouter":
            record(
                "mcp.llm.generate.error",
                error="openrouter_not_implemented",
                provider=provider_key,
            )
            return {"error": "OpenRouter routing not yet implemented"}

        adapter = resolve_llm_adapter(parsed.provider)
        if adapter is None:
            logger.error(
                "No API key for LLM provider %s — llm_generate unavailable",
                provider_key,
            )
            record(
                "mcp.llm.generate.error",
                error="missing_api_key",
                provider=provider_key,
            )
            return {
                "error": f"LLM generation not configured (missing API key for provider {provider_key})",
            }

        api_model = parsed.api_model_id
        req = LLMRequest(
            messages=messages,
            model=api_model,
            max_tokens=max_tokens,
            system=system,
            inject_mcp=parsed.is_mcp,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
            seed=seed,
        )
        mcp_cfg = mcp_config_from_env()
        url, headers, json_body = adapter.build_request(
            req, mcp_cfg if mcp_cfg else None
        )

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(url, headers=headers, json=json_body)
        except httpx.TimeoutException:
            return {"error": "Upstream timeout"}
        except httpx.RequestError as exc:
            logger.error("LLM upstream request failed: %s", exc)
            return {"error": "Upstream connection failed"}

        if resp.status_code >= 400:
            logger.warning(
                "LLM upstream returned %d for model=%s provider=%s",
                resp.status_code,
                model,
                provider_key,
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
        content_text = adapter.extract_text(raw)
        usage = adapter.extract_usage(raw)
        if "content" in raw and isinstance(raw.get("content"), list):
            block_count = len(raw["content"])
        else:
            out = raw.get("output")
            block_count = len(out) if isinstance(out, list) else 0

        record(
            "mcp.llm.generate.completed",
            duration_s=round(duration, 3),
            content_blocks=block_count,
            model=raw.get("model", api_model),
            routing=routing,
            provider=adapter.provider_label,
        )
        logger.info(
            "llm_generate completed: %.3fs, provider=%s model=%s",
            duration,
            adapter.provider_label,
            model,
        )
        return {
            "content": content_text,
            "model": str(raw.get("model", api_model)),
            "usage": usage,
            "provider": adapter.provider_label,
        }
