"""Lane touch tallies and binding from Cursor JSONL agent-bus tool_use inputs.

AMEND-R discover/seal/harvest use Path-based ``lane_touches`` with ``LaneTouch``
dataclass tallies. Transcript projection uses record-list helpers
(``lane_touches_from_records``, ``projection_binding_for``, …).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex_store.transcript_tool_normalize import iter_normalized_tool_uses

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

_PROJECTION_WRITE_OPS = frozenset({"send", "post", "reply"})
_PROJECTION_READ_OPS = frozenset(
    {"get", "fetch", "wait", "read", "list", "search", "thread_get"}
)

BusTouch = dict[str, int]
LaneTouches = dict[str, BusTouch]


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


def _projection_bus_thread_from_input(inp: dict[str, Any]) -> str | None:
    for key in ("thread", "thread_id", "id"):
        val = inp.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _projection_bus_op_from_input(inp: dict[str, Any], tool_name: str) -> str | None:
    op = inp.get("op")
    if isinstance(op, str) and op.strip():
        return op.strip().lower()
    if "agent_bus" in tool_name.lower():
        return "send"
    return None


def lane_touches_from_records(records: list[dict[str, Any]]) -> LaneTouches:
    """Return per-thread bus read/write tallies parsed from tool_use inputs."""
    tallies: dict[str, dict[str, int]] = defaultdict(lambda: {"w": 0, "r": 0})
    for tool_name, inp in iter_normalized_tool_uses(records):
        lname = tool_name.lower()
        if lname != "agent_bus":
            continue
        thread = _projection_bus_thread_from_input(inp)
        if thread is None:
            continue
        op = _projection_bus_op_from_input(inp, tool_name)
        if op in _PROJECTION_WRITE_OPS:
            tallies[thread]["w"] += 1
        elif op in _PROJECTION_READ_OPS:
            tallies[thread]["r"] += 1
    return {tid: dict(vals) for tid, vals in tallies.items()}


def projection_dominant_lane(
    touches: LaneTouches,
    root: str,
    children: list[str],
) -> str:
    """Pick the lane with the highest write count; ties resolve to *root*."""
    candidates = [root, *children]
    best = root
    best_w = -1
    for lane in candidates:
        w = touches.get(lane, {}).get("w", 0)
        if w > best_w:
            best_w = w
            best = lane
    return best


def projection_binding_for(
    lane: str,
    touches: LaneTouches,
    *,
    explicit_uuids: set[str] | None = None,
    transcript_id: str | None = None,
) -> str:
    """Classify how a window binds to *lane* from bus touch tallies (projection)."""
    if explicit_uuids and transcript_id and transcript_id in explicit_uuids:
        return "explicit_cp"
    lane_touch = touches.get(lane, {})
    w = lane_touch.get("w", 0)
    r = lane_touch.get("r", 0)
    if w == 0 and r == 0:
        return "no_touch"
    if w == 0 and r > 0:
        return "read_only"
    total_w = sum(t.get("w", 0) for t in touches.values())
    if total_w > 0 and w < total_w and w <= max(
        touches.get(other, {}).get("w", 0)
        for other in touches
        if other != lane
    ):
        return "foreign_dominant"
    return "dominant_write"


__all__ = [
    "LaneTouch",
    "LaneTouches",
    "binding_for",
    "dominant_lane",
    "lane_touches",
    "lane_touches_from_records",
    "projection_binding_for",
    "projection_dominant_lane",
]
