"""Nest park/restore coordination for cursor-sdk write-lease + capacity.

Ledger park columns and FifoCapacityGate.transfer_holder must both observe
enter/restore (PARK-RESTORE-DUAL). Ordinary release/force_release wakes FIFO
waiters and must not run while a parked parent waits for the child.

Unified liveness (5960): ``orphans := blocking_holders() − live_holders()``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_worker_orphaned,
    emit_write_lease_park_enter,
    emit_write_lease_park_restore,
)
from services.git_integration_worker.cursor_sdk_gate import (
    force_release_sdk_dispatch_slot,
    release_sdk_dispatch_slot,
    transfer_sdk_dispatch_slot,
    transfer_sdk_dispatch_slot_sync,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)
from services.git_integration_worker.cursor_sdk_restart_orphan import load_ledger_row

logger = get_logger(__name__)

_BLOCKING_STATUSES = ("admitted", "running", "parked_waiting")
_TERMINAL = frozenset({"completed", "failed"})

ReleaseDisposition = Literal["restored", "released"]


def _resolve_key(*, lease_key: str | None, source_repo: str | None) -> str | None:
    return lease_key if lease_key is not None else source_repo


def blocking_holders_conn(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Non-read-only rows that prevent FIFO promotion."""
    return conn.execute(
        "SELECT dispatch_id, status, lease_key, source_repo, worker_instance, "
        "park_child_dispatch_id, last_heartbeat_at, started_at "
        "FROM cursor_sdk_dispatches "
        "WHERE COALESCE(read_only,0)=0 "
        "AND status IN ('admitted','running','parked_waiting')"
    ).fetchall()


def _child_status_map(
    conn: sqlite3.Connection, rows: list[sqlite3.Row]
) -> dict[str, str]:
    child_ids = [
        r["park_child_dispatch_id"]
        for r in rows
        if r["park_child_dispatch_id"]
    ]
    if not child_ids:
        return {}
    placeholders = ",".join("?" * len(child_ids))
    return {
        r["dispatch_id"]: r["status"]
        for r in conn.execute(
            f"SELECT dispatch_id, status FROM cursor_sdk_dispatches "
            f"WHERE dispatch_id IN ({placeholders})",
            child_ids,
        )
    }


def _heartbeat_stale(
    row: sqlite3.Row,
    *,
    task_live: bool,
    threshold_s: float,
    dead_run_grace_s: float,
    arming_timeout_s: float | None,
) -> bool:
    grace_s = threshold_s if task_live else dead_run_grace_s
    if arming_timeout_s is not None and row["last_heartbeat_at"] is None:
        grace_s = min(grace_s, arming_timeout_s)
    cutoff = datetime.now(UTC).timestamp() - grace_s
    ts = row["last_heartbeat_at"] or row["started_at"]
    if ts is None:
        return True
    try:
        return datetime.fromisoformat(ts).timestamp() < cutoff
    except ValueError:
        return True


def live_holders(
    ledger: CursorDispatchLedger,
    blocking: list[sqlite3.Row],
    child_status: dict[str, str],
    *,
    threshold_s: float | None = None,
    dead_run_grace_s: float | None = None,
    arming_timeout_s: float | None = None,
) -> set[str]:
    """Blocking holders backed by a live asyncio task or live nested child."""
    live: set[str] = set()
    for row in blocking:
        did = row["dispatch_id"]
        if row["status"] in ("admitted", "running"):
            task = ledger._tasks.get(did)
            task_live = task is not None and not task.done()
            if not task_live:
                continue
            if threshold_s is not None and dead_run_grace_s is not None:
                if _heartbeat_stale(
                    row,
                    task_live=task_live,
                    threshold_s=threshold_s,
                    dead_run_grace_s=dead_run_grace_s,
                    arming_timeout_s=arming_timeout_s,
                ):
                    continue
            live.add(did)
            continue
        child_id = row["park_child_dispatch_id"]
        if not child_id:
            continue
        st = child_status.get(child_id)
        if st is None or st in _TERMINAL:
            continue
        child_task = ledger._tasks.get(child_id)
        if child_task is not None and not child_task.done():
            live.add(did)
    return live


def orphan_holders(
    ledger: CursorDispatchLedger,
    *,
    threshold_s: float | None = None,
    dead_run_grace_s: float | None = None,
    worker_instance: str | None = None,
    arming_timeout_s: float | None = None,
) -> list[str]:
    """``blocking_holders − live_holders`` — shared scan/reclaim predicate."""
    del worker_instance  # cross-instance orphans must be visible on every worker
    with ledger._connect() as conn:
        blocking = blocking_holders_conn(conn)
        child_status = _child_status_map(conn, blocking)
    live = live_holders(
        ledger,
        blocking,
        child_status,
        threshold_s=threshold_s,
        dead_run_grace_s=dead_run_grace_s,
        arming_timeout_s=arming_timeout_s,
    )
    return [r["dispatch_id"] for r in blocking if r["dispatch_id"] not in live]


def queue_stall_lease_keys(ledger: CursorDispatchLedger) -> list[str]:
    """Leases with ``queue_depth > 0`` and zero live blocking holders."""
    with ledger._connect() as conn:
        blocking = blocking_holders_conn(conn)
        child_status = _child_status_map(conn, blocking)
        queued_rows = conn.execute(
            "SELECT DISTINCT lease_key, source_repo FROM cursor_sdk_dispatches "
            "WHERE status='queued' AND COALESCE(read_only,0)=0 "
            "AND (lease_key IS NOT NULL OR source_repo IS NOT NULL)"
        ).fetchall()
        depth_by_key: dict[str, int] = {}
        for row in queued_rows:
            key = _resolve_key(
                lease_key=row["lease_key"], source_repo=row["source_repo"]
            )
            if not key:
                continue
            depth_row = conn.execute(
                "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches "
                "WHERE lease_key=? AND COALESCE(read_only,0)=0 AND status='queued'",
                (key,),
            ).fetchone()
            depth_by_key[key] = int(depth_row["n"]) if depth_row else 0
    live = live_holders(ledger, blocking, child_status)
    live_keys = {
        _resolve_key(lease_key=r["lease_key"], source_repo=r["source_repo"])
        for r in blocking
        if r["dispatch_id"] in live
        and _resolve_key(lease_key=r["lease_key"], source_repo=r["source_repo"])
    }
    return [key for key, depth in depth_by_key.items() if depth > 0 and key not in live_keys]


def _terminal_epoch(row: dict) -> float | None:
    """Wall time of predecessor terminal for reactor/watchdog grace."""
    record_json = str(row.get("record_json") or "")
    if record_json:
        try:
            data = json.loads(record_json)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            raw = data.get("hop_last_terminal_at")
            if isinstance(raw, (int, float)):
                return float(raw)
    terminal_at = row.get("terminal_at")
    if not terminal_at:
        return None
    try:
        dt = datetime.fromisoformat(str(terminal_at))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except ValueError:
        return None


def _successor_admitted(
    conn: sqlite3.Connection, *, predecessor_id: str, record_json: str
) -> bool:
    if hop_fields_from_record_json(record_json).get("hop_successor"):
        return True
    row = conn.execute(
        "SELECT 1 FROM cursor_sdk_dispatches "
        "WHERE json_extract(record_json, '$.hop_from') = ? LIMIT 1",
        (predecessor_id,),
    ).fetchone()
    return row is not None


def _latest_terminal_conductor_rows(
    conn: sqlite3.Connection,
) -> list[sqlite3.Row]:
    """Last terminal conductor row per worker thread (bind §5 last_terminal_row)."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _is_conductor_row,
    )

    rows = conn.execute(
        "SELECT * FROM cursor_sdk_dispatches "
        "WHERE status IN ('completed','failed','cancelled') "
        "AND thread_id IS NOT NULL AND thread_id != ''"
    ).fetchall()
    by_thread: dict[str, sqlite3.Row] = {}
    for row in rows:
        mapped = {k: row[k] for k in row.keys()}
        if not _is_conductor_row(mapped):
            continue
        thread_id = str(mapped.get("thread_id") or "")
        if not thread_id:
            continue
        existing = by_thread.get(thread_id)
        if existing is None:
            by_thread[thread_id] = row
            continue
        existing_map = {k: existing[k] for k in existing.keys()}
        existing_seq = hop_fields_from_record_json(
            str(existing_map.get("record_json") or "")
        ).get("hop_seq")
        row_seq = hop_fields_from_record_json(
            str(mapped.get("record_json") or "")
        ).get("hop_seq")
        existing_seq_i = int(existing_seq) if isinstance(existing_seq, int) else 0
        row_seq_i = int(row_seq) if isinstance(row_seq, int) else 0
        if row_seq_i > existing_seq_i:
            by_thread[thread_id] = row
            continue
        if row_seq_i < existing_seq_i:
            continue
        existing_ts = _terminal_epoch(existing_map) or 0.0
        row_ts = _terminal_epoch(mapped) or 0.0
        if row_ts >= existing_ts:
            by_thread[thread_id] = row
    return list(by_thread.values())


def conductor_hop_watchdog_candidates(
    ledger: CursorDispatchLedger,
    *,
    grace_s: float | None = None,
    now: float | None = None,
) -> list[str]:
    """Terminal conductor rows past reactor grace that still owe a hop successor."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _closeout_tokens_from_row,
        hop_owed,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        evaluate_hop_budget,
        load_hop_budget_config,
    )

    cfg = load_hop_budget_config()
    grace = grace_s if grace_s is not None else cfg.reactor_grace_s
    now_ts = now if now is not None else datetime.now(UTC).timestamp()
    candidates: list[str] = []
    with ledger._connect() as conn:
        for row in _latest_terminal_conductor_rows(conn):
            mapped = {k: row[k] for k in row.keys()}
            dispatch_id = str(mapped.get("dispatch_id") or "")
            record_json = str(mapped.get("record_json") or "")
            if not dispatch_id:
                continue
            if _successor_admitted(
                conn, predecessor_id=dispatch_id, record_json=record_json
            ):
                continue
            terminal_ts = _terminal_epoch(mapped)
            if terminal_ts is None or (now_ts - terminal_ts) < grace:
                continue
            closeout_tokens = _closeout_tokens_from_row(mapped)
            verdict = evaluate_hop_budget(mapped, closeout_tokens=closeout_tokens)
            if verdict.park and verdict.reason:
                candidates.append(dispatch_id)
                continue
            if hop_owed(mapped, closeout_tokens=closeout_tokens):
                candidates.append(dispatch_id)
    return candidates


def conductor_park_harvest_watchdog_candidates(
    ledger: CursorDispatchLedger,
    *,
    grace_s: float | None = None,
    now: float | None = None,
) -> list[str]:
    """Terminal conductor rows past grace with park_harvest still owed and unstamped."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _closeout_tokens_from_row,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        load_hop_budget_config,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
        park_harvest_owed,
    )

    cfg = load_hop_budget_config()
    grace = grace_s if grace_s is not None else cfg.reactor_grace_s
    now_ts = now if now is not None else datetime.now(UTC).timestamp()
    candidates: list[str] = []
    with ledger._connect() as conn:
        for row in _latest_terminal_conductor_rows(conn):
            mapped = {k: row[k] for k in row.keys()}
            dispatch_id = str(mapped.get("dispatch_id") or "")
            if not dispatch_id:
                continue
            terminal_ts = _terminal_epoch(mapped)
            if terminal_ts is None or (now_ts - terminal_ts) < grace:
                continue
            closeout_tokens = _closeout_tokens_from_row(mapped)
            if park_harvest_owed(mapped, closeout_tokens=closeout_tokens):
                candidates.append(dispatch_id)
    return candidates


async def transfer_capacity_after_park(
    *,
    parent_id: str,
    child_id: str,
    source_repo: str | None,
    nest_depth: int | None = None,
) -> None:
    """Move capacity from parked parent to nested child without waking waiters.

    Emits ``frontier.sdk.worker.lease.park_enter`` after a successful transfer.
    """
    await transfer_sdk_dispatch_slot(from_id=parent_id, to_id=child_id)
    emit_write_lease_park_enter(
        parent_id=parent_id,
        child_id=child_id,
        source_repo=source_repo,
        nest_depth=nest_depth,
    )


async def release_or_restore_for_child(*, dispatch_id: str) -> ReleaseDisposition:
    """Child capacity exit: restore parked parent via transfer, else release.

    A1: never wake FIFO waiters while a ``parked_waiting`` parent points at
    this child — transfer_holder(child→parent) instead.
    """
    ledger = CursorDispatchLedger.instance()
    parked = await asyncio.to_thread(
        ledger.find_parked_parent_for_child, child_id=dispatch_id
    )
    if parked is not None:
        parent_id, source_repo = parked
        try:
            await transfer_sdk_dispatch_slot(from_id=dispatch_id, to_id=parent_id)
        except Exception:
            # Child may already have released via worker finally; still restore ledger.
            logger.warning(
                "park restore transfer failed (may already be transferred): "
                "child=%s parent=%s",
                dispatch_id[:8],
                parent_id[:8],
                exc_info=True,
            )
        restored_repo = await asyncio.to_thread(
            ledger.restore_from_park, parent_id=parent_id
        )
        emit_write_lease_park_restore(
            parent_id=parent_id,
            child_id=dispatch_id,
            source_repo=restored_repo or source_repo,
        )
        return "restored"
    await force_release_sdk_dispatch_slot(dispatch_id=dispatch_id)
    return "released"


def release_or_restore_for_child_sync(
    loop: asyncio.AbstractEventLoop, *, dispatch_id: str
) -> ReleaseDisposition:
    """Worker-thread finally path for park-aware capacity exit (A1).

    Restores a parked parent via ``transfer_holder`` when present; otherwise
    performs an ordinary release on the owning event loop.
    """
    ledger = CursorDispatchLedger.instance()
    parked = ledger.find_parked_parent_for_child(child_id=dispatch_id)
    if parked is not None:
        parent_id, source_repo = parked
        try:
            transfer_sdk_dispatch_slot_sync(
                loop, from_id=dispatch_id, to_id=parent_id
            )
        except Exception:
            logger.warning(
                "park restore sync transfer failed: child=%s parent=%s",
                dispatch_id[:8],
                parent_id[:8],
                exc_info=True,
            )
        restored_repo = ledger.restore_from_park(parent_id=parent_id)
        emit_write_lease_park_restore(
            parent_id=parent_id,
            child_id=dispatch_id,
            source_repo=restored_repo or source_repo,
        )
        return "restored"
    fut = asyncio.run_coroutine_threadsafe(
        release_sdk_dispatch_slot(dispatch_id=dispatch_id), loop
    )
    fut.result(timeout=30.0)
    return "released"


async def reclaim_orphan_holder(
    ledger: CursorDispatchLedger, *, dispatch_id: str
) -> str | None:
    """Reap one orphan blocking holder; return lease_key for FIFO promotion."""
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id, status, lease_key, source_repo, "
            "park_child_dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None or row["status"] not in _BLOCKING_STATUSES:
        return None
    key = _resolve_key(lease_key=row["lease_key"], source_repo=row["source_repo"])
    if row["status"] == "parked_waiting":
        child_id = row["park_child_dispatch_id"]
        if child_id:
            with ledger._connect() as conn:
                child = conn.execute(
                    "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                    (child_id,),
                ).fetchone()
            if child is not None and child["status"] not in _TERMINAL:
                child_row = load_ledger_row(ledger, dispatch_id=child_id)
                if child_row is not None:
                    execution_id = child_row.execution_id or child_id
                    emit_sdk_worker_orphaned(
                        dispatch_id=child_id,
                        thread_id=child_row.thread_id,
                        execution_id=execution_id,
                        resolved_model=child_row.resolved_model,
                        timeout_s=0.0,
                        bridge_aborted=False,
                    )
                await asyncio.to_thread(
                    ledger.mark_terminal,
                    dispatch_id=child_id,
                    terminal_status="failed",
                )
            await release_or_restore_for_child(dispatch_id=child_id)
        elif ledger.restore_from_park(parent_id=dispatch_id) is None:
            parent_row = load_ledger_row(ledger, dispatch_id=dispatch_id)
            if parent_row is not None:
                execution_id = parent_row.execution_id or dispatch_id
                emit_sdk_worker_orphaned(
                    dispatch_id=dispatch_id,
                    thread_id=parent_row.thread_id,
                    execution_id=execution_id,
                    resolved_model=parent_row.resolved_model,
                    timeout_s=0.0,
                    bridge_aborted=False,
                )
            await asyncio.to_thread(
                ledger.mark_terminal,
                dispatch_id=dispatch_id,
                terminal_status="failed",
            )
        return key
    await force_release_sdk_dispatch_slot(dispatch_id=dispatch_id)
    return await asyncio.to_thread(
        ledger.release_stale_writer, dispatch_id=dispatch_id, force=True
    )
