"""SDK multi-row posture — View-only labels for how live rows relate.

Three operator-facing classes (≠ solo):

* ``nested`` — lease park: a live parent is ``parked_waiting`` while a child runs.
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


def row_role(
    row: SdkDispatchRow,
    live: list[SdkDispatchRow],
    posture: SdkMultiPosture,
) -> str | None:
    """Return a short per-row role tag, or None when solo / unneeded."""
    if posture == "solo":
        return None
    if posture == "nested":
        return "parent" if row.state == "parked_waiting" else "child"
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
        return "  multi: nested — PARENT parked_waiting · CHILD holds lease"
    if posture == "id_split":
        return (
            "  multi: id_split — same root dual IDs "
            "(GHOST=Stargate exec · LIVE=worker dispatch)"
        )
    return "  multi: parallel — concurrent roots (distinct WIP)"


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
