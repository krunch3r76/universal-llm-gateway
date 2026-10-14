"""Every conductor stop is a harness service, not a human page.

``ROW_HOP`` is the hop reactor. ``CONSULT_PENDING`` and harvest parks are
the continue services. ``DONE`` closes the lane when the closeout lands.
``HOLD_MERGE`` and ``OPERATOR_GATE`` are the explicit human stops.

``PARKED_TRANSPORT`` with ``hop_budget_admit_retry_cap`` is this module:
one more admit once the worker thread is empty. It does not page.
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    PARK_REASON_ADMIT_RETRY_CAP,
    HopBudgetVerdict,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_inspection import (
    REASON_INSPECTION_DEFERRED,
    REASON_INSPECTION_REQUESTED,
    REASON_INSPECTION_UNPROVABLE,
    signature_key_for,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress import (
    progress_signature_for_row,
    record_data,
)

STOP_SERVICE = {
    "ROW_HOP": "hop_reactor",
    "CONSULT_PENDING": "consult_continue",
    "PARKED_TRANSPORT": "park_service",
    "ROW_PINNED": "holds_until_explicit_gate",
    "HOLD_MERGE": "explicit_human",
    "OPERATOR_GATE": "explicit_human",
    "CONFIRM_PENDING": "explicit_human",
    "DONE": "close_lane",
}


def _record(row: dict[str, Any]) -> dict[str, Any]:
    data = record_data(str(row.get("record_json") or ""))
    return data if isinstance(data, dict) else {}


def admit_retry_park_unserviced(row: dict[str, Any]) -> bool:
    """True when an admit-retry park still owes one service hop."""
    record = _record(row)
    if record.get("hop_parked") is not True:
        return False
    if str(record.get("hop_park_reason") or "") != PARK_REASON_ADMIT_RETRY_CAP:
        return False
    return record.get("hop_park_serviced") is not True


def budget_park_pages(reason: str) -> bool:
    """Admit-retry is serviced by the harness. Other budget parks still page."""
    return reason != PARK_REASON_ADMIT_RETRY_CAP


HOP_INSPECTION_PAGED_FOR_KEY = "hop_inspection_paged_for"
HOP_INSPECTION_EMITTED_FOR_KEY = "hop_inspection_emitted_for"


async def apply_hop_budget_announcer(
    row: dict[str, Any],
    verdict: HopBudgetVerdict,
) -> bool:
    """Page/emit once per inspection window; park loop rows.

    Returns True when the caller may proceed to admit a successor.
    """
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park import (
        park_conductor_hop_mission,
    )
    from services.git_integration_worker.cursor_sdk_hop_events import (
        emit_frontier_sdk_conductor_hop_inspection_deferred,
        emit_frontier_sdk_conductor_hop_inspection_requested,
        emit_frontier_sdk_conductor_hop_inspection_unprovable,
    )

    if verdict.park and verdict.reason:
        await park_conductor_hop_mission(row, reason=verdict.reason)
        return False
    if verdict.reason == REASON_INSPECTION_DEFERRED:
        emit_frontier_sdk_conductor_hop_inspection_deferred(
            dispatch_id=str(row.get("dispatch_id") or ""),
            thread_id=str(row.get("thread_id") or ""),
            work_key=str(row.get("work_key") or "") or None,
            hop_seq=_hop_seq(row),
            error_class="operational_error",
        )
        return False
    if verdict.reason == REASON_INSPECTION_UNPROVABLE:
        _emit_inspection_once(
            row,
            outcome="unprovable",
            emit_fn=lambda key: emit_frontier_sdk_conductor_hop_inspection_unprovable(
                dispatch_id=str(row.get("dispatch_id") or ""),
                thread_id=str(row.get("thread_id") or ""),
                work_key=str(row.get("work_key") or "") or None,
                hop_seq=_hop_seq(row),
                signature_key=key,
            ),
        )
        return True
    if verdict.reason == REASON_INSPECTION_REQUESTED:
        key = signature_key_for(progress_signature_for_row(row))
        await _page_inspection_once(row, signature_key=key)
        _emit_inspection_once(
            row,
            outcome="requested",
            emit_fn=lambda k: emit_frontier_sdk_conductor_hop_inspection_requested(
                dispatch_id=str(row.get("dispatch_id") or ""),
                thread_id=str(row.get("thread_id") or ""),
                work_key=str(row.get("work_key") or "") or None,
                hop_seq=_hop_seq(row),
                signature_key=k,
            ),
            signature_key=key,
        )
        return True
    return True


def _hop_seq(row: dict[str, Any]) -> int:
    from services.git_integration_worker.cursor_sdk_ledger_hop import (
        hop_fields_from_record_json,
    )

    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    hop_seq = hop_fields.get("hop_seq")
    return int(hop_seq) if isinstance(hop_seq, int) else 0


def _emit_inspection_once(
    row: dict[str, Any],
    *,
    outcome: str,
    emit_fn,
    signature_key: str | None = None,
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    dispatch_id = str(row.get("dispatch_id") or "")
    record = _record(row)
    emitted = record.get(HOP_INSPECTION_EMITTED_FOR_KEY)
    if isinstance(emitted, dict):
        prior = emitted.get(outcome)
        key = signature_key or signature_key_for(progress_signature_for_row(row))
        if prior == key:
            return
    key = signature_key or signature_key_for(progress_signature_for_row(row))
    emit_fn(key)
    patch = dict(record)
    emitted_map = dict(emitted) if isinstance(emitted, dict) else {}
    emitted_map[outcome] = key
    patch[HOP_INSPECTION_EMITTED_FOR_KEY] = emitted_map
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id=dispatch_id,
        patch=patch,
    )


async def _page_inspection_once(row: dict[str, Any], *, signature_key: str) -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park import (
        page_hop_budget_parked,
    )

    record = _record(row)
    if record.get(HOP_INSPECTION_PAGED_FOR_KEY) == signature_key:
        return
    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    work_key = str(row.get("work_key") or "")
    await page_hop_budget_parked(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        reason=REASON_INSPECTION_REQUESTED,
        work_key=work_key,
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    patch = dict(record)
    patch[HOP_INSPECTION_PAGED_FOR_KEY] = signature_key
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id=dispatch_id,
        patch=patch,
    )
