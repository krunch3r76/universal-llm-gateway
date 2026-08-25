"""Lease-acquired / resume / duplicate-refused / partial-work handlers.

Kept out of ``sdk.py`` so that module stays inside the SLOC budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .sdk_state import SdkState, ensure_canonical_row, first_str, lease_row_id

if TYPE_CHECKING:
    from ..protocols import EventRecord
    from .sdk import SdkFold


def set_write_lease_holder(fold: SdkFold, dispatch_id: str) -> None:
    """Point the live GIW write-lease at ``dispatch_id`` (alias-resolved)."""
    fold.write_lease_holder_id = fold._aliases.resolve(dispatch_id)


def clear_write_lease_if_current(fold: SdkFold, dispatch_id: str) -> None:
    """Clear the write-lease pointer when ``dispatch_id`` holds it."""
    if fold.write_lease_holder_id is None:
        return
    released = fold._aliases.resolve(dispatch_id)
    current = fold._aliases.resolve(fold.write_lease_holder_id)
    if released == current:
        fold.write_lease_holder_id = None


def park_enter_write_lease(fold: SdkFold, child_id: str) -> None:
    """Park yields the write-lease to the nested child."""
    fold.write_lease_holder_id = fold._aliases.resolve(child_id)


def park_restore_write_lease(fold: SdkFold, parent_id: str) -> None:
    """Child terminal returns the write-lease to the parent."""
    fold.write_lease_holder_id = fold._aliases.resolve(parent_id)


def live_write_lease(
    fold: SdkFold, *, now_ms: int
) -> tuple[str | None, str | None, str | None, int | None]:
    """Return live GIW lease paint fields when holder row is non-terminal."""
    holder_id = fold.write_lease_holder_id
    if not holder_id:
        return None, None, None, None
    resolved = fold._aliases.resolve(holder_id)
    row = fold.dispatches.get(resolved)
    if row is None or row.terminal_ms is not None:
        return None, None, None, None
    hb = (
        None
        if row.last_progress_ms is None
        else max(0, now_ms - row.last_progress_ms)
    )
    return resolved, row.thread_id, row.model, hb


def on_lease_acquired(fold: SdkFold, record: EventRecord) -> None:
    """Start/admit clock when ``started_ms`` is still missing (lease is running)."""
    row = fold._state(record)
    if row is None:
        return
    if row.started_ms is None:
        row.started_ms = record.ts_unix_ms
    if row.terminal_ms is None and row.state in ("unknown", "queued"):
        row.state = "running"
    source_repo = record.payload.get("source_repo")
    if source_repo and row.source_repo is None:
        row.source_repo = str(source_repo)
    fold._advance_progress(row, record.ts_unix_ms)
    if dispatch_id := first_str(record.payload, ("dispatch_id", "execution_id")):
        set_write_lease_holder(fold, dispatch_id)


def on_lease_promoted(fold: SdkFold, record: EventRecord) -> None:
    """FIFO advance — queued dispatch becomes lease holder (v3 §5)."""
    row = fold._state(record)
    if row is None or row.terminal_ms is not None:
        return
    row.state = "running"
    row.queue_position = None
    fold._advance_progress(row, record.ts_unix_ms)
    if dispatch_id := first_str(record.payload, ("dispatch_id", "execution_id")):
        set_write_lease_holder(fold, dispatch_id)


def on_lease_released(fold: SdkFold, record: EventRecord) -> None:
    """Write lease released for a dispatch (v3 §5)."""
    row = fold._state(record)
    if row is None:
        return
    if payload := record.payload:
        if payload.get("source_repo") and row.source_repo is None:
            row.source_repo = str(payload["source_repo"])
    if row.terminal_ms is None and row.state != "parked_waiting":
        row.lease_released_without_terminal = True
    if dispatch_id := first_str(record.payload, ("dispatch_id", "execution_id")):
        clear_write_lease_if_current(fold, dispatch_id)


def on_park_enter(fold: SdkFold, record: EventRecord) -> None:
    """Parent yields lease to nested child — parent → ``parked_waiting`` (v3 §5)."""
    payload = record.payload
    parent_id = lease_row_id(payload, "parent_id")
    child_id = lease_row_id(payload, "child_id")
    if parent_id:
        parent = fold._row_for_id(parent_id, record)
        if parent.terminal_ms is None and parent.state != "parked_waiting":
            parent.pre_park_state = parent.state
            parent.state = "parked_waiting"
    if child_id:
        child = fold._row_for_id(child_id, record)
        if child.terminal_ms is None:
            child.state = "running"
            fold._advance_progress(child, record.ts_unix_ms)
    if parent_id and child_id:
        note_lease_park(fold, parent_id, child_id)


def on_park_restore(fold: SdkFold, record: EventRecord) -> None:
    """Child terminal returns lease to parent — restore prior parent state (v3 §5)."""
    payload = record.payload
    parent_id = lease_row_id(payload, "parent_id")
    if not parent_id:
        return
    parent_id = fold._aliases.resolve(parent_id)
    parent = fold.dispatches.get(parent_id)
    if parent is None or parent.terminal_ms is not None:
        return
    parent.state = parent.pre_park_state or "running"
    parent.pre_park_state = None
    parent.last_progress_ms = record.ts_unix_ms
    park_restore_write_lease(fold, parent_id)


def on_worker_resumed(fold: SdkFold, record: EventRecord) -> None:
    """Stamp ``resume_of``; collapse onto a live parent rather than a second LIVE row."""
    payload = record.payload
    dispatch_id = first_str(payload, ("dispatch_id",))
    resume_of = first_str(payload, ("resume_of",))
    if not dispatch_id:
        return
    if resume_of:
        parent_id = fold._aliases.resolve(resume_of)
        parent = fold.dispatches.get(parent_id)
        if parent is not None and parent.terminal_ms is None:
            row = ensure_canonical_row(
                fold.dispatches, fold._aliases, parent_id, (dispatch_id,)
            )
            _mark_resumed_progress(fold, row, record)
            return
    row = fold._state(record)
    if row is None:
        return
    if resume_of and row.resume_of is None:
        row.resume_of = resume_of
    _mark_resumed_progress(fold, row, record)


def _mark_resumed_progress(fold: SdkFold, row: SdkState, record: EventRecord) -> None:
    """Shared idle/start stamps after a resume event lands on a row."""
    if row.started_ms is None:
        row.started_ms = record.ts_unix_ms
    if row.terminal_ms is None and row.state in ("unknown", "queued"):
        row.state = "running"
    fold._absorb_identity(row, record)
    if row.resume_of == row.dispatch_id:
        row.resume_of = None
    fold._advance_progress(row, record.ts_unix_ms)


def on_duplicate_refused(fold: SdkFold, record: EventRecord) -> None:
    """Record a refused admit for attention — never mint a live dispatch row."""
    dispatch_id = first_str(record.payload, ("dispatch_id",))
    if not dispatch_id:
        return
    holder = first_str(record.payload, ("holder_dispatch_id",)) or "?"
    thread_id = first_str(record.payload, ("thread_id",)) or ""
    fold.duplicate_refused.setdefault(
        dispatch_id, (record.ts_unix_ms, holder, thread_id)
    )


def on_partial_work_specimen(fold: SdkFold, record: EventRecord) -> None:
    """Ack production ``partial:work`` specimen — identity only, never terminalize."""
    row = fold._state(record)
    if row is None:
        return
    fold._advance_progress(row, record.ts_unix_ms)


def note_lease_park(fold: SdkFold, parent_id: str, child_id: str) -> None:
    """Record an evidence-only park edge (first-seen parent/child pair)."""
    pair = (parent_id, child_id)
    if pair not in fold.lease_parks:
        fold.lease_parks.append(pair)
    park_enter_write_lease(fold, child_id)
