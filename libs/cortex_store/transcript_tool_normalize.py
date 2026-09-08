"""Normalize Cursor JSONL tool_use blocks into logical tool calls.

Cursor exports primarily use ``CallDynamicTool`` with ``namespace``,
``toolName``, and JSON ``arguments``. Facts and lane-touch modules consume
the normalized stream so both agree on bus tallies and send detection.
"""

from __future__ import annotations

import json
from typing import Any, Iterator


def _parse_json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def iter_normalized_tool_uses(
    records: list[dict[str, Any]],
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(logical_tool, input_dict)`` for each tool_use in *records*."""
    for record in records:
        if record.get("role") != "assistant":
            continue
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "")
            raw_input = block.get("input")
            if not isinstance(raw_input, dict):
                continue
            if name == "CallDynamicTool":
                namespace = str(raw_input.get("namespace") or "")
                tool_name = str(raw_input.get("toolName") or "")
                args = _parse_json_obj(raw_input.get("arguments"))
                if namespace == "cursor-app-control":
                    yield tool_name, args
                elif namespace == "user-vortex-code":
                    if tool_name == "agent_bus":
                        outer = raw_input.get("arguments")
                        if isinstance(outer, dict):
                            args = _parse_json_obj(outer.get("arguments"))
                            bus_tool = str(
                                outer.get("tool")
                                or args.get("tool")
                                or args.get("op")
                                or ""
                            ).lower()
                        else:
                            args = _parse_json_obj(outer)
                            bus_tool = str(
                                raw_input.get("tool")
                                or args.get("tool")
                                or args.get("op")
                                or ""
                            ).lower()
                        merged = {**args, "op": bus_tool or args.get("op", "send")}
                        yield "agent_bus", merged
                    else:
                        yield tool_name, args
                else:
                    yield tool_name or name, args
            else:
                yield name.lower(), raw_input


__all__ = ["iter_normalized_tool_uses"]
