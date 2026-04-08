"""LLM generation tool — universal chat via Stargate.

Routes all generation through Stargate ``/v1/chat/completions`` so every
model known to the system (local, pipeline, cloud) is reachable with a
single tool call.  Stargate handles model resolution, pipeline dispatch,
cloud proxy passthrough, and load-on-demand.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import httpx
from llm_adapters import (
    LLMRequest,
    resolve_llm_adapter,
)
from mcp_events import monotonic_now, record
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


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
        """Generate text via Stargate — works with any model, pipeline, or cloud provider.

        All requests go through ``/v1/chat/completions`` on Stargate.
        Stargate resolves the model: local inference, pipeline dispatch,
        or cloud proxy passthrough. Use ``claude_generate`` / ``grok_generate``
        only when you need provider-native features (thinking, MCP, structured output).

        **Model ID format** (CRITICAL — wrong format → 404):

        - ``anthropic/claude-sonnet-4`` — direct Anthropic API
        - ``xai/grok-4.20-0309-reasoning`` — direct xAI API
        - ``openai/gpt-5.4`` — direct OpenAI API
        - ``openrouter/google/gemini-2.5-flash`` — OpenRouter (note triple-segment ID)
        - ``openrouter/qwen/qwen3-32b`` — OpenRouter
        - ``hermes-3-llama-3-1-70b-...-16384-hybrid`` — local model (no slash)

        Google, Qwen, Meta, Mistral, and all other providers without a direct
        API integration MUST use the ``openrouter/`` prefix. Bare
        ``google/gemini-*`` will 404 — there is no direct Google provider.

        Use ``model_status()`` to discover available model IDs.

        Args:
            messages: Conversation messages (list of ``{role, content}`` dicts).
            system: Optional system prompt prepended as a ``{"role": "system"}``
                message.  Ignored when empty.
            model: Model ID in the format described above.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature. None = model/provider default.
            top_p: Nucleus sampling probability mass. None = default.
            stop_sequences: Stop strings. None = none.
            seed: Random seed. None = non-deterministic.

        Returns:
            ``{"role": "assistant", "content": str, "finish_reason": str | None,
            "model": str, "usage": {prompt_tokens, completion_tokens}}``,
            or ``{"error": "..."}`` on failure.

            Append the response directly to your ``messages`` array for
            multi-turn conversations::

                result = llm_generate(messages=history, ...)
                history.append({"role": result["role"], "content": result["content"]})
                history.append({"role": "user", "content": next_turn})
        """
        t0 = monotonic_now()
        record("mcp.llm.generate.called", model=model)

        wire_messages: list[dict[str, Any]] = []
        if system:
            wire_messages.append({"role": "system", "content": system})
        wire_messages.extend(messages)

        body: dict[str, Any] = {
            "model": model,
            "messages": wire_messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if top_p is not None:
            body["top_p"] = top_p
        if stop_sequences:
            body["stop"] = stop_sequences
        if seed is not None:
            body["seed"] = seed

        url = f"{_STARGATE_URL}/v1/chat/completions"
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(url, json=body)
        except httpx.TimeoutException:
            record("mcp.llm.generate.error", error="timeout", model=model)
            return {"error": "Stargate timeout"}
        except httpx.RequestError as exc:
            logger.error("Stargate request failed: %s", exc)
            record("mcp.llm.generate.error", error="connection", model=model)
            return {"error": "Stargate connection failed"}

        if resp.status_code >= 400:
            logger.warning("Stargate returned %d for model=%s", resp.status_code, model)
            record(
                "mcp.llm.generate.error",
                error=f"http_{resp.status_code}",
                model=model,
            )
            return {
                "error": f"Stargate error ({resp.status_code})",
                "detail": resp.text[:500],
            }

        try:
            data = resp.json()
        except json.JSONDecodeError:
            return {"error": "Stargate returned invalid JSON"}
        if not isinstance(data, dict):
            return {"error": "Stargate returned non-object JSON"}

        duration = monotonic_now() - t0

        choices = data.get("choices") or []
        choice = choices[0] if choices else {}
        msg = choice.get("message") or {}
        usage_raw = data.get("usage") or {}

        content = msg.get("content", "")
        finish_reason = choice.get("finish_reason")
        returned_model = data.get("model", model)

        record(
            "mcp.llm.generate.completed",
            duration_s=round(duration, 3),
            model=returned_model,
        )
        logger.info("llm_generate completed: %.3fs, model=%s", duration, returned_model)
        return {
            "role": msg.get("role", "assistant"),
            "content": content,
            "finish_reason": finish_reason,
            "model": str(returned_model),
            "usage": {
                "prompt_tokens": usage_raw.get("prompt_tokens", 0),
                "completion_tokens": usage_raw.get("completion_tokens", 0),
            },
        }
