"""SDK multi-row posture — View-only labels for how open rows relate.

Activity (per row, own fields only):

* ``LIVE`` — ``thread_id`` is set, or ``tool_call_count > 0``, or
  ``idle_age_ms < elapsed_ms`` (progress heartbeat younger than start).
* ``UNSTARTED`` — no thread, no tool calls, and idle age equals elapsed.

Relationship classes among LIVE rows (≠ solo):

* ``nested`` — lease park or auto-review child, or a closed ``nest_under`` family
  with no unrelated live peers. PARENT / CHILD stay on those rows.
* ``id_split`` — same root, ≥2 LIVE rows, no park. No per-row GHOST/LIVE tag.
* ``parallel`` — ≥2 LIVE rows on distinct roots, or unrelated peers beside a nest
  family (bar says parallel; tree glyphs still mark the family).

Membership stays Rival A (open obligation = ``terminal_ms is None``). Activity
is a paint split, not a fold close.
"""

from __future__ import annotations

from typing import Literal

from .dtos import SdkDispatchRow
from .sdk_tree import resolved_nest_members

SdkMultiPosture = Literal["solo", "nested", "id_split", "parallel"]
SdkActivity = Literal["LIVE", "UNSTARTED"]

#: Short tags painted on LIVE rows when multi (nested / parallel only).
ROW_TAG = {
    "parent": "PARENT",
    "child": "CHILD",
    "para": "PARA",
}


def classify_sdk_activity(row: SdkDispatchRow) -> SdkActivity:
    """LIVE vs UNSTARTED from this row's fields only — no sibling compare."""
    if row.thread_id:
        return "LIVE"
    if row.tool_call_count is not None and row.tool_call_count > 0:
        return "LIVE"
    idle = row.idle_age_ms
    elapsed = row.elapsed_ms
    if idle is not None and elapsed is not None and idle < elapsed:
        return "LIVE"
    return "UNSTARTED"


def split_open_sdk(
    rows: list[SdkDispatchRow],
) -> tuple[list[SdkDispatchRow], list[SdkDispatchRow]]:
    """Partition still-open rows into LIVE then UNSTARTED (oldest elapsed first)."""
    live = [row for row in rows if classify_sdk_activity(row) == "LIVE"]
    unstarted = [row for row in rows if classify_sdk_activity(row) == "UNSTARTED"]
    live = sort_sdk_live(live, classify_sdk_live(live))
    unstarted.sort(key=lambda row: -(row.elapsed_ms or 0))
    return live, unstarted


def sdk_flight_label(
    live: list[SdkDispatchRow],
    unstarted: list[SdkDispatchRow],
) -> str:
    """Header count: LIVE only; append ``+M unstarted`` when any exist."""
    label = f"sdk {len(live)}"
    if unstarted:
        label += f" +{len(unstarted)} unstarted"
    return label


def classify_sdk_live(live: list[SdkDispatchRow]) -> SdkMultiPosture:
    """Classify how the current LIVE SDK set relates."""
    if len(live) < 2:
        return "solo"
    if any(row.state == "parked_waiting" for row in live):
        return "nested"
    if _has_review_child_nest(live):
        return "nested"
    nest_members = resolved_nest_members(live)
    if nest_members:
        if all(row.dispatch_id in nest_members for row in live):
            return "nested"
        return "parallel"
    roots = {row.root_id for row in live if row.root_id}
    if len(roots) == 1 and any(row.root_id for row in live):
        return "id_split"
    if len(roots) >= 2:
        return "parallel"
    return "id_split"


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
    """Return PARENT / CHILD / PARA for nest and parallel chrome, else None."""
    if posture == "solo":
        return None
    if posture == "nested":
        if row.state == "parked_waiting":
            return "parent"
        if (
            any(r.state == "parked_waiting" for r in live)
            and row.state != "parked_waiting"
        ):
            return "child"
        if row.review_child and _parent_dispatch_id(row, live):
            return "child"
        if row.nest_under and any(r.dispatch_id == row.nest_under for r in live):
            return "child"
        if any(
            r.review_child and r.parent_execution_id == row.dispatch_id for r in live
        ):
            return "parent"
        if any(r.nest_under == row.dispatch_id for r in live):
            return "parent"
        return None
    if posture == "parallel":
        return "para"
    return None


def posture_legend(posture: SdkMultiPosture) -> str | None:
    """One-line legend under the SDK bar when multi; None for solo."""
    if posture == "solo":
        return None
    if posture == "nested":
        return "  multi: nested — lease park, nest_under family, or auto-review child"
    if posture == "id_split":
        return None
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
    """Order live rows for glance: parent→child within nest families, else idle-desc."""
    from .sdk_tree import sort_sdk_tree

    tree_ordered = sort_sdk_tree(live) if posture == "nested" else live

    def key(row: SdkDispatchRow) -> tuple:
        role = row_role(row, live, posture)
        role_rank = {
            "parent": 0,
            "child": 1,
            "para": 0,
            None: 0,
        }.get(role, 0)
        tree_rank = (
            tree_ordered.index(row)
            if posture == "nested" and row in tree_ordered
            else 0
        )
        return (
            0 if row.divergent_fields else 1,
            0 if row.queue_position is None else 1,
            role_rank if posture != "nested" else tree_rank,
            -(row.idle_age_ms or 0),
        )

    return sorted(live, key=key)
