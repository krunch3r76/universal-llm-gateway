"""Finalize a cursor-sdk dispatch whose run was cancelled for a park (spec D6).

Runs on the gated coroutine after the worker thread unwinds from the bridge
``CancelRun``. Order matters and is the whole contract:

1. partial harvest sidecar (``park_partial_uri``) — what the run did so far;
2. ledger ``mark_parked`` — terminal ``cancelled`` + ``park_*`` columns +
   ``resume_retain`` in one UPDATE (the tree and HOME must be retained
   *before* the terminal path's prune runs);
3. PARKED bus turn on the worker thread — conductor rows carry the literal
   ``PARKED_TRANSPORT wake=giw_restart:{intent_id}`` line so the crash-cap
   grades the row as a designed stop, and that body is merged as closeout
   authority before terminal;
4. ``_mark_terminal_and_promote(terminal_status="cancelled")`` — closes the
   admission ticket (drain convergence), releases capacity, prunes nothing
   (retain), and fires the hop reactor which skips (exit-persist token).

The thread/dispatch link is **not** terminated: the resume child inherits
``execution_id`` and ``thread_id``, so the caller's ``poll_hint`` continues
until the child's terminal (or ``park_expired``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout.bridge_death_harvest import (
    emit_partial_harvest_on_park,
)
from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
    SdkRunOutcome,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    merge_conductor_closeout_hop_authority,
)
from services.git_integration_worker.cursor_sdk_closeout_trigger import (
    extract_turn_number,
)
from services.git_integration_worker.cursor_sdk_conductor_conflict import (
    _record_packet_kind,
)
from services.git_integration_worker.cursor_sdk_park_events import (
    emit_sdk_park_parked,
)
from services.git_integration_worker.cursor_sdk_park_for_restart import (
    ParkMark,
    clear_park_mark,
)
from services.git_integration_worker.cursor_sdk_park_ledger import mark_parked
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

logger = get_logger(__name__)

PARKED_EMIT_TAG = "CURSOR_SDK_PARKED"


def _tool_summary(
    outcome: SdkRunOutcome | None, exc: BaseException | None
) -> tuple[int, list[dict[str, str]]]:
    """Tool-call count and last calls from the outcome or the abort forensics."""
    if outcome is not None:
        last = [
            {"tool_name": tc.tool_name, "status": tc.status}
            for tc in list(outcome.tool_calls or ())[-3:]
        ]
        return int(outcome.tool_call_count or 0), last
    forensics = getattr(exc, "forensics", None)
    if isinstance(forensics, dict):
        raw_last = forensics.get("last_tool_calls")
        last = [
            {
                "tool_name": str(t.get("tool_name", "")),
                "status": str(t.get("status", "")),
            }
            for t in (raw_last if isinstance(raw_last, list) else [])
            if isinstance(t, dict)
        ]
        return int(forensics.get("stream_tool_call_count") or 0), last
    return 0, []


def _is_conductor_row(dispatch_id: str, contract: str | None) -> bool:
    if (contract or "").lower() == "conductor":
        return True
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return False
    return _record_packet_kind(str(row["record_json"] or "")) == "conductor"


def parked_wake_line(intent_id: str | None) -> str:
    """The substrate-written wake record for a conductor parked by GIW."""
    return f"PARKED_TRANSPORT wake=giw_restart:{intent_id or 'none'}"


def build_parked_body(
    *,
    dispatch_id: str,
    mark: ParkMark,
    park: dict[str, Any],
    sidecar_uri: str | None,
    conductor: bool,
) -> str:
    """PARKED turn body: JSON envelope, plus designed-stop lines for conductors."""
    payload = {
        "status": "parked",
        "park": park,
        "resume_of": dispatch_id,
        "sidecar_ref": sidecar_uri,
        "wake": f"giw_restart:{mark.intent_id or 'none'}",
    }
    lines: list[str] = []
    if conductor:
        lines.append("stop: PARKED_TRANSPORT")
        lines.append(parked_wake_line(mark.intent_id))
        lines.append("")
    lines.append("```json")
    lines.append(json.dumps(payload, indent=2, sort_keys=True))
    lines.append("```")
    return "\n".join(lines)


async def finalize_parked(
    *,
    req: CursorDispatchRequest,
    source_repo: Path,
    bus: CursorBusClient,
    reply_to: str,
    controller: Any,
    mark: ParkMark,
    outcome: SdkRunOutcome | None,
    exc: BaseException | None,
) -> None:
    """Terminal path for a park-cancelled run (see module docstring for order)."""
    from services.git_integration_worker.routes.cursor_sdk import (
        _mark_terminal_and_promote,
    )

    del source_repo  # the park never touches the tree; retention keeps it pinned
    dispatch_id = req.dispatch_id
    tool_call_count, last_tools = _tool_summary(outcome, exc)
    harvest = await asyncio.to_thread(
        emit_partial_harvest_on_park,
        dispatch_id,
        thread_id=req.thread_id,
        intent_id=mark.intent_id,
        method=mark.method,
        tool_call_count=tool_call_count,
        last_tools=last_tools,
    )
    sidecar_uri = harvest.get("sidecar_uri")
    park_row = await asyncio.to_thread(
        mark_parked,
        dispatch_id=dispatch_id,
        intent_id=mark.intent_id,
        drain_epoch=mark.drain_epoch,
        actor=mark.actor,
        reason=mark.reason,
        requested_at=mark.requested_at,
        method=mark.method,
        tool_call_count=tool_call_count,
        last_tool_calls=last_tools,
        sidecar_uri=sidecar_uri,
    )
    park = park_row.park if park_row is not None else {}
    conductor = await asyncio.to_thread(
        _is_conductor_row, dispatch_id, req.handoff_contract
    )
    body = build_parked_body(
        dispatch_id=dispatch_id,
        mark=mark,
        park=park,
        sidecar_uri=sidecar_uri,
        conductor=conductor,
    )
    bus_result = await bus.reply(
        thread_id=req.thread_id,
        to_agent=reply_to,
        from_agent="cursor-sdk",
        subject=(
            f"cursor-sdk dispatch {dispatch_id} PARKED "
            f"(for GIW restart {mark.intent_id or 'none'})"
        ),
        body=body,
    )
    if bus_result.status_code >= 400:
        logger.error(
            "cursor-sdk PARKED turn post failed dispatch_id=%s status=%s body=%s",
            dispatch_id,
            bus_result.status_code,
            bus_result.body,
        )
    if conductor:
        await asyncio.to_thread(
            merge_conductor_closeout_hop_authority,
            dispatch_id=dispatch_id,
            closeout_body=body,
            thread_id=req.thread_id,
            closeout_turn=extract_turn_number(bus_result.body),
        )
    emit_sdk_park_parked(
        dispatch_id=dispatch_id,
        thread_id=req.thread_id,
        intent_id=mark.intent_id,
        method=mark.method,
        tool_call_count=tool_call_count,
        sidecar_uri=sidecar_uri,
        sdk_agent_id_present=bool(park_row is not None and park_row.sdk_agent_id),
    )
    logger.warning(
        "cursor-sdk dispatch parked dispatch_id=%s intent_id=%s method=%s "
        "tool_calls=%s sidecar=%s",
        dispatch_id,
        mark.intent_id,
        mark.method,
        tool_call_count,
        sidecar_uri,
    )
    try:
        await _mark_terminal_and_promote(
            dispatch_id=dispatch_id,
            terminal_status="cancelled",
            controller=controller,
            emit_tag=PARKED_EMIT_TAG,
        )
    finally:
        clear_park_mark(dispatch_id)


__all__ = [
    "PARKED_EMIT_TAG",
    "build_parked_body",
    "finalize_parked",
    "parked_wake_line",
]
