"""Computed lane binding from Cursor JSONL agent-bus touches (AMEND-R D1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_WRITE_OPS = frozenset(
    {
        "send",
        "post",
        "reply",
        "update",
        "update_thread",
        "close",
        "lane_bind",
        "add_tags",
        "remove_tags",
        "delete_turn",
        "delete_thread",
    }
)
_BUS_TOOLS = frozenset({"agent_bus", "agent_bus_read"})


@dataclass
class LaneTouch:
    """Read/write touch counts for one continuity lane."""

    writes: int = 0
    reads: int = 0
    last_write_index: int = -1


def _parse_inner_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _lane_from_arguments(args: dict[str, Any]) -> str | None:
    for key in ("thread", "thread_id"):
        value = args.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _bus_op_from_arguments(tool_name: str, args: dict[str, Any]) -> str | None:
    if tool_name == "agent_bus_read":
        return None
    for key in ("tool", "op"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _record_touch_from_block(
    block: dict[str, Any],
    *,
    line_index: int,
    touches: dict[str, LaneTouch],
) -> None:
    name = str(block.get("name") or "").strip()
    payload = block.get("input")
    if not isinstance(payload, dict):
        payload = block.get("args") if isinstance(block.get("args"), dict) else {}
    if not isinstance(payload, dict):
        return

    tool_name = str(payload.get("toolName") or payload.get("tool_name") or name).strip()
    if tool_name == "mcp":
        tool_name = str(payload.get("toolName") or payload.get("tool_name") or "").strip()
    if tool_name not in _BUS_TOOLS:
        return

    inner = _parse_inner_arguments(payload.get("arguments"))
    nested = payload.get("args")
    if isinstance(nested, dict):
        inner = {**nested, **inner}
        if "arguments" in nested:
            inner = {**inner, **_parse_inner_arguments(nested.get("arguments"))}

    lane = _lane_from_arguments(inner)
    if lane is None:
        lane = _lane_from_arguments(payload)
    if lane is None:
        return

    touch = touches.setdefault(lane, LaneTouch())
    op = _bus_op_from_arguments(tool_name, inner)
    if tool_name == "agent_bus" and op in _WRITE_OPS:
        touch.writes += 1
        touch.last_write_index = line_index
    else:
        touch.reads += 1


def _extract_touches_from_record(
    record: dict[str, Any],
    *,
    line_index: int,
    touches: dict[str, LaneTouch],
) -> None:
    message = record.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    _record_touch_from_block(
                        block, line_index=line_index, touches=touches
                    )
    if record.get("type") == "toolCall" and isinstance(record.get("message"), dict):
        _record_touch_from_block(
            record["message"], line_index=line_index, touches=touches
        )


def lane_touches(jsonl_path: Path) -> dict[str, LaneTouch]:
    """Return per-lane read/write touch tallies from a Cursor JSONL transcript."""
    touches: dict[str, LaneTouch] = {}
    if not jsonl_path.is_file():
        return touches
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line_index, line in enumerate(fh):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                _extract_touches_from_record(
                    record, line_index=line_index, touches=touches
                )
    return touches


def dominant_lane(touches: dict[str, LaneTouch]) -> str | None:
    """Argmax write touches; tie-break on latest write index."""
    candidates = [(lane, touch) for lane, touch in touches.items() if touch.writes > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[1].writes, item[1].last_write_index))[0]


def binding_for(
    lane: str,
    touches: dict[str, LaneTouch],
    *,
    explicit_uuids: set[str],
    conversation_uuid: str,
) -> tuple[str, str | None]:
    """Classify how a JSONL window binds to *lane* for discover/seal."""
    lane_key = str(lane).strip()
    dom = dominant_lane(touches)
    if conversation_uuid in explicit_uuids:
        return "explicit_cp", dom or lane_key

    total_touches = sum(t.reads + t.writes for t in touches.values())
    if total_touches == 0:
        return "no_touch", dom

    lane_touch = touches.get(lane_key)
    if dom is None:
        if lane_touch and lane_touch.reads > 0 and lane_touch.writes == 0:
            return "read_only", dom
        return "no_touch", dom

    if dom == lane_key and lane_touch and lane_touch.writes >= 1:
        return "dominant_write", dom

    if lane_touch and lane_touch.reads > 0 and lane_touch.writes == 0:
        return "read_only", dom

    return "foreign_dominant", dom


__all__ = [
    "LaneTouch",
    "binding_for",
    "dominant_lane",
    "lane_touches",
]
