"""Anthropic Messages API — private helpers, output-format and tool builders.

Module-private helpers: model-family detection, list deduplication, output-format
shaping, thinking-config normalization, max_tokens invariant enforcement, and
tool-entry construction (function tools + Anthropic server tools).
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

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
