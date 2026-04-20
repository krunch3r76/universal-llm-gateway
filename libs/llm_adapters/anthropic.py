"""Anthropic Messages API adapter — request building and response parsing.

MCP tool calling uses client-side tool resolution (tool_use blocks executed locally
by the caller's tool loop).  The ``mcp_servers`` Connector field is never injected
automatically; it only appears if the caller explicitly passes it via
``provider_options`` (OAuth/web-only pattern).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from provider_model_limits import (
    anthropic_max_output_tokens,
    clamp_anthropic_max_tokens,
)

from llm_adapters._mcp_entry import (
    anthropic_mcp_server_entry,
    resolve_mcp_env,
)

if TYPE_CHECKING:
    from llm_adapters import FrontierRequest, LLMRequest

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_BETA_MCP = "mcp-client-2025-11-20"
_ANTHROPIC_BETA_INTERLEAVED = "interleaved-thinking-2025-05-14"
_ANTHROPIC_BETA_CONTEXT = "context-management-2025-06-27"
_ANTHROPIC_BETA_COMPACT = "compact-2026-01-12"
_ANTHROPIC_BETA_FAST = "fast-mode-2026-02-01"
_ANTHROPIC_SERVER_TOOL_VERSION_MAP = {
    "web_search": "web_search_20260209",
    "web_fetch": "web_fetch_20250910",
    "code_execution": "code_execution_20260120",
}


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        clean = value.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


def _is_claude4_model(model: str) -> bool:
    return any(
        marker in model for marker in ("claude-sonnet-4", "claude-opus-4", "claude-4")
    )


def _build_anthropic_output_format(response_format: dict[str, Any]) -> dict[str, Any]:
    if response_format.get("type") != "json_schema":
        return dict(response_format)
    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return dict(response_format)
    output_format: dict[str, Any] = {"type": "json_schema"}
    schema = json_schema.get("schema")
    if schema is not None:
        output_format["schema"] = schema
    if "schema" not in output_format and response_format.get("schema") is not None:
        output_format["schema"] = response_format["schema"]
    return output_format


def _build_anthropic_thinking(thinking: dict[str, Any] | None) -> dict[str, Any] | None:
    if not thinking:
        return None
    thinking_type = thinking.get("type")
    thinking_mode = thinking.get("mode")
    display = thinking.get("display")

    if thinking_type == "adaptive" or thinking_mode == "adaptive":
        config: dict[str, Any] = {"type": "adaptive"}
        if display is not None:
            config["display"] = display
        return config

    budget_tokens = thinking.get("budget_tokens")
    if isinstance(budget_tokens, int) and budget_tokens > 0:
        config = {"type": "enabled", "budget_tokens": budget_tokens}
        if display is not None:
            config["display"] = display
        return config

    if (
        thinking_type == "disabled"
        or thinking_mode == "disabled"
        or thinking.get("enabled") is False
    ):
        return {"type": "disabled"}

    return None


def _resolve_anthropic_max_tokens(
    requested_max_tokens: int | None,
    thinking_config: dict[str, Any] | None,
    *,
    model: str,
) -> int | None:
    """Ensure Anthropic's max_tokens > thinking.budget_tokens invariant."""
    if (
        not isinstance(thinking_config, dict)
        or thinking_config.get("type") != "enabled"
    ):
        return requested_max_tokens

    budget_tokens = thinking_config.get("budget_tokens")
    if not isinstance(budget_tokens, int) or budget_tokens < 1:
        return requested_max_tokens

    if isinstance(requested_max_tokens, int) and requested_max_tokens > budget_tokens:
        return requested_max_tokens

    bumped_max_tokens = budget_tokens * 2
    logger.warning(
        "Auto-bumped Anthropic max_tokens from %s to %d for model=%s to satisfy "
        "max_tokens > thinking.budget_tokens",
        requested_max_tokens,
        bumped_max_tokens,
        model,
    )
    return bumped_max_tokens


def _build_anthropic_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
    tool_type = tool.get("type")
    if tool_type == "function":
        fn = tool.get("function", tool)
        return {
            "name": fn.get("name") or tool.get("name", ""),
            "description": fn.get("description") or tool.get("description", ""),
            "input_schema": fn.get("parameters") or tool.get("parameters", {}),
        }

    if isinstance(tool_type, str) and tool_type in _ANTHROPIC_SERVER_TOOL_VERSION_MAP:
        return {
            "type": _ANTHROPIC_SERVER_TOOL_VERSION_MAP[tool_type],
            "name": tool_type,
        }

    return None


class AnthropicAdapter:
    """Anthropic Messages API — x-api-key auth, client-side tool resolution."""

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base = (
            base_url or os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        ).rstrip("/")

    @property
    def provider_label(self) -> str:
        return "anthropic"

    def build_request(
        self,
        req: LLMRequest,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        resolved_max_tokens = clamp_anthropic_max_tokens(req.model, req.max_tokens)
        if resolved_max_tokens != req.max_tokens:
            logger.info(
                "Clamped Anthropic max_tokens from %d to %d for model=%s",
                req.max_tokens,
                resolved_max_tokens,
                req.model,
            )
        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": req.model,
            "max_tokens": resolved_max_tokens,
            "messages": req.messages,
        }
        if req.system:
            body["system"] = req.system
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop_sequences is not None:
            body["stop_sequences"] = req.stop_sequences
        url = f"{self._base}/v1/messages"
        return url, headers, body

    def build_frontier_request(
        self,
        req: FrontierRequest,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": req.model,
            "messages": list(req.messages),
        }
        if req.system:
            body["system"] = req.system
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop_sequences is not None:
            body["stop_sequences"] = req.stop_sequences

        thinking_config = _build_anthropic_thinking(req.thinking)
        if thinking_config is not None:
            body["thinking"] = thinking_config
        resolved_max_tokens = _resolve_anthropic_max_tokens(
            req.max_tokens, thinking_config, model=req.model
        )
        model_max = anthropic_max_output_tokens(req.model)
        if resolved_max_tokens is not None:
            body["max_tokens"] = clamp_anthropic_max_tokens(
                req.model, resolved_max_tokens
            )
            if body["max_tokens"] != resolved_max_tokens:
                logger.info(
                    "Clamped Anthropic max_tokens from %d to %d for model=%s",
                    resolved_max_tokens,
                    body["max_tokens"],
                    req.model,
                )
        else:
            body["max_tokens"] = model_max
            logger.info(
                "No max_tokens specified for model=%s; using model max %d",
                req.model,
                model_max,
            )

        if req.remote_mcp:
            if req.mcp_tool_loop:
                raise ValueError(
                    "remote_mcp=True is mutually exclusive with mcp_tool_loop=True"
                )
            # Anthropic's MCP descriptor lives in body["mcp_servers"], never in
            # req.tools. Any tool present alongside remote_mcp=True is a client-
            # side function that cannot fire (loop disabled) — fail loudly.
            if req.tools:
                raise ValueError(
                    "remote_mcp=True rejects any req.tools for Anthropic "
                    "(client-side tools cannot fire with the loop disabled)"
                )
            url, token = resolve_mcp_env()
            server_entry = anthropic_mcp_server_entry(url, token)
            existing_servers = body.get("mcp_servers") or []
            body["mcp_servers"] = [*existing_servers, server_entry]
            # Anthropic (2026-04) requires each defined mcp_servers entry to be
            # referenced by an `mcp_toolset` tool; otherwise the API 400s with
            # "MCP server '<name>' is defined but not referenced". The adapter
            # synthesizes this entry — caller-supplied req.tools is still
            # rejected above (strict contract intact).
            body["tools"] = [
                *(body.get("tools") or []),
                {"type": "mcp_toolset", "mcp_server_name": server_entry["name"]},
            ]
        else:
            tools_list: list[dict[str, Any]] = []
            if req.tools:
                for t in req.tools:
                    mapped_tool = _build_anthropic_tool(t)
                    if mapped_tool is not None:
                        tools_list.append(mapped_tool)
            if tools_list:
                body["tools"] = tools_list
        if req.tool_choice is not None:
            body["tool_choice"] = req.tool_choice

        output_config: dict[str, Any] = {}
        if req.response_format:
            output_config["format"] = _build_anthropic_output_format(
                req.response_format
            )
        if req.effort is not None and thinking_config is not None:
            output_config["effort"] = req.effort
        elif req.effort is not None:
            logger.info(
                "Stripping effort=%s for model=%s (no thinking config)",
                req.effort,
                req.model,
            )
        if output_config:
            body["output_config"] = output_config

        opts = (req.provider_options or {}).get("anthropic", {})
        for key, value in opts.items():
            if key == "betas":
                continue
            if key == "output_config" and isinstance(value, dict):
                merged = dict(value)
                merged.update(body.get("output_config", {}))
                body["output_config"] = merged
                continue
            if key not in body:
                body[key] = value

        auto_betas: list[str] = []
        if thinking_config is not None and _is_claude4_model(req.model):
            auto_betas.append(_ANTHROPIC_BETA_INTERLEAVED)

        context_management = body.get("context_management")
        if isinstance(context_management, dict):
            auto_betas.append(_ANTHROPIC_BETA_CONTEXT)
            edits = context_management.get("edits")
            if isinstance(edits, list) and any(
                isinstance(edit, dict)
                and str(edit.get("type", "")).startswith("compact")
                for edit in edits
            ):
                auto_betas.append(_ANTHROPIC_BETA_COMPACT)

        if opts.get("speed") == "fast":
            auto_betas.append(_ANTHROPIC_BETA_FAST)

        # mcp_servers beta — only fires if caller explicitly passed mcp_servers
        # via provider_options (OAuth/Connector escape hatch, not auto-injected)
        if body.get("mcp_servers"):
            auto_betas.append(_ANTHROPIC_BETA_MCP)

        explicit_betas = opts.get("betas", [])
        if isinstance(explicit_betas, list):
            beta_values = [*explicit_betas, *auto_betas]
        else:
            beta_values = auto_betas
        if beta_values:
            headers["anthropic-beta"] = ",".join(_dedupe_preserve_order(beta_values))

        url = f"{self._base}/v1/messages"
        return url, headers, body

    def parse_frontier_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        thinking_blocks: list[dict[str, Any]] = []
        thinking_tokens = 0
        tool_calls: list[dict[str, Any]] = []
        server_tool_calls: list[dict[str, Any]] = []

        for block in response_data.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                content_parts.append(str(block.get("text", "")))
            elif btype == "thinking":
                thinking_blocks.append(block)
                thinking_parts.append(str(block.get("thinking", "")))
            elif btype == "redacted_thinking":
                thinking_blocks.append(block)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input"),
                    }
                )
            elif btype in {
                "server_tool_use",
                "web_search_tool_result",
                "web_fetch_tool_result",
                "code_execution_tool_result",
            }:
                server_tool_calls.append(block)

        u = response_data.get("usage") or {}
        usage: dict[str, Any] = {
            "input_tokens": int(u.get("input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0),
            "reasoning_tokens": None,
            "cached_tokens": int(u.get("cache_read_input_tokens") or 0) or None,
        }

        thinking: dict[str, Any] | None = None
        if thinking_blocks:
            thinking_tokens = int(u.get("thinking_tokens") or 0)
            thinking = {
                "text": "".join(thinking_parts) or None,
                "encrypted_content": None,
                "tokens": thinking_tokens,
                "blocks": thinking_blocks,
            }
            usage["reasoning_tokens"] = thinking_tokens

        return {
            "content": "".join(content_parts),
            "model": str(response_data.get("model", "")),
            "provider": "anthropic",
            "usage": usage,
            "thinking": thinking,
            "tool_calls": tool_calls or None,
            "server_tool_calls": server_tool_calls or None,
            "response_id": response_data.get("id"),
            "raw": None,
        }

    def append_tool_round(
        self,
        body: dict[str, Any],
        raw_response: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> None:
        """Append assistant content + tool_result blocks for the next Anthropic turn."""
        body["messages"].append(
            {
                "role": "assistant",
                "content": raw_response.get("content", []),
            }
        )
        result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": tr["id"],
                "content": tr["content"],
            }
            for tr in tool_results
        ]
        body["messages"].append({"role": "user", "content": result_blocks})

    def extract_text(self, response_data: dict[str, Any]) -> str:
        parts: list[str] = []
        for block in response_data.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)

    def extract_usage(self, response_data: dict[str, Any]) -> dict[str, int]:
        u = response_data.get("usage") or {}
        return {
            "input_tokens": int(u.get("input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0),
        }
