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


def _normalize_image_url_block(block: dict[str, Any]) -> dict[str, Any]:
    """Map Chat Completions ``image_url`` block to Responses ``input_image``."""
    raw = block.get("image_url")
    out: dict[str, Any] = {"type": "input_image"}
    if isinstance(raw, str):
        if not raw:
            msg = "image_url block has empty string image_url"
            raise ValueError(msg)
        out["image_url"] = raw
    elif isinstance(raw, dict):
        url = raw.get("url")
        if not isinstance(url, str) or not url:
            msg = "image_url block requires image_url.url as a non-empty string"
            raise ValueError(msg)
        out["image_url"] = url
        if "detail" in raw:
            out["detail"] = raw["detail"]
    else:
        msg = "image_url block requires image_url as a string or object with url"
        raise ValueError(msg)
    if "detail" in block and "detail" not in out:
        out["detail"] = block["detail"]
    return out


def _normalize_input_content(content: Any) -> Any:
    """Normalize message content for Responses API input messages.

    The Responses API uses ``"type": "input_text"`` for input content blocks,
    not ``"type": "text"`` (which is the output block type). Callers that build
    messages with Chat-Completions-style ``{"type": "text", "text": "..."}``
    blocks must be translated before sending to the Responses API.

    Chat-Completions-style ``{"type": "image_url", ...}`` becomes
    ``{"type": "input_image", "image_url": "<url>", ...}``. Already-normalized
    ``input_image``, ``input_file``, and ``computer_screenshot`` blocks pass
    through unchanged.

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
        btype = block.get("type")
        if btype == "text":
            normalized.append({"type": "input_text", "text": block.get("text", "")})
        elif btype == "image_url":
            normalized.append(_normalize_image_url_block(block))
        else:
            normalized.append(block)
    return normalized
