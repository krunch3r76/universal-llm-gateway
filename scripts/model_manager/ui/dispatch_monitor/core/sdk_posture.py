"""SDK multi-row posture — View-only labels for how live rows relate.

Three operator-facing classes (≠ solo):

* ``nested`` — lease park or auto-review child: a live parent row waits while
  a child runs (``parked_waiting`` or explicit ``parent_execution_id`` link).
* ``id_split`` — same root, ≥2 live rows, no park — usually Stargate ``execution_id``
  vs worker ``dispatch_id`` for one logical dispatch (ghost + live).
* ``parallel`` — ≥2 live rows on distinct roots (concurrent work).

Membership stays Rival A (open obligation = live). This module only names the
relationship so the board does not leave the operator to infer it.
"""

from __future__ import annotations

from typing import Literal

from .dtos import SdkDispatchRow

SdkMultiPosture = Literal["solo", "nested", "id_split", "parallel"]

#: Short tags painted on each live row when multi.
ROW_TAG = {
    "parent": "PARENT",
    "child": "CHILD",
    "ghost": "GHOST",
    "live": "LIVE",
    "para": "PARA",
}


def classify_sdk_live(live: list[SdkDispatchRow]) -> SdkMultiPosture:
    """Classify how the current live SDK set relates."""
    if len(live) < 2:
        return "solo"
    if any(row.state == "parked_waiting" for row in live):
        return "nested"
    if _has_review_child_nest(live):
        return "nested"
    roots = {row.root_id for row in live if row.root_id}
    if len(roots) == 1 and any(row.root_id for row in live):
        return "id_split"
    if len(roots) >= 2:
        return "parallel"
    # Multi live with missing roots — treat as id_split (same unknown bucket).
    return "id_split"


def _has_progress(row: SdkDispatchRow) -> bool:
    """True when the row shows worker activity (toolcalls / named tool)."""
    if row.tool_call_count is not None and row.tool_call_count > 0:
        return True
    return bool(row.last_tool_name)


def _has_review_child_nest(live: list[SdkDispatchRow]) -> bool:
    """True when a live review child points at another live (or parked) parent row."""
    live_ids = {row.dispatch_id for row in live}
    for row in live:
        if not row.review_child or not row.parent_execution_id:
            continue
        if row.parent_execution_id in live_ids:
            return True
    return False


def _parent_dispatch_id(row: SdkDispatchRow, live: list[SdkDispatchRow]) -> str | None:
    if row.parent_execution_id and any(
        r.dispatch_id == row.parent_execution_id for r in live
    ):
        return row.parent_execution_id
    return None


def row_role(
    row: SdkDispatchRow,
    live: list[SdkDispatchRow],
    posture: SdkMultiPosture,
) -> str | None:
    """Return a short per-row role tag, or None when solo / unneeded."""
    if posture == "solo":
        return None
    if posture == "nested":
        if row.state == "parked_waiting":
            return "parent"
        if row.review_child and _parent_dispatch_id(row, live):
            return "child"
        if any(
            r.review_child and r.parent_execution_id == row.dispatch_id for r in live
        ):
            return "parent"
        return "child"
    if posture == "parallel":
        return "para"
    # id_split — mark silent sibling GHOST when another same-root row has progress.
    root = row.root_id
    siblings = [r for r in live if r.root_id == root] if root else live
    any_progress = any(_has_progress(r) for r in siblings)
    if not any_progress:
        return "live"
    if _has_progress(row):
        return "live"
    return "ghost"


def posture_legend(posture: SdkMultiPosture) -> str | None:
    """One-line legend under the SDK bar when multi; None for solo."""
    if posture == "solo":
        return None
    if posture == "nested":
        return (
            "  multi: nested — PARENT/CHILD "
            "(parked_waiting lease or auto-review child)"
        )
    if posture == "id_split":
        return (
            "  multi: id_split — same root dual IDs "
            "(GHOST=Stargate exec · LIVE=worker dispatch; "
            "writer count = ledger live writers, not id_split)"
        )
    return "  multi: parallel — concurrent roots (distinct WIP)"


def live_writer_count(*, active_by_lane: dict[str, int] | None) -> int:
    """Ledger-derived live writer census for board chrome (F-board)."""
    if not active_by_lane:
        return 0
    return int(active_by_lane.get("A", 0)) + int(active_by_lane.get("B", 0))


def sort_sdk_live(
    live: list[SdkDispatchRow],
    posture: SdkMultiPosture,
) -> list[SdkDispatchRow]:
    """Order live rows for glance: parent→child, live→ghost, else idle-desc."""

    def key(row: SdkDispatchRow) -> tuple:
        role = row_role(row, live, posture)
        role_rank = {
            "parent": 0,
            "child": 1,
            "live": 0,
            "ghost": 1,
            "para": 0,
            None: 0,
        }.get(role, 0)
        return (
            0 if row.divergent_fields else 1,
            0 if row.queue_position is None else 1,
            role_rank,
            -(row.idle_age_ms or 0),
        )

    return sorted(live, key=key)
