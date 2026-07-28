"""Review-child spawn fold helpers — kept out of ``sdk.py`` for SLOC budget."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .sdk_state import SdkState, first_str

if TYPE_CHECKING:
    from ..protocols import EventRecord
    from .sdk import SdkFold


def on_review_child_spawned(fold: SdkFold, record: EventRecord) -> None:
    """Attach a live review child row nested under its parent execution."""
    payload = record.payload
    execution_id = first_str(payload, ("execution_id",))
    parent_execution_id = first_str(payload, ("parent_execution_id",))
    if not execution_id:
        return

    if parent_execution_id:
        parent = _ensure_row(fold, parent_execution_id)
        if parent.terminal_ms is None and parent.state in ("unknown", "queued"):
            parent.state = "running"
        if parent.started_ms is None:
            parent.started_ms = record.ts_unix_ms

    child = _ensure_row(fold, execution_id)
    child.review_child = True
    child.parent_execution_id = parent_execution_id
    child.role = "reviewer"
    reviewer_model = payload.get("reviewer_model")
    if reviewer_model:
        child.model = str(reviewer_model)
    if child.state in ("unknown", "queued"):
        child.state = "running"
    if child.started_ms is None:
        child.started_ms = record.ts_unix_ms
    fold._advance_progress(child, record.ts_unix_ms)

    thread_id = payload.get("parent_thread_id")
    if thread_id and child.thread_id is None:
        child.thread_id = str(thread_id)
    root_id = payload.get("root_id") or payload.get("root")
    if root_id and child.root_id is None:
        child.root_id = str(root_id)
    fold._index.link_dispatch(child.dispatch_id, child.root_id, child.thread_id)


def _ensure_row(fold: SdkFold, row_id: str) -> SdkState:
    """Open a row by explicit id without spawn-payload alt-id alias bleed."""
    canonical = fold._aliases.resolve(row_id)
    row = fold.dispatches.get(canonical)
    if row is None:
        row = SdkState(row_id)
        fold.dispatches[row_id] = row
        fold._aliases.register(row_id, row_id)
    return row


def on_system_started(fold: SdkFold, record: EventRecord) -> None:
    """Terminalize review-child rows stranded before the new Stargate session."""
    terminalize_stale_review_children(
        fold.dispatches,
        watermark_ms=record.ts_unix_ms,
        ts_unix_ms=record.ts_unix_ms,
    )


def close_terminal_row(
    fold: SdkFold,
    row: SdkState,
    record: EventRecord,
    *,
    state: str,
    failure_reason: str | None,
    emitter: str,
) -> None:
    """Close id-split siblings and any live review children after terminal bind."""
    from .sdk_state import terminalize_id_siblings

    terminalize_id_siblings(
        fold.dispatches,
        fold._aliases,
        row,
        record.payload,
        record.ts_unix_ms,
        state=state,
        failure_reason=failure_reason,
        emitter=emitter,
    )
    terminalize_children_of_terminal_parent(fold.dispatches, row, record.ts_unix_ms)


def terminalize_children_of_terminal_parent(
    dispatches: dict[str, SdkState],
    parent: SdkState,
    ts_unix_ms: int,
) -> int:
    """Close live review children when their parent row has terminalized."""
    if parent.terminal_ms is None:
        return 0
    count = 0
    for row in list(dispatches.values()):
        if not row.review_child or row.terminal_ms is not None:
            continue
        if row.parent_execution_id != parent.dispatch_id:
            continue
        _terminalize_review_child(
            row,
            ts_unix_ms,
            state="orphaned",
            failure_reason="parent_terminal",
        )
        count += 1
    return count


def terminalize_stale_review_children(
    dispatches: dict[str, SdkState],
    *,
    watermark_ms: int | None,
    ts_unix_ms: int,
) -> int:
    """Clear live review children with no post-watermark progress."""
    count = 0
    for row in list(dispatches.values()):
        if not row.review_child or row.terminal_ms is not None:
            continue
        parent_id = row.parent_execution_id
        if parent_id:
            parent = dispatches.get(parent_id)
            if parent is not None and parent.terminal_ms is not None:
                _terminalize_review_child(
                    row,
                    ts_unix_ms,
                    state="orphaned",
                    failure_reason="parent_terminal",
                )
                count += 1
                continue
        if watermark_ms is None:
            continue
        anchor = row.started_ms or row.last_progress_ms
        if anchor is None or anchor >= watermark_ms:
            continue
        if row.last_progress_ms is not None and row.last_progress_ms >= watermark_ms:
            continue
        _terminalize_review_child(
            row,
            ts_unix_ms,
            state="orphaned",
            failure_reason="restart_orphan",
        )
        count += 1
    return count


def _terminalize_review_child(
    row: SdkState,
    ts_unix_ms: int,
    *,
    state: str,
    failure_reason: str,
) -> None:
    row.terminal_ms = ts_unix_ms
    row.terminal_emitter = "worker"
    row.state = state
    row.failure_reason = failure_reason
    if row.last_progress_ms is None or ts_unix_ms > row.last_progress_ms:
        row.last_progress_ms = ts_unix_ms
