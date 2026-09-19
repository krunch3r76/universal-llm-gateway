"""Conductor hop watchdog sweep (todo:conductor-hop-reactor R7).

Periodic GIW sweep — sibling cadence to ``orphan_holders`` / ``queue_stall_lease_keys``.
Fires when the reactor did not admit within ``CONDUCTOR_HOP_REACTOR_GRACE_S``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    _closeout_tokens_from_row,
    _emit_hop_skipped,
    _is_conductor_row,
    _load_row,
    _mission_park_blocks_hop,
    build_hop_team_dispatch_body,
    hop_owed,
    post_conductor_hop_team_dispatch,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    HOP_LAST_TERMINAL_AT_KEY,
    PARK_REASON_ADMIT_RETRY_CAP,
    evaluate_hop_budget,
    load_hop_budget_config,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park import (
    park_conductor_hop_mission,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    fire_park_harvest_continue,
    maybe_fire_conductor_park_harvest,
    park_harvest_continue_owed,
    park_harvest_owed,
)
from services.git_integration_worker.cursor_sdk_hop_events import (
    emit_frontier_sdk_conductor_hop_admit_failed,
    emit_frontier_sdk_conductor_hop_admitted,
    emit_frontier_sdk_conductor_hop_watchdog_fired,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
    merge_hop_patch,
)
from services.git_integration_worker.cursor_sdk_park import (
    conductor_hop_watchdog_candidates,
    conductor_park_harvest_continue_candidates,
    conductor_park_harvest_watchdog_candidates,
)

logger = get_logger(__name__)
SKIP_GATE_ADMIT_ERROR_PERMANENT = "admit_error_permanent"


def _write_record_json(dispatch_id: str, record_json: str) -> None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (record_json, dispatch_id),
        )


def _admit_error_permanent(row: dict[str, Any]) -> bool:
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    admit_err = hop_fields.get("hop_admit_error")
    if not isinstance(admit_err, dict):
        return False
    if admit_err.get("retryable") is not False:
        return False
    return int(admit_err.get("attempts") or 0) >= 1


def _backoff_elapsed(row: dict[str, Any], *, backoff_s: float) -> bool:
    if backoff_s <= 0:
        return True
    rec = json.loads(str(row.get("record_json") or "") or "{}")
    if not isinstance(rec, dict):
        return True
    terminal_at = rec.get(HOP_LAST_TERMINAL_AT_KEY)
    if not isinstance(terminal_at, (int, float)):
        return True
    return (time.time() - float(terminal_at)) >= backoff_s


async def _stamp_admit_outcome(
    *,
    dispatch_id: str,
    row: dict[str, Any],
    closeout_tokens: frozenset[str],
    hop_reason: str,
    ok: bool,
    detail: dict[str, Any],
    emit_watchdog: bool,
) -> bool:
    """Apply ledger + event side effects for one hop admit attempt."""
    thread_id = str(row.get("thread_id") or "")
    record_json = str(row.get("record_json") or "")
    body = build_hop_team_dispatch_body(row, hop_reason_override=hop_reason)
    hop_seq = int((body or {}).get("hop_seq") or 1)
    if ok:
        successor = (
            str(detail.get("dispatch_id") or "")
            or str(detail.get("execution_id") or "")
        )
        if not successor:
            logger.warning(
                "conductor hop watchdog admit ok but no successor id "
                "dispatch_id=%s detail=%s",
                dispatch_id,
                detail,
            )
            return False
        merged = merge_hop_patch(record_json, {"hop_successor": successor})
        _write_record_json(dispatch_id, merged)
        emit_frontier_sdk_conductor_hop_admitted(
            predecessor_dispatch_id=dispatch_id,
            successor_dispatch_id=successor,
            thread_id=thread_id,
            hop_seq=hop_seq,
            hop_reason=hop_reason,
        )
        if emit_watchdog:
            emit_frontier_sdk_conductor_hop_watchdog_fired(
                last_dispatch_id=dispatch_id,
                thread_id=thread_id,
                hop_seq=hop_seq,
            )
        return True
    error_text = json.dumps(detail, sort_keys=True)[:500]
    merged = merge_hop_patch(
        record_json,
        {
            "hop_admit_error": {
                "last_error": error_text,
                "last_status_code": detail.get("status_code"),
            }
        },
    )
    _write_record_json(dispatch_id, merged)
    emit_frontier_sdk_conductor_hop_admit_failed(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=hop_seq,
        hop_reason=hop_reason,
        error=error_text,
        status_code=detail.get("status_code"),
    )
    return False


async def maybe_fire_conductor_hop_watchdog(*, dispatch_id: str) -> bool:
    """Retry one owed hop or park-harvest after reactor grace (bind §5)."""
    row = _load_row(dispatch_id)
    if row is None or not _is_conductor_row(row):
        return False
    closeout_tokens = _closeout_tokens_from_row(row)
    if _mission_park_blocks_hop(row):
        return False
    if park_harvest_continue_owed(row, closeout_tokens=closeout_tokens):
        return await fire_park_harvest_continue(row)
    if park_harvest_owed(row, closeout_tokens=closeout_tokens):
        return await maybe_fire_conductor_park_harvest(dispatch_id=dispatch_id)
    if _admit_error_permanent(row):
        _emit_hop_skipped(row, gate=SKIP_GATE_ADMIT_ERROR_PERMANENT)
        return False
    verdict = evaluate_hop_budget(row, closeout_tokens=closeout_tokens)
    if verdict.park and verdict.reason:
        await park_conductor_hop_mission(row, reason=verdict.reason)
        return False
    if not hop_owed(row, closeout_tokens=closeout_tokens):
        return False
    if not _backoff_elapsed(row, backoff_s=verdict.backoff_s):
        return False
    body = build_hop_team_dispatch_body(row, hop_reason_override="watchdog")
    if body is None or "dispatch_thread_id" not in body:
        return False
    ok, detail = await post_conductor_hop_team_dispatch(body)
    if ok:
        return await _stamp_admit_outcome(
            dispatch_id=dispatch_id,
            row=row,
            closeout_tokens=closeout_tokens,
            hop_reason="watchdog",
            ok=ok,
            detail=detail,
            emit_watchdog=True,
        )
    status_code = detail.get("status_code")
    stamped = await _stamp_admit_outcome(
        dispatch_id=dispatch_id,
        row=row,
        closeout_tokens=closeout_tokens,
        hop_reason="watchdog",
        ok=False,
        detail=detail,
        emit_watchdog=False,
    )
    row = _load_row(dispatch_id) or row
    if _admit_error_permanent(row):
        _emit_hop_skipped(row, gate=SKIP_GATE_ADMIT_ERROR_PERMANENT)
        return False
    if status_code is not None and int(status_code) >= 500:
        cfg = load_hop_budget_config()
        hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
        admit_err = hop_fields.get("hop_admit_error")
        attempts = (
            int(admit_err.get("attempts") or 0)
            if isinstance(admit_err, dict)
            else 0
        )
        if attempts >= cfg.crash_cap_per_row:
            await park_conductor_hop_mission(
                row, reason=PARK_REASON_ADMIT_RETRY_CAP
            )
    return stamped


async def sweep_conductor_hop_watchdog(
    ledger: CursorDispatchLedger | None = None,
) -> int:
    """One watchdog pass inside ``reconcile_stale_leases``; return admit count."""
    ledger = ledger or CursorDispatchLedger.instance()
    continue_candidates = await asyncio.to_thread(
        conductor_park_harvest_continue_candidates, ledger
    )
    hop_candidates = await asyncio.to_thread(conductor_hop_watchdog_candidates, ledger)
    park_candidates = await asyncio.to_thread(
        conductor_park_harvest_watchdog_candidates, ledger
    )
    candidates = list(
        dict.fromkeys([*continue_candidates, *park_candidates, *hop_candidates])
    )
    fired = 0
    for dispatch_id in candidates:
        try:
            if await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id):
                fired += 1
        except Exception:
            logger.warning(
                "conductor hop watchdog fire failed dispatch_id=%s",
                dispatch_id,
                exc_info=True,
            )
    return fired


__all__ = [
    "SKIP_GATE_ADMIT_ERROR_PERMANENT",
    "maybe_fire_conductor_hop_watchdog",
    "sweep_conductor_hop_watchdog",
]
