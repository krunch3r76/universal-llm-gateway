"""Shared MCP / stream tool-result unwrapping for cursor-sdk harvest (item 18)."""

from __future__ import annotations

import json
from collections.abc import Mapping


def _json_loads_text(text: str) -> object | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _text_parts_from_content_blocks(content: object, *, sdk_double_wrap: bool) -> list[str]:
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        text: object | None
        if sdk_double_wrap:
            text_obj = block.get("text")
            text = text_obj.get("text") if isinstance(text_obj, Mapping) else None
        else:
            if str(block.get("type") or "") != "text":
                continue
            text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return parts


def _payload_from_content_blocks(content: object, *, sdk_double_wrap: bool) -> object | None:
    parts = _text_parts_from_content_blocks(content, sdk_double_wrap=sdk_double_wrap)
    if not parts:
        return None
    joined = "\n".join(parts)
    parsed = _json_loads_text(joined)
    return parsed if parsed is not None else joined


def _payload_from_mcp_content_blocks(content: object) -> object | None:
    return _payload_from_content_blocks(content, sdk_double_wrap=False)


def unwrap_tool_result(result: object) -> object | None:
    """Normalize cursor-sdk / MCP tool results to a harvestable payload."""
    if result is None:
        return None
    if isinstance(result, str):
        parsed = _json_loads_text(result)
        return parsed if parsed is not None else result
    if not isinstance(result, Mapping):
        return result
    if result.get("status") == "error":
        return None
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        return structured
    value = result.get("value")
    if value is not None:
        if isinstance(value, str):
            parsed = _json_loads_text(value)
            return parsed if parsed is not None else value
        if isinstance(value, Mapping):
            from_sdk = _payload_from_content_blocks(value.get("content"), sdk_double_wrap=True)
            if from_sdk is not None:
                return from_sdk
            from_mcp = _payload_from_mcp_content_blocks(value.get("content"))
            if from_mcp is not None:
                return from_mcp
        return value
    from_content = _payload_from_mcp_content_blocks(result.get("content"))
    if from_content is not None:
        return from_content
    nested = result.get("result")
    if isinstance(nested, Mapping):
        return unwrap_tool_result(nested)
    return result


def assertion_id_from_payload(payload: object) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    item = payload.get("item")
    if isinstance(item, Mapping):
        id_val = item.get("id")
        if isinstance(id_val, int) and not isinstance(id_val, bool):
            return id_val
    for key in ("id", "assertion_id"):
        id_val = payload.get(key)
        if isinstance(id_val, int) and not isinstance(id_val, bool):
            return id_val
        if isinstance(id_val, str) and id_val.isdigit():
            return int(id_val)
    return None


__all__ = ["assertion_id_from_payload", "unwrap_tool_result"]
