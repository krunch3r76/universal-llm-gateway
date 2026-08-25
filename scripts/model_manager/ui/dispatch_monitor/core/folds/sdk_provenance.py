"""Lease-acquired / resume / duplicate-refused / partial-work handlers.

Kept out of ``sdk.py`` so that module stays inside the SLOC budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .sdk_state import SdkState, ensure_canonical_row, first_str

if TYPE_CHECKING:
    from ..protocols import EventRecord
    from .sdk import SdkFold


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
