"""GIW closeout-memo outbox: claim, suppress, POST, backoff. Never raises."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

from closeout_memo.client import post_closeout_memo
from closeout_memo.events import emit_closeout_memo
from closeout_memo.models import CloseoutMemoRequest, coerce_status

logger = logging.getLogger(__name__)

_BACKOFF_S = (30, 60, 120, 300)
_GAVE_UP_AFTER = 8
_THREAD_RE = re.compile(r"^\d{1,10}$")
_GATE_RE = re.compile(r"[a-z_]{1,40}")

# Direct mark_terminal callers covered by the owed UPDATE + this sweep
# (spec §5.1 step 2). The fast path in _mark_terminal_and_promote does not
# run for these:
#   cursor_dispatch_ledger.py claim/cancel terminals
#   cursor_sdk_await_reply.py
#   cursor_sdk_park.py (two sites)
#   routes/cursor_sdk_startup_reconcile.py


def record_memo_closeout(dispatch_id: str, facts: dict[str, Any]) -> None:
    """Merge pointer facts into record_json. Failure does not change terminal."""
    try:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        CursorDispatchLedger.instance().merge_record_json(
            dispatch_id=dispatch_id,
            patch={"memo_closeout": facts},
        )
    except Exception:  # noqa: BLE001 — AC-10
        logger.exception("memo facts merge failed dispatch_id=%s", dispatch_id)


def _record(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("record_json") or "{}"
    try:
        data = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def classify_memo(
    row: dict[str, Any], rec: dict[str, Any]
) -> tuple[str, str, str, str | None]:
    """Return kind, status, next, suppress_reason. suppress_reason set ⇒ no POST."""
    memo = rec.get("memo_closeout") if isinstance(rec.get("memo_closeout"), dict) else {}
    contract = str(row.get("contract") or rec.get("handoff_contract") or "unknown")
    terminal = str(row.get("terminal_status") or "")
    wake = str(memo.get("wake") or "")
    actor = str(memo.get("actor") or "")
    wake_lane = str(row.get("wake_lane") or "")
    hop_successor = str(rec.get("hop_successor") or "").strip()
    hop_parked = bool(rec.get("hop_parked"))
    deferral = str(rec.get("hop_deferral_gate") or "")
    emit_tag = str(memo.get("emit_tag") or "")
    closeout_status = str(memo.get("closeout_status") or "")
    if not closeout_status:
        if terminal == "failed":
            closeout_status = "delivery_failed"
        elif terminal == "cancelled":
            closeout_status = "discarded"
        elif terminal in ("completed", "ok"):
            closeout_status = "completed"
    is_conductor = contract == "conductor"
    nxt = "none"
    if hop_successor:
        nxt = f"hop_admitted:{hop_successor}"
    elif deferral:
        match = _GATE_RE.search(deferral)
        if match:
            nxt = f"hop_skipped:{match.group(0)}"
    if is_conductor and hop_successor:
        return "conductor_stop", "completed", nxt, "hop_successor"
    if wake.startswith("giw_restart:"):
        return "sdk_parked", "parked", nxt, "giw_restart"
    if actor and actor == wake_lane and (
        "DISCARDED" in emit_tag or closeout_status == "discarded"
    ):
        return "sdk_discarded", "discarded", nxt, "operator_discard"
    if hop_parked or (is_conductor and closeout_status == "parked"):
        return "conductor_parked", "parked", nxt, None
    if "DISCARDED" in emit_tag or closeout_status == "discarded":
        return "sdk_discarded", "discarded", nxt, None
    if "PARKED" in emit_tag or closeout_status == "parked":
        return "sdk_parked", "parked", nxt, None
    if is_conductor:
        status = coerce_status(closeout_status or "completed")
        return "conductor_stop", status, nxt, None
    if "DELIVERY_FAILED" in emit_tag or closeout_status == "delivery_failed":
        return "sdk_failed", "delivery_failed", nxt, None
    if closeout_status == "refused" or "REFUSED" in emit_tag:
        return "sdk_closeout", "refused", nxt, None
    return "sdk_closeout", coerce_status(closeout_status or "completed"), nxt, None


def build_request(row: dict[str, Any]) -> CloseoutMemoRequest | None:
    """Structured request from the ledger row. None when the row cannot memo."""
    wake_lane = str(row.get("wake_lane") or "")
    if not _THREAD_RE.fullmatch(wake_lane):
        return None
    rec = _record(row)
    kind, status, nxt, suppress = classify_memo(row, rec)
    if suppress:
        return None
    memo = rec.get("memo_closeout") if isinstance(rec.get("memo_closeout"), dict) else {}
    worker = str(row.get("thread_id") or "")
    dispatch_thread = str(rec.get("parent_dispatch_thread_id") or worker)
    if not _THREAD_RE.fullmatch(worker):
        return None
    if not _THREAD_RE.fullmatch(dispatch_thread):
        dispatch_thread = worker
    turn = memo.get("turn") if memo.get("turn") not in (None, "") else "latest"
    if isinstance(turn, str) and turn.isdigit():
        turn = int(turn)
    sidecar = memo.get("sidecar_ref") or memo.get("sidecar")
    contract = str(row.get("contract") or rec.get("handoff_contract") or "unknown")
    dispatch_id = str(row.get("dispatch_id") or "")
    execution_id = str(row.get("execution_id") or "") or None
    return CloseoutMemoRequest(
        memo_key=f"giw:{dispatch_id}",
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        contract=contract,
        wake_lane=wake_lane,
        worker_thread=worker,
        dispatch_thread=dispatch_thread,
        turn=turn,
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        sidecar=sidecar if isinstance(sidecar, str) else None,
        next=nxt,
        emitted_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


async def try_emit(dispatch_id: str) -> None:
    """Claim an owed row and POST, or record suppression. Never raises."""
    try:
        await _try_emit(dispatch_id)
    except Exception:  # noqa: BLE001 — AC-10
        logger.exception("closeout memo emit failed dispatch_id=%s", dispatch_id)
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        row = CursorDispatchLedger.instance().memo_row(dispatch_id)
        if row is not None and row.get("memo_state") == "claimed":
            _reschedule_after_crash(dispatch_id)


def _reschedule_after_crash(dispatch_id: str) -> None:
    """Return a claimed row to owed so the sweep can retry. Terminal stays."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ledger = CursorDispatchLedger.instance()
    row = ledger.memo_row(dispatch_id)
    attempts = int((row or {}).get("memo_attempts") or 0) + 1
    if attempts >= _GAVE_UP_AFTER:
        ledger.finish_memo(dispatch_id, state="emitted", attempts=attempts)
        emit_closeout_memo(
            "producer_gave_up", dispatch_id=dispatch_id, attempts=attempts
        )
        return
    delay = _BACKOFF_S[min(attempts - 1, len(_BACKOFF_S) - 1)]
    ledger.finish_memo(
        dispatch_id,
        state="owed",
        next_at=time.time() + delay,
        attempts=attempts,
    )


async def _try_emit(dispatch_id: str) -> bool:
    """Return True once the row is claimed. Callers reschedule on raise."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ledger = CursorDispatchLedger.instance()
    if not ledger.claim_memo(dispatch_id):
        return False
    row = ledger.memo_row(dispatch_id)
    if row is None:
        return True
    rec = _record(row)
    _kind, _status, _nxt, suppress = classify_memo(row, rec)
    if suppress:
        ledger.finish_memo(dispatch_id, state="suppressed")
        emit_closeout_memo(
            "suppressed",
            dispatch_id=dispatch_id,
            reason=suppress,
            wake_lane=row.get("wake_lane"),
        )
        return True
    request = build_request(row)
    if request is None:
        ledger.finish_memo(dispatch_id, state="suppressed")
        emit_closeout_memo(
            "field_rejected",
            dispatch_id=dispatch_id,
            field="wake_lane_or_thread",
        )
        return True
    for field_name in request.rejected_fields:
        emit_closeout_memo(
            "field_rejected", memo_key=request.memo_key, field=field_name
        )
    result = await post_closeout_memo(request)
    if result.accepted:
        ledger.finish_memo(dispatch_id, state="emitted")
        return True
    attempts = int(row.get("memo_attempts") or 0) + 1
    if attempts >= _GAVE_UP_AFTER:
        ledger.finish_memo(dispatch_id, state="emitted", attempts=attempts)
        emit_closeout_memo(
            "producer_gave_up",
            dispatch_id=dispatch_id,
            memo_key=request.memo_key,
            attempts=attempts,
            error=result.error,
        )
        return True
    delay = _BACKOFF_S[min(attempts - 1, len(_BACKOFF_S) - 1)]
    ledger.finish_memo(
        dispatch_id,
        state="owed",
        next_at=time.time() + delay,
        attempts=attempts,
    )
    return True


async def sweep_due() -> int:
    """Claim every owed row whose next_at has arrived. Returns the count seen."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ids = CursorDispatchLedger.instance().list_owed_memos(now=time.time())
    for dispatch_id in ids:
        await try_emit(dispatch_id)
    return len(ids)


async def closeout_memo_sweep_loop(app: Any) -> None:
    """60s sweep. Covers bypass mark_terminal callers and restarts."""
    while not getattr(app.state, "shutting_down", False):
        try:
            await sweep_due()
        except Exception:  # noqa: BLE001
            logger.exception("closeout memo sweep failed")
        await asyncio.sleep(60)
