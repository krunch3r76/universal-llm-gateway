"""Responses API input and tool schema normalization helpers."""

from __future__ import annotations

from typing import Any

from llm_adapters._tool_schema import sanitize_tool_parameters


def _normalize_tool_for_responses_api(tool: dict[str, Any]) -> dict[str, Any]:
    """Flatten Chat Completions function format to Responses API format.

    Chat Completions: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Responses API:    {"type": "function", "name": ..., "description": ..., "parameters": ...}

    Server tools (web_search, x_search, etc.) and already-flat tools pass through unchanged.
    """
    fn = tool.get("function")
    if not isinstance(fn, dict):
        return tool
    flat: dict[str, Any] = {"type": "function"}
    if "name" in fn:
        flat["name"] = fn["name"]
    if "description" in fn:
        flat["description"] = fn["description"]
    if "parameters" in fn:
        params = fn["parameters"]
        if isinstance(params, dict):
            flat["parameters"] = sanitize_tool_parameters(params)
        else:
            flat["parameters"] = params
    for k, v in fn.items():
        if k not in flat:
            flat[k] = v
    return flat


def _normalize_input_content(content: Any) -> Any:
    """Normalize message content for Responses API input messages.

    The Responses API uses ``"type": "input_text"`` for input content blocks,
    not ``"type": "text"`` (which is the output block type). Callers that build
    messages with Chat-Completions-style ``{"type": "text", "text": "..."}``
    blocks must be translated before sending to the Responses API.

    String content passes through unchanged.
    Single-text-block arrays are flattened to plain strings (simplest form).
    Multi-block arrays have text blocks translated to ``input_text`` type.
    """
    if not isinstance(content, list):
        return content
    text_only = all(
        isinstance(b, dict) and b.get("type") in {"text", "input_text"} for b in content
    )
    if text_only and len(content) == 1:
        return str(content[0].get("text", ""))
    normalized: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            normalized.append(block)
            continue
        if block.get("type") == "text":
            normalized.append({"type": "input_text", "text": block.get("text", "")})
        else:
            normalized.append(block)
    return normalized
