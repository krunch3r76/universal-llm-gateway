"""Lane touch tallies from Cursor JSONL tool_use inputs — projection membership input.

Counts agent-bus read/write touches per thread id from assistant ``tool_use``
blocks only (E11: never parse tool responses). Used by transcript projection
membership to classify windows as dominant_write, read_only, foreign_dominant,
or no_touch relative to a root lane and its children.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

BusTouch = dict[str, int]
LaneTouches = dict[str, BusTouch]

from cortex_store.transcript_tool_normalize import iter_normalized_tool_uses

_WRITE_OPS = frozenset({"send", "post", "reply"})
_READ_OPS = frozenset({"get", "fetch", "wait", "read", "list", "search", "thread_get"})


def _iter_tool_uses(records: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return list(iter_normalized_tool_uses(records))


def _bus_thread_from_input(inp: dict[str, Any]) -> str | None:
    for key in ("thread", "thread_id", "id"):
        val = inp.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _bus_op_from_input(inp: dict[str, Any], tool_name: str) -> str | None:
    op = inp.get("op")
    if isinstance(op, str) and op.strip():
        return op.strip().lower()
    if "agent_bus" in tool_name.lower():
        return "send"
    return None


def lane_touches(records: list[dict[str, Any]]) -> LaneTouches:
    """Return per-thread bus read/write tallies parsed from tool_use inputs."""
    tallies: dict[str, dict[str, int]] = defaultdict(lambda: {"w": 0, "r": 0})
    for tool_name, inp in _iter_tool_uses(records):
        lname = tool_name.lower()
        if lname != "agent_bus":
            continue
        thread = _bus_thread_from_input(inp)
        if thread is None:
            continue
        op = _bus_op_from_input(inp, tool_name)
        if op in _WRITE_OPS:
            tallies[thread]["w"] += 1
        elif op in _READ_OPS:
            tallies[thread]["r"] += 1
    return {tid: dict(vals) for tid, vals in tallies.items()}


def dominant_lane(
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


def binding_for(
    lane: str,
    touches: LaneTouches,
    *,
    explicit_uuids: set[str] | None = None,
    transcript_id: str | None = None,
) -> str:
    """Classify how a window binds to *lane* from bus touch tallies."""
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


__all__ = ["binding_for", "dominant_lane", "lane_touches"]
