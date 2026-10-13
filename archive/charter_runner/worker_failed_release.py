"""Release ledger WIP when a charter worker terminal-fails without CHECKPOINT.

Phase-3 hole (live 6409): ``ADMIT_CONSULT`` / ``ADMIT_WORKER`` set
``wip_window_id``; a cursor-sdk ``FAILED`` closeout never posts a root
CHECKPOINT, so harvest never pairs, ``decide`` sees WIP forever → sticky
``CONSULT_ADMITTED`` / ``ADMITTED`` NOOP. ``Transition.WORKER_FAILED`` existed
but was never applied.
"""

from __future__ import annotations

import time
from dataclasses import replace

from universal_logging import get_logger

from . import bus_client, window_log
from .admission import ADMISSION_SUBJECT_PREFIX, _latest_matching
from .consult_lane import backoff_s
from .root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
    load_root,
    upsert_root,
    write_cortex_mirror,
)
from .window_sequence import window_id_for, window_index_from_id
from .work_key_store import find_record_by_window_id, stamp_disposition

logger = get_logger(__name__)

_IN_FLIGHT = frozenset({RootStatus.ADMITTED, RootStatus.CONSULT_ADMITTED})


def _admission_for_wip(
    turns: list[dict],
    *,
    wip_window_id: str,
) -> dict | None:
    """Latest admission pointer whose meta.window matches the ledger WIP."""
    want = window_index_from_id(wip_window_id)
    if want <= 0:
        return None
    tip = _latest_matching(
        turns,
        lambda subject: subject.upper().startswith(ADMISSION_SUBJECT_PREFIX.upper()),
    )
    if tip is None:
        return None
    meta = window_log.parse_admission_meta(str(tip.get("body") or ""))
    try:
        window = int(meta.get("window") or 0)
    except (TypeError, ValueError):
        window = 0
    if window != want:
        return None
    return tip


async def maybe_release_failed_window_wip(
    conn,
    row: RootLedgerRow,
    turns: list[dict],
) -> tuple[RootLedgerRow, str | None]:
    """If the in-flight worker terminal-failed, clear WIP and re-arm the lane.

    Returns ``(live_row, failure_reason|None)``. Consult admits go to
    ``CONSULT_DEFERRED`` with backoff (retryable fire). Worker admits go to
    ``IDLE`` so the next decide can re-admit.
    """
    if row.status not in _IN_FLIGHT or not row.wip_window_id:
        return row, None
    admission = _admission_for_wip(turns, wip_window_id=row.wip_window_id)
    if admission is None:
        return row, None
    meta = window_log.parse_admission_meta(str(admission.get("body") or ""))
    worker_thread = str(meta.get("worker_thread") or "").strip()
    if not worker_thread:
        return row, None
    reason = await bus_client.worker_failure_reason(worker_thread)
    if not reason:
        return row, None

    window_index = window_index_from_id(row.wip_window_id)
    was_consult = row.status == RootStatus.CONSULT_ADMITTED
    attempts = row.consult_attempts + (1 if was_consult else 0)
    if row.status in (RootStatus.BLOCKED, RootStatus.CLOSED):
        next_status = row.status
        next_retry = None
    elif was_consult:
        next_status = RootStatus.CONSULT_DEFERRED
        next_retry = time.time() + backoff_s(max(attempts, 1))
    else:
        next_status = RootStatus.IDLE
        next_retry = None

    released = replace(
        row,
        status=next_status,
        wip_window_id=None,
        last_window_id=window_id_for(row.root_id, window_index)
        if window_index > 0
        else row.last_window_id,
        last_transition=Transition.WORKER_FAILED.value,
        last_error=f"worker_failed:{reason}:thread={worker_thread}",
        consult_attempts=attempts if was_consult else row.consult_attempts,
        consult_next_retry=next_retry if was_consult else row.consult_next_retry,
        updated_at=time.time(),
    )
    upsert_root(conn, released)
    write_cortex_mirror(released)
    window_id = window_id_for(row.root_id, window_index) if window_index > 0 else None
    if window_id:
        record = find_record_by_window_id(conn, window_id)
        if record is not None:
            stamp_disposition(
                conn,
                work_key=record.work_key,
                window_id=window_id,
                disposition="failed",
            )
    if was_consult:
        _mark_consult_queue_retry(conn, released)
    logger.warning(
        "charter-runner released failed worker WIP root=%s window=%s "
        "worker=%s reason=%s -> %s",
        row.root_id,
        window_index,
        worker_thread,
        reason,
        next_status.value,
    )
    live = load_root(conn, row.root_id) or released
    return live, reason


def _mark_consult_queue_retry(conn, row: RootLedgerRow) -> None:
    """Keep the durable consult row live with backoff after a failed admit."""
    from libs.charter_runner_store.db import execute_with_retry

    gid = row.pickup_gid or "G?"
    role = row.consult_role or "judgment_gap"
    now = time.time()
    execute_with_retry(
        conn,
        """
        UPDATE consult_queue
           SET status='queued',
               attempts=?,
               next_retry=?,
               updated_at=?
         WHERE root_id = ? AND gid = ? AND consult_role = ?
        """,
        (
            row.consult_attempts,
            row.consult_next_retry,
            now,
            row.root_id,
            gid,
            role,
        ),
    )


__all__ = ["maybe_release_failed_window_wip"]
