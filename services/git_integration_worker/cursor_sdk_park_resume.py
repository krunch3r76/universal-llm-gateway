"""Auto-resume parked cursor-sdk dispatches after a GIW restart (spec D5).

Recovery for park state belongs to the authority that holds the rows: GIW's
own ``startup_ledger_reconcile`` (and the periodic ``reconcile_stale_leases``
tick for rows refused on the first pass) walks ``open_park_rows`` oldest-first
and re-admits each as an ordinary ``resume_of`` child through the same
admission path the POST route uses — lane binding (S1 re-pin), write lease,
work-key identity, resume eligibility all apply; N parked rows re-enter under
the existing concurrency cap, no herd.

The child inherits the parent's identity surface (``thread_id``,
``execution_id``, ``caller_agent``, model + knobs, packet/message, contract,
skills, lane/worktree, ``work_key``) and carries a versioned PARK-RESUME
preamble ahead of the unchanged packet so the agent continues rather than
restarts. Expiry (``CURSOR_SDK_PARK_AUTO_RESUME_TTL_S``) only stops automatic
re-admission; manual ``team_dispatch(resume_of=…)`` stays possible until the
resume-retain TTL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import merge_hop_patch
from services.git_integration_worker.cursor_sdk_packet import _IMPLEMENT_PREAMBLE
from services.git_integration_worker.cursor_sdk_park_events import (
    emit_sdk_park_expired,
    emit_sdk_park_resume_admitted,
    emit_sdk_park_resume_refused,
)
from services.git_integration_worker.cursor_sdk_park_ledger import (
    ParkRow,
    bump_resume_attempt,
    mark_park_expired,
    mark_park_resumed,
    open_park_rows,
    park_auto_resume_ttl_s,
    record_resume_refusal,
)
from services.git_integration_worker.cursor_sdk_resume import (
    resume_eligibility_reason,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

logger = get_logger(__name__)

PARK_RESUME_PREAMBLE_VERSION = 1
ADMITTED_VIA_PARK_RESUME = "giw_park_resume"

_PREAMBLE_TEMPLATE = (
    "PARK-RESUME v{version} — substrate notice, not operator prose.\n"
    "You are the same agent as dispatch {parent} on agent-bus:{thread_id}. GIW parked "
    "you at {parked_at} for restart intent {intent_id} ({reason}); the substrate is "
    "back at code_version {code_version}.\n"
    "Your run was cancelled after {tool_call_count} tool calls; last tool calls: "
    "{last_tool_calls}.\n"
    "Treat the last one as NOT completed unless you verify its effect. Partial "
    "harvest: {sidecar_uri}.\n"
    "Continue from your own CHECKPOINT/journal; do not redo completed writes; do "
    "not restart the task.\n"
    "The packet below is unchanged from your original brief."
)


def render_park_resume_preamble(row: ParkRow, *, code_version: str) -> str:
    """Versioned PARK-RESUME notice prepended to the child's prompt."""
    park = row.park
    last_calls = park.get("last_tool_calls") or []
    rendered_calls = (
        ", ".join(
            f"{c.get('tool_name', '?')}[{c.get('status', '?')}]"
            for c in last_calls
            if isinstance(c, dict)
        )
        or "none observed"
    )
    return _PREAMBLE_TEMPLATE.format(
        version=PARK_RESUME_PREAMBLE_VERSION,
        parent=row.dispatch_id,
        thread_id=row.thread_id,
        parked_at=row.parked_at or park.get("parked_at") or "unknown",
        intent_id=row.park_intent_id or park.get("intent_id") or "none",
        reason=park.get("reason") or "restart",
        code_version=code_version,
        tool_call_count=park.get("tool_call_count", 0),
        last_tool_calls=rendered_calls,
        sidecar_uri=park.get("sidecar_uri") or "none",
    )


def child_dispatch_id(row: ParkRow, *, attempt: int) -> str:
    """Deterministic per-attempt child id so a retried admit hits ledger idempotency."""
    return f"{row.dispatch_id}-r{attempt}"


def build_park_resume_request(
    row: ParkRow, *, attempt: int, code_version: str
) -> CursorDispatchRequest:
    """Mint the ``resume_of`` child request from the parent's durable record.

    Same builder shape as ``CursorDispatchLedger.load_promoted_request``: the
    wire fields come back out of ``record_json``; identity columns
    (``execution_id``, ``work_key``, ``source_ref``) are inherited so the
    caller's ``poll_hint`` and the work-identity gate see one lineage.
    """
    record = row.record
    contract = str(record.get("handoff_contract") or row.contract or "").lower() or None
    original_preamble = str(record.get("prompt_preamble") or "").strip()
    if not original_preamble and contract == "implement":
        original_preamble = _IMPLEMENT_PREAMBLE
    notice = render_park_resume_preamble(row, code_version=code_version)
    preamble = (
        f"{notice}\n\n{original_preamble}".strip() if original_preamble else notice
    )
    lane = record.get("lane")
    worktree_path = record.get("worktree_path")
    worktree_isolated = bool(record.get("worktree_isolated", False))
    if lane == "A":
        worktree_path = None
        worktree_isolated = False
    return CursorDispatchRequest(
        thread_id=row.thread_id,
        model=str(record.get("model") or row.resolved_model),
        dispatch_id=child_dispatch_id(row, attempt=attempt),
        execution_id=row.execution_id or row.dispatch_id,
        caller_agent=row.caller_agent,
        packet_path=record.get("packet_path") or row.packet_path,
        message=record.get("message"),
        handoff_contract=contract,
        prompt_preamble=preamble,
        skills=record.get("skills"),
        model_knobs=record.get("model_knobs"),
        read_only=bool(record.get("read_only", False)),
        lane=lane if lane in ("A", "B") else None,
        worktree_isolated=worktree_isolated,
        worktree_path=worktree_path,
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        work_key=row.work_key,
        source_ref=row.source_ref,
        resume_of=row.dispatch_id,
    )


@dataclass(slots=True)
class ParkResumeSummary:
    admitted: list[tuple[str, str]] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    reconciled: list[tuple[str, str]] = field(default_factory=list)


def _existing_child(parent_id: str) -> str | None:
    """A child already admitted for this parent (crash between admit and stamp)."""
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches "
            "WHERE resume_of=? AND record_json LIKE ? "
            "ORDER BY rowid DESC LIMIT 1",
            (parent_id, f'%"admitted_via":"{ADMITTED_VIA_PARK_RESUME}"%'),
        ).fetchone()
    return str(row["dispatch_id"]) if row is not None else None


def _is_conductor(row: ParkRow) -> bool:
    kind = row.record.get("packet_kind") or row.record.get("contract") or row.contract
    return str(kind or "").strip().lower() == "conductor"


def _stamp_hop_successor(parent_id: str, child_id: str) -> None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
        if row is None:
            return
        merged = merge_hop_patch(
            str(row["record_json"] or "{}"), {"hop_successor": child_id}
        )
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (merged, parent_id),
        )


def _response_code(response: Any) -> str:
    try:
        body = json.loads(bytes(response.body).decode() or "{}")
    except (ValueError, TypeError, AttributeError):
        return f"http_{getattr(response, 'status_code', '?')}"
    if isinstance(body, dict):
        code = body.get("code") or body.get("status")
        if code:
            return str(code)
    return f"http_{getattr(response, 'status_code', '?')}"


async def _post_resumed_turn(
    bus: CursorBusClient, *, row: ParkRow, child_id: str, attempt: int
) -> None:
    payload = {
        "status": "resumed",
        "resume_of": row.dispatch_id,
        "dispatch_id": child_id,
        "execution_id": row.execution_id or row.dispatch_id,
        "intent_id": row.park_intent_id,
        "attempt": attempt,
        "admitted_via": ADMITTED_VIA_PARK_RESUME,
    }
    result = await bus.reply(
        thread_id=row.thread_id,
        to_agent=row.caller_agent or "dispatch",
        from_agent="cursor-sdk",
        subject=(
            f"cursor-sdk dispatch {child_id} RESUMED "
            f"(resume_of {row.dispatch_id}, restart {row.park_intent_id or 'none'})"
        ),
        body=f"```json\n{json.dumps(payload, indent=2, sort_keys=True)}\n```",
    )
    if result.status_code >= 400:
        logger.error(
            "cursor-sdk RESUMED turn post failed child=%s status=%s body=%s",
            child_id,
            result.status_code,
            result.body,
        )


async def _expire(row: ParkRow, bus: CursorBusClient) -> None:
    from services.git_integration_worker.routes.cursor_sdk import _terminate_link

    if not mark_park_expired(parent_id=row.dispatch_id):
        return
    emit_sdk_park_expired(
        parent_dispatch_id=row.dispatch_id,
        parked_at=row.parked_at,
        ttl_s=park_auto_resume_ttl_s(),
    )
    # The link stayed open through the park for the child that never came;
    # close it now so the caller's poll_hint terminates honestly.
    await _terminate_link(
        bus,
        thread_id=row.thread_id,
        terminal_status="cancelled",
        execution_id=row.execution_id or row.dispatch_id,
    )
    await bus.reply(
        thread_id=row.thread_id,
        to_agent=row.caller_agent or "dispatch",
        from_agent="cursor-sdk",
        subject=f"cursor-sdk dispatch {row.dispatch_id} PARK EXPIRED (no auto-resume)",
        body=(
            f"Auto-resume TTL ({park_auto_resume_ttl_s()}s) elapsed for parked dispatch "
            f"{row.dispatch_id} (restart {row.park_intent_id or 'none'}). Manual "
            f"team_dispatch(resume_of={row.dispatch_id}, reuse_thread={row.thread_id}) "
            "remains possible until the resume-retain TTL."
        ),
    )


async def resume_parked_dispatches(
    *,
    cfg: WorkerConfig,
    controller: Any,
    code_version: str,
    bus: CursorBusClient | None = None,
) -> ParkResumeSummary:
    """Re-admit every open park row (oldest first); idempotent on ``park_resumed_by``."""
    from services.git_integration_worker.routes.cursor_sdk import admit_cursor_dispatch

    summary = ParkResumeSummary()
    bus = bus or CursorBusClient()
    ledger = CursorDispatchLedger.instance()
    for row in open_park_rows():
        if controller.is_draining():
            break
        if row.expired:
            await _expire(row, bus)
            summary.expired.append(row.dispatch_id)
            continue
        existing = _existing_child(row.dispatch_id)
        if existing is not None:
            mark_park_resumed(parent_id=row.dispatch_id, child_id=existing)
            summary.reconciled.append((row.dispatch_id, existing))
            continue
        reason = resume_eligibility_reason(ledger, parent_id=row.dispatch_id)
        if reason is not None:
            attempt = record_resume_refusal(parent_id=row.dispatch_id, reason=reason)
            emit_sdk_park_resume_refused(
                parent_dispatch_id=row.dispatch_id, reason=reason, attempt=attempt
            )
            summary.refused.append((row.dispatch_id, reason))
            continue
        attempt = bump_resume_attempt(parent_id=row.dispatch_id)
        req = build_park_resume_request(row, attempt=attempt, code_version=code_version)
        response = await admit_cursor_dispatch(req, cfg=cfg, controller=controller)
        if response.status_code not in (200, 202):
            code = _response_code(response)
            record_resume_refusal(parent_id=row.dispatch_id, reason=code)
            emit_sdk_park_resume_refused(
                parent_dispatch_id=row.dispatch_id, reason=code, attempt=attempt
            )
            summary.refused.append((row.dispatch_id, code))
            logger.warning(
                "cursor-sdk park resume refused parent=%s child=%s attempt=%s code=%s",
                row.dispatch_id,
                req.dispatch_id,
                attempt,
                code,
            )
            continue
        mark_park_resumed(parent_id=row.dispatch_id, child_id=req.dispatch_id)
        if _is_conductor(row):
            _stamp_hop_successor(row.dispatch_id, req.dispatch_id)
        emit_sdk_park_resume_admitted(
            parent_dispatch_id=row.dispatch_id,
            child_dispatch_id=req.dispatch_id,
            thread_id=row.thread_id,
            intent_id=row.park_intent_id,
            code_version=code_version,
            attempt=attempt,
        )
        await _post_resumed_turn(
            bus, row=row, child_id=req.dispatch_id, attempt=attempt
        )
        summary.admitted.append((row.dispatch_id, req.dispatch_id))
        logger.info(
            "cursor-sdk park resumed parent=%s child=%s intent=%s code_version=%s",
            row.dispatch_id,
            req.dispatch_id,
            row.park_intent_id,
            code_version,
        )
    return summary


__all__ = [
    "ADMITTED_VIA_PARK_RESUME",
    "PARK_RESUME_PREAMBLE_VERSION",
    "ParkResumeSummary",
    "build_park_resume_request",
    "child_dispatch_id",
    "render_park_resume_preamble",
    "resume_parked_dispatches",
]
