"""Provider adapters for frontier LLM calls — types, protocols, factory.

Adapter implementations live in ``llm_adapters.anthropic``,
``llm_adapters.responses``, and ``llm_adapters.google``. This module re-exports
them from the ``llm_adapters`` package so existing importers (``from
llm_adapters import AnthropicAdapter``) keep working.

MCP Connector pattern (``mcp_servers`` in body) is NOT injected by any adapter.
API calls use client-side tool resolution.  The Connector field only appears if
the caller explicitly passes it via ``provider_options`` (OAuth/web-only).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from llm_adapters.anthropic import AnthropicAdapter
from llm_adapters.responses import ResponsesAPIAdapter

logger = logging.getLogger(__name__)

__all__ = [
    "AnthropicAdapter",
    "ResponsesAPIAdapter",
    "LLMRequest",
    "FrontierRequest",
    "LLMAdapter",
    "FrontierAdapter",
    "flatten_anthropic_system",
    "body_to_llm_request",
    "resolve_llm_adapter",
    "effective_provider_for_model",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """Normalized request for adapter build_request (tool + HTTP proxy paths)."""

    messages: list[dict[str, Any]]
    model: str
    max_tokens: int | None = None
    system: str = ""
    temperature: float | None = None
    top_p: float | None = None
    stop_sequences: list[str] | None = None
    seed: int | None = None


@dataclass(frozen=True, slots=True)
class FrontierRequest:
    """Full-fidelity request preserving vendor-native features (thinking, tools, cache affinity)."""

    messages: list[dict[str, Any]]
    model: str
    max_tokens: int | None = None
    system: str = ""
    temperature: float | None = None
    top_p: float | None = None
    stop_sequences: list[str] | None = None
    seed: int | None = None
    stream: bool = False
    thinking: dict[str, Any] | None = None
    effort: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    boot: str = "none"
    boot_ref: str | None = None
    conversation_id: str | None = None
    reasoning_trace: list[dict[str, Any]] | None = None
    provider_options: dict[str, Any] | None = None
    mcp_tool_loop: bool = False
    remote_mcp: bool = False
    """When True, adapter injects a provider-native MCP-server descriptor
    before POST. Anthropic: body.mcp_servers; OpenAI/xAI (Responses API):
    body.tools[{type:'mcp', ...}]; Google: NotImplementedError. Env
    MCP_PUBLIC_URL + MCP_AUTH_TOKEN must be set or build_frontier_request
    raises. Client-side tool loop MUST be disabled when True — enforced
    by runtime check (mcp_tool_loop=False)."""


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class FrontierAdapter(Protocol):
    """Build vendor-native requests and parse structured responses preserving full fidelity."""

    def build_frontier_request(
        self,
        req: FrontierRequest,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return (full_url, headers, json_body) for a frontier request."""
        ...

    def parse_frontier_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Return structured response: content, thinking, tool_calls, usage, response_id."""
        ...

    def append_tool_round(
        self,
        body: dict[str, Any],
        raw_response: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> None:
        """Mutate body in place to append assistant tool calls + user tool results for next turn.

        ``tool_results`` entries: ``{"id": str, "name": str, "content": str}``.
        """
        ...


class LLMAdapter(Protocol):
    """Build upstream HTTP request and parse provider-native JSON responses."""

    @property
    def provider_label(self) -> str:
        """Short name for telemetry (e.g. anthropic, xai)."""
        ...

    def build_request(
        self,
        req: LLMRequest,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return (full_url, headers, json_body)."""

    def extract_text(self, response_data: dict[str, Any]) -> str:
        """Best-effort assistant text for normalized tool responses."""

    def extract_usage(self, response_data: dict[str, Any]) -> dict[str, int]:
        """input_tokens / output_tokens for telemetry."""


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def flatten_anthropic_system(system: Any) -> str:
    """Turn Anthropic system (str or content blocks) into a plain string."""
    if system is None or system == "":
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(system)


def body_to_llm_request(
    body: dict[str, Any],
    api_model_id: str,
) -> LLMRequest:
    """Map Anthropic-shaped proxy body to LLMRequest for Responses adapters."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        messages = []
    system_raw = body.get("system", "")
    system = flatten_anthropic_system(system_raw)
    max_tok_raw = body.get("max_tokens")
    max_tok: int | None = max_tok_raw if isinstance(max_tok_raw, int) and max_tok_raw >= 1 else None
    temperature = body.get("temperature")
    if isinstance(temperature, bool) or not isinstance(temperature, int | float):
        temperature = None
    else:
        temperature = float(temperature)
    top_p = body.get("top_p")
    if isinstance(top_p, bool) or not isinstance(top_p, int | float):
        top_p = None
    else:
        top_p = float(top_p)
    stop_sequences = body.get("stop_sequences")
    if not (
        isinstance(stop_sequences, list)
        and all(isinstance(item, str) for item in stop_sequences)
    ):
        stop_sequences = None
    seed = body.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        seed = None
    return LLMRequest(
        messages=messages,
        model=api_model_id,
        max_tokens=max_tok,
        system=system,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        seed=seed,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def resolve_llm_adapter(provider: str | None) -> LLMAdapter | None:
    """Return a configured adapter for cloud provider, or None if API key missing."""
    p = (provider or "anthropic").strip().lower()
    match p:
        case "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                return None
            base = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
            return AnthropicAdapter(api_key=key, base_url=base)
        case "xai":
            key = os.environ.get("XAI_API_KEY", "").strip()
            if not key:
                return None
            base = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
            return ResponsesAPIAdapter(api_key=key, base_url=base, vendor="xai")
        case "openai" | "chatgpt":
            key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not key:
                return None
            base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            return ResponsesAPIAdapter(api_key=key, base_url=base, vendor=p)
        case "google":
            from llm_adapters.google import GoogleAdapter as GeminiAdapter

            key = os.environ.get("GOOGLE_API_KEY", "").strip()
            if not key:
                return None
            base = os.getenv(
                "GOOGLE_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta",
            )
            return GeminiAdapter(api_key=key, base_url=base)
        case _:
            return None


def effective_provider_for_model(parsed_provider: str | None) -> str:
    """Default cloud provider when ModelId has no provider (legacy bare IDs)."""
    return (parsed_provider or "anthropic").strip().lower()
