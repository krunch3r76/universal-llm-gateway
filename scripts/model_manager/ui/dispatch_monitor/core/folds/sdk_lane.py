"""Checkout lane/branch stamp/join — kept out of ``sdk.py`` for the SLOC budget."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocols import EventRecord
    from .sdk import SdkFold


def _find_row_by_dispatch_id(fold: SdkFold, dispatch_id: str) -> object | None:
    """Look up an existing row by ``dispatch_id`` / aliases — never create."""
    resolved = fold._aliases.resolve(dispatch_id)
    row = fold.dispatches.get(resolved)
    if row is not None:
        return row
    for candidate in fold.dispatches.values():
        if candidate.dispatch_id == dispatch_id or candidate.dispatch_id.startswith(
            f"{dispatch_id}-"
        ):
            return candidate
    return None


def _short_branch(branch: str) -> str:
    prefix = "cursor-sdk/"
    return branch[len(prefix) :] if branch.startswith(prefix) else branch


def stash_or_stamp_lane(fold: SdkFold, record: EventRecord) -> None:
    """Extract lane from ``sdk.lane.selected`` and stamp or stash until row exists."""
    payload = record.payload
    dispatch_id = payload.get("dispatch_id")
    lane = payload.get("lane")
    if not dispatch_id or not lane:
        return
    dispatch_id = str(dispatch_id)
    lane_s = str(lane)
    row = _find_row_by_dispatch_id(fold, dispatch_id)
    if row is not None:
        from .sdk_state import SdkState

        assert isinstance(row, SdkState)
        if row.checkout_lane is None:
            row.checkout_lane = lane_s
    else:
        fold._pending_lane[dispatch_id] = lane_s


def stash_or_stamp_branch(fold: SdkFold, record: EventRecord) -> None:
    """Extract branch from ``sdk.lane_b.minted`` and stamp or stash until row exists."""
    payload = record.payload
    dispatch_id = payload.get("dispatch_id")
    branch = payload.get("branch")
    if not dispatch_id or not branch:
        return
    dispatch_id = str(dispatch_id)
    branch_s = str(branch)
    row = _find_row_by_dispatch_id(fold, dispatch_id)
    if row is not None:
        from .sdk_state import SdkState

        assert isinstance(row, SdkState)
        if row.checkout_branch is None:
            row.checkout_branch = branch_s
    else:
        fold._pending_branch[dispatch_id] = branch_s


def apply_pending_lane(fold: SdkFold, row: object) -> None:
    """Apply stashed checkout lane/branch once a dispatch row resolves."""
    from .sdk_state import SdkState

    assert isinstance(row, SdkState)
    if not fold._pending_lane and not fold._pending_branch:
        return
    for key, lane in fold._pending_lane.items():
        if row.dispatch_id == key or row.dispatch_id.startswith(f"{key}-"):
            if row.checkout_lane is None:
                row.checkout_lane = lane
            break
    for key, branch in fold._pending_branch.items():
        if row.dispatch_id == key or row.dispatch_id.startswith(f"{key}-"):
            if row.checkout_branch is None:
                row.checkout_branch = branch
            break


__all__ = (
    "apply_pending_lane",
    "stash_or_stamp_branch",
    "stash_or_stamp_lane",
    "_short_branch",
)
