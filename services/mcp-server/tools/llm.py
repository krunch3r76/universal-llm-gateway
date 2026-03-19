"""LLM generation tool — model-routed API call with credential injection.

Routes LLM generation through model routing so sandbox clients (e.g. Cortex
Workbench on claude.ai) get privacy-by-default via ``native/anthropic/...``
with the option to switch to ``openrouter/...`` for cost savings.  The API
key never leaves the server.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import monotonic_now, record
from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_ANTHROPIC_API_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
_ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


def _get_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip() or None


def _call_anthropic(payload: dict[str, Any], *, requested_model: str) -> dict[str, Any]:
    """Send payload to Anthropic Messages API with server-side credentials."""
    api_key = _get_api_key()
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — llm_generate unavailable")
        return {"error": "LLM generation not configured (missing API key)"}

    headers = {
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                f"{_ANTHROPIC_API_URL}/v1/messages",
                headers=headers,
                json=payload,
            )
    except httpx.TimeoutException:
        return {"error": "Upstream timeout"}
    except httpx.RequestError as exc:
        logger.error("Anthropic API request failed: %s", exc)
        return {"error": "Upstream connection failed"}

    if resp.status_code >= 400:
        logger.warning(
            "Anthropic API returned %d for model=%s",
            resp.status_code,
            requested_model,
        )
        return {
            "error": f"Upstream error ({resp.status_code})",
            "detail": resp.text[:500],
        }

    try:
        return resp.json()
    except json.JSONDecodeError:
        return {"error": "Upstream returned invalid JSON"}


def register_llm_tools(mcp: FastMCP) -> None:
    """Register the llm_generate tool on the MCP server instance."""

    @mcp.tool()
    def llm_generate(
        messages: list[dict[str, Any]],
        system: str = "",
        model: str = "native/anthropic/claude-sonnet-4",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Generate text via LLM proxy with model routing.

        Supports routed model strings for provider selection:
        - ``native/anthropic/claude-sonnet-4`` — direct to Anthropic (privacy, default)
        - ``openrouter/anthropic/claude-3.5-sonnet`` — via OpenRouter (cost savings)
        - ``native/anthropic/claude-opus-4`` — direct to Anthropic (higher capability)

        Bare model names (e.g. ``claude-sonnet-4-20250514``) route to native Anthropic.

        Args:
            messages: Conversation messages in Anthropic format
                      (list of {role, content} dicts).
            system: Optional system prompt.
            model: Routed model identifier (default: native/anthropic/claude-sonnet-4).
            max_tokens: Maximum tokens to generate (default: 4096).

        Returns:
            Full Anthropic Messages API response, or {"error": "..."} on failure.
        """
        parsed = ModelId.parse(model)
        routing = parsed.routing_layer or "native"

        t0 = monotonic_now()
        record("mcp.llm.generate.called", model=model, routing=routing)

        if routing == "openrouter":
            record("mcp.llm.generate.error", error="openrouter_not_implemented")
            return {"error": "OpenRouter routing not yet implemented"}

        api_model = parsed.api_model_id
        payload: dict[str, Any] = {
            "model": api_model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system

        result = _call_anthropic(payload, requested_model=model)
        duration = monotonic_now() - t0

        if "error" in result:
            record(
                "mcp.llm.generate.error",
                duration_s=round(duration, 3),
                error=result["error"],
                model=model,
            )
            return result

        content_blocks = len(result.get("content", []))
        record(
            "mcp.llm.generate.completed",
            duration_s=round(duration, 3),
            content_blocks=content_blocks,
            model=result.get("model", api_model),
            routing=routing,
        )
        logger.info(
            "llm_generate completed: %.3fs, %d blocks, model=%s",
            duration,
            content_blocks,
            model,
        )
        return result
