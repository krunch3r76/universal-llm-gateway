"""Provider adapters for frontier LLM calls.

Moved from services/mcp-server/ so both MCP and Stargate pipeline handlers
can import the same adapter implementations. Adapters own the provider-native
request/response shape translation (build_frontier_request,
parse_frontier_response, append_tool_round).

Shared with ``libs/agent_seat/native_loop.py`` — the tool-loop driver.
"""

from __future__ import annotations

from llm_adapters.anthropic import AnthropicAdapter
from llm_adapters.core import (
    FrontierAdapter,
    FrontierRequest,
    LLMAdapter,
    LLMRequest,
    body_to_llm_request,
    effective_provider_for_model,
    flatten_anthropic_system,
    resolve_llm_adapter,
)
from llm_adapters.responses import ResponsesAPIAdapter

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('cloud_proxy', 'gateway', 'mcp', 'stargate')

# Google adapter loaded lazily via resolve_llm_adapter to avoid pulling the
# google-genai SDK into callers that don't need it.

__all__ = [
    "AnthropicAdapter",
    "FrontierAdapter",
    "FrontierRequest",
    "LLMAdapter",
    "LLMRequest",
    "ResponsesAPIAdapter",
    "body_to_llm_request",
    "effective_provider_for_model",
    "flatten_anthropic_system",
    "resolve_llm_adapter",
]
