"""Transcript projection state schema, merge transform, and compaction.

The state JSON under ``notes/system/threads/{N}-transcript-projection.state.json``
is the projection SoT; cells merge idempotently on ``(transcript_id, turn_lo, turn_hi)``.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA = "transcript-projection/v1"
STATE_SIZE_CAP = 1_048_576


@dataclass
class CellFacts:
    """Mechanical facts stored inside a projection cell."""

    asks: list[str] = field(default_factory=list)
    bus_sends: list[dict[str, Any]] = field(default_factory=list)
    bus_touch: dict[str, dict[str, int]] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    code_paths: list[str] = field(default_factory=list)
    dispatches: list[dict[str, Any]] = field(default_factory=list)
    sha_mentions: list[str] = field(default_factory=list)
    harness_errors: int = 0
    prose_tail: str = ""


@dataclass
class ProjectionCell:
    """One closed or open-tail cell in a window."""

    turn_lo: int
    turn_hi: int
    boundary: dict[str, Any] | None = None
    note: str = ""
    note_source: str = ""
    facts: CellFacts = field(default_factory=CellFacts)
    projected_at: str = ""
    compacted: bool = False


@dataclass
class WindowState:
    """Per-window projection state."""

    lane: str = ""
    binding: str = ""
    tab_title: str = ""
    tab_titles: list[str] = field(default_factory=list)
    opening_ask: str | None = None
    first_seen: str = ""
    jsonl: dict[str, Any] = field(default_factory=dict)
    source: str = "live"
    session_ids: list[str] = field(default_factory=list)
    turn_count: int = 0
    closed_hi: int = 0
    cells: list[dict[str, Any]] = field(default_factory=list)
    open_tail: dict[str, Any] | None = None
    terminal: dict[str, Any] | None = None


def empty_state(thread: str, children: list[str]) -> dict[str, Any]:
    """Return a fresh v1 state document."""
    return {
        "schema": SCHEMA,
        "thread": thread,
        "children_policy": "sub_mission",
        "children": children,
        "updated_at": "",
        "runs": 0,
        "last_run": {},
        "windows": {},
        "anchor_mismatches": [],
    }


def load_state(text: str) -> dict[str, Any]:
    """Parse state JSON; raises ValueError on corrupt input."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"state_corrupt: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("state_corrupt: schema mismatch")
    return data


def _cell_key(cell: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(cell.get("_transcript_id", "")),
        int(cell.get("turn_lo", 0)),
        int(cell.get("turn_hi", 0)),
    )


def merge_window_update(
    state: dict[str, Any],
    transcript_id: str,
    window_patch: dict[str, Any],
    *,
    cells_added: list[dict[str, Any]],
    cells_updated: list[dict[str, Any]],
) -> None:
    """Apply one window merge into *state* (mutates in place)."""
    windows = state.setdefault("windows", {})
    existing = windows.get(transcript_id, {})
    merged = {**existing, **window_patch}
    old_cells = {(_c["turn_lo"], _c["turn_hi"]): _c for _c in existing.get("cells", [])}
    for cell in window_patch.get("cells", []):
        key = (cell["turn_lo"], cell["turn_hi"])
        if key in old_cells:
            prev = old_cells[key]
            if prev != cell:
                cells_updated.append({"transcript_id": transcript_id, "cell": cell})
            old_cells[key] = cell
        else:
            cells_added.append({"transcript_id": transcript_id, "cell": cell, "change": "added"})
            old_cells[key] = cell
    merged["cells"] = sorted(old_cells.values(), key=lambda c: (c["turn_lo"], c["turn_hi"]))
    windows[transcript_id] = merged


def compact_state(state: dict[str, Any]) -> bool:
    """Drop prose_tail/asks from oldest closed cells until under size cap."""
    changed = False
    while len(json.dumps(state)) > STATE_SIZE_CAP:
        oldest: dict[str, Any] | None = None
        for window in state.get("windows", {}).values():
            for cell in window.get("cells", []):
                if cell.get("compacted"):
                    continue
                if oldest is None or cell.get("turn_lo", 0) < oldest.get("turn_lo", 0):
                    oldest = cell
        if oldest is None:
            break
        facts = oldest.setdefault("facts", {})
        facts.pop("prose_tail", None)
        facts["asks"] = []
        oldest["compacted"] = True
        changed = True
    return changed


def state_transform_merge(
    before_text: str,
    *,
    thread: str,
    children: list[str],
    window_updates: dict[str, dict[str, Any]],
    anchor_mismatches: list[dict[str, Any]],
    last_run: dict[str, Any],
    updated_at: str,
) -> str:
    """RMW transform: merge window patches and advance run metadata."""
    if before_text.strip():
        state = load_state(before_text)
    else:
        state = empty_state(thread, children)
    cells_added: list[dict[str, Any]] = []
    cells_updated: list[dict[str, Any]] = []
    for tid, patch in window_updates.items():
        merge_window_update(
            state,
            tid,
            patch,
            cells_added=cells_added,
            cells_updated=cells_updated,
        )
    by_cp = {row["cp_turn"]: row for row in state.get("anchor_mismatches", [])}
    for row in anchor_mismatches:
        by_cp[row["cp_turn"]] = row
    state["anchor_mismatches"] = sorted(by_cp.values(), key=lambda r: r["cp_turn"])
    state["children"] = children
    state["updated_at"] = updated_at
    state["runs"] = int(state.get("runs", 0)) + 1
    state["last_run"] = {**last_run, "cells_added": len(cells_added)}
    return json.dumps(state, indent=2, ensure_ascii=False) + "\n"


def facts_to_cell_dict(facts: CellFacts) -> dict[str, Any]:
    """Serialize CellFacts for JSON state."""
    return asdict(facts)


__all__ = [
    "CellFacts",
    "ProjectionCell",
    "SCHEMA",
    "WindowState",
    "compact_state",
    "empty_state",
    "facts_to_cell_dict",
    "load_state",
    "merge_window_update",
    "state_transform_merge",
]
