"""Harvest side effects and open-children query for operator lanes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .checkpoint_auto_stamp_wiring import load_thread_tags
from .db.lane_associations import list_substantiated_child_thread_ids
from .db.threads import get_thread, normalize_thread_id
from .db.turns import bulk_mark_read_state, get_turns

_DISPOSITION_CHILD_LINE = re.compile(r"^(?:lane|child):\s*(\d+)\s*$", re.MULTILINE)
_DISPOSITION_TYPE_LINE = "TYPE: DISPOSITION"
_CLOSEOUT_TYPE_LINE = "TYPE: CLOSEOUT"


def _first_non_empty_line(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _is_unread_terminal_closeout(turn_row: dict[str, Any]) -> bool:
    if turn_row.get("read_at") is not None:
        return False
    body = turn_row.get("body") or ""
    return _first_non_empty_line(body) == _CLOSEOUT_TYPE_LINE


def _unread_terminal_closeout_turn_numbers(*, child_thread_id: str) -> list[int]:
    numbers: list[int] = []
    for row in get_turns(thread=child_thread_id, include_superseded=False):
        if _is_unread_terminal_closeout(row):
            numbers.append(int(row["turn_number"]))
    return numbers


def disposition_child_ids(body: str) -> tuple[str, ...]:
    """Parse ``lane:`` / ``child:`` lines naming decimal thread ids (whole line only)."""
    ids = _DISPOSITION_CHILD_LINE.findall(body or "")
    return tuple(dict.fromkeys(ids))


def compute_open_children(parent_thread_id: str) -> tuple[str, ...]:
    """Active depth-1 children plus any child with an unread ``TYPE: CLOSEOUT`` turn."""
    parent_thread_id = normalize_thread_id(parent_thread_id)
    child_ids = list_substantiated_child_thread_ids(parent_thread_id=parent_thread_id)
    open_ids: set[str] = set()
    for child_id in child_ids:
        thread_row = get_thread(child_id)
        if thread_row is None:
            continue
        if thread_row.get("status") == "active":
            open_ids.add(child_id)
        if _unread_terminal_closeout_turn_numbers(child_thread_id=child_id):
            open_ids.add(child_id)
    return tuple(sorted(open_ids, key=int))


def mark_harvested_closeouts(
    *,
    parent_thread_id: str,
    body: str,
    from_agent: str,
) -> dict[str, dict[str, list[int]]]:
    """Mark harvested closeouts read after a qualifying parent ``TYPE: DISPOSITION``."""
    marked: dict[str, list[int]] = {}
    if from_agent != "web-anthropic":
        return {"marked": marked}
    if _first_non_empty_line(body or "") != _DISPOSITION_TYPE_LINE:
        return {"marked": marked}
    parent_thread_id = normalize_thread_id(parent_thread_id)
    tags = load_thread_tags(parent_thread_id)
    if "lane:cursor-auto" not in tags:
        return {"marked": marked}

    substantiated = set(
        list_substantiated_child_thread_ids(parent_thread_id=parent_thread_id)
    )
    for child_id in disposition_child_ids(body):
        if child_id not in substantiated:
            continue
        turn_numbers = _unread_terminal_closeout_turn_numbers(child_thread_id=child_id)
        if not turn_numbers:
            continue
        bulk_mark_read_state(thread=child_id, turn_numbers=turn_numbers)
        marked[child_id] = turn_numbers
    return {"marked": marked}


def open_children_as_of() -> str:
    """UTC ISO timestamp for open-children query responses."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
