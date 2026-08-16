"""No-execution orchestration for directive-loop mission negotiation."""

from __future__ import annotations

from typing import Any

from agent_seat.registry import normalize_bus_address
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.directive import parse_request_body
from services.git_integration_worker.cursor_auto.mission_negotiation_events import (
    emit_negotiation_agreed,
    emit_negotiation_countered,
    emit_negotiation_expired,
    emit_negotiation_opened,
    emit_negotiation_ratified,
    emit_negotiation_refused,
    emit_negotiation_round_limited,
)
from services.git_integration_worker.cursor_auto.mission_negotiation_ledger import (
    get_negotiation_ledger,
)
from services.git_integration_worker.cursor_auto.mission_negotiation_wire import (
    NegotiationParseError,
    ParsedNegotiationRequest,
    build_disposition_body,
    is_mission_negotiation_request,
    negotiation_hop_conflict,
    parse_negotiation_request,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient

logger = get_logger(__name__)

_FROM_AUTO = "cursor-auto"


async def process_mission_negotiation(
    job: AutoJob,
    *,
    bus: CursorBusClient | None = None,
    queue: Any,
) -> dict[str, Any]:
    """Handle one negotiation DIRECTIVE without executable admission."""
    client = bus or CursorBusClient()
    if job.continuity_hop or negotiation_hop_conflict(
        job.body, continuity_hop=bool(job.continuity_hop)
    ):
        return await _refuse(
            job,
            client=client,
            queue=queue,
            reason="negotiation.hop_conflict",
            summary="negotiation cannot combine with continuity hop",
            negotiation_id=None,
        )
    directive = parse_request_body(job.body)
    turn_type = directive.turn_type if directive is not None else "UNKNOWN"
    if not is_mission_negotiation_request(job.body):
        return await _refuse(
            job,
            client=client,
            queue=queue,
            reason="negotiation.malformed",
            summary="expected negotiation DIRECTIVE",
            negotiation_id=None,
        )
    parsed = parse_negotiation_request(
        job.body,
        turn_type=turn_type,
        from_agent=job.from_agent,
    )
    if isinstance(parsed, NegotiationParseError):
        return await _refuse(
            job,
            client=client,
            queue=queue,
            reason=parsed.reason,
            summary=parsed.summary,
            negotiation_id=_negotiation_id_from_body(job.body),
        )
    ledger = get_negotiation_ledger()
    expired = ledger.expire_idle(job.thread_id, parsed.negotiation_id)
    if expired.ok and expired.row is not None and expired.row.state == "EXPIRED":
        emit_negotiation_expired(
            thread_id=job.thread_id,
            negotiation_id=parsed.negotiation_id,
            revision=expired.row.revision,
        )
    result = ledger.apply_transition(
        thread_id=job.thread_id,
        negotiation_id=parsed.negotiation_id,
        phase=parsed.phase,
        revision=parsed.revision,
        proposal_hash=parsed.proposal_hash,
        payload=parsed.payload,
        in_reply_to_turn=parsed.in_reply_to_turn,
        sender=normalize_bus_address(job.from_agent),
        operator_agent=normalize_bus_address(job.from_agent),
        idle_deadline=parsed.idle_deadline,
        request_turn=job.turn_number,
    )
    if result.duplicate:
        return await _reply(
            job,
            client=client,
            queue=queue,
            disposition="negotiation.duplicate",
            row=result.row or _fallback_row(job, parsed),
            in_reply_to_turn=job.turn_number,
            reason="duplicate exact tuple",
        )
    if not result.ok:
        emit_negotiation_refused(
            thread_id=job.thread_id,
            negotiation_id=parsed.negotiation_id,
            reason=str(result.reason or "negotiation.refused"),
            revision=parsed.revision,
        )
        if result.reason == "negotiation.round_limit" and result.row is not None:
            emit_negotiation_round_limited(
                thread_id=job.thread_id,
                negotiation_id=parsed.negotiation_id,
                revision=result.row.revision,
            )
        return await _refuse(
            job,
            client=client,
            queue=queue,
            reason=str(result.reason or "negotiation.refused"),
            summary=str(result.reason or "negotiation refused"),
            negotiation_id=parsed.negotiation_id,
            row=result.prior,
            revision=parsed.revision,
            in_reply_to_turn=job.turn_number,
            proposal_hash=parsed.proposal_hash,
        )
    row = result.row
    assert row is not None
    if parsed.phase == "proposal":
        emit_negotiation_opened(
            thread_id=job.thread_id,
            negotiation_id=row.negotiation_id,
            revision=row.revision,
            proposal_hash=row.proposal_hash,
        )
    elif parsed.phase == "counter":
        emit_negotiation_countered(
            thread_id=job.thread_id,
            negotiation_id=row.negotiation_id,
            revision=row.revision,
            proposal_hash=row.proposal_hash,
        )
    elif parsed.phase == "agree":
        emit_negotiation_agreed(
            thread_id=job.thread_id,
            negotiation_id=row.negotiation_id,
            revision=row.revision,
            proposal_hash=row.proposal_hash,
        )
        return await _reply(
            job,
            client=client,
            queue=queue,
            disposition="negotiation.agreed",
            row=row,
            in_reply_to_turn=job.turn_number,
        )
    elif parsed.phase == "ratify":
        agreement_ref = f"agent-bus:{job.thread_id}#{job.turn_number}"
        emit_negotiation_ratified(
            thread_id=job.thread_id,
            negotiation_id=row.negotiation_id,
            revision=row.revision,
            proposal_hash=row.proposal_hash,
            agreement_ref=agreement_ref,
        )
        return await _reply(
            job,
            client=client,
            queue=queue,
            disposition="negotiation.ratified",
            row=row,
            in_reply_to_turn=job.turn_number,
            agreement_ref=agreement_ref,
        )
    disposition = "negotiation.accepted"
    return await _reply(
        job,
        client=client,
        queue=queue,
        disposition=disposition,
        row=row,
        in_reply_to_turn=job.turn_number,
    )


def _negotiation_id_from_body(body: str) -> str | None:
    from services.git_integration_worker.cursor_auto.mission_negotiation_wire import (
        field_value,
    )

    raw = field_value(body, "negotiation_id")
    return raw.strip() if raw else None


def _fallback_row(job: AutoJob, parsed: ParsedNegotiationRequest) -> Any:
    from services.git_integration_worker.cursor_auto.mission_negotiation_ledger import (
        NegotiationRow,
    )

    return NegotiationRow(
        thread_id=job.thread_id,
        negotiation_id=parsed.negotiation_id,
        state="OPEN",
        revision=parsed.revision,
        proposal_hash=parsed.proposal_hash,
        payload=parsed.payload,
        counter_count=0,
        operator_agent=normalize_bus_address(job.from_agent),
        idle_deadline=parsed.idle_deadline,
        latest_turn=job.turn_number,
    )


async def _reply(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    disposition: str,
    row: Any,
    in_reply_to_turn: int,
    reason: str | None = None,
    agreement_ref: str | None = None,
) -> dict[str, Any]:
    body = build_disposition_body(
        disposition=disposition,
        negotiation_id=row.negotiation_id,
        revision=row.revision,
        in_reply_to_turn=in_reply_to_turn,
        proposal_hash=row.proposal_hash,
        state=row.state,
        reason=reason,
        agreement_ref=agreement_ref,
        payload=row.payload if disposition == "negotiation.countered" else None,
    )
    subject = f"status:wait — negotiation.{disposition.split('.', 1)[-1]}"
    reply = await client.reply(
        thread_id=job.thread_id,
        to_agent=normalize_bus_address(job.from_agent),
        from_agent=_FROM_AUTO,
        subject=subject,
        body=body,
    )
    queue.mark_done(job.job_id, failed=False)
    return {
        "ok": reply.status_code < 400,
        "phase": "negotiation",
        "disposition": disposition,
        "state": row.state,
        "status_code": reply.status_code,
        "terminal_status": subject,
    }


async def _refuse(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    reason: str,
    summary: str,
    negotiation_id: str | None,
    row: Any | None = None,
    revision: int | None = None,
    in_reply_to_turn: int | None = None,
    proposal_hash: str | None = None,
) -> dict[str, Any]:
    emit_negotiation_refused(
        thread_id=job.thread_id,
        negotiation_id=negotiation_id,
        reason=reason,
        revision=revision,
    )
    state = row.state if row is not None else "OPEN"
    body = build_disposition_body(
        disposition="negotiation.refused",
        negotiation_id=negotiation_id or "unknown",
        revision=revision or (row.revision if row is not None else 0),
        in_reply_to_turn=in_reply_to_turn or job.turn_number,
        proposal_hash=proposal_hash or (row.proposal_hash if row is not None else "sha256:" + "0" * 64),
        state=state,
        reason=reason,
    )
    subject = "status:wait — negotiation.refused"
    reply = await client.reply(
        thread_id=job.thread_id,
        to_agent=normalize_bus_address(job.from_agent),
        from_agent=_FROM_AUTO,
        subject=subject,
        body=body,
    )
    queue.mark_done(job.job_id, failed=reply.status_code >= 400)
    return {
        "ok": False,
        "phase": "negotiation",
        "disposition": "negotiation.refused",
        "reason": reason,
        "summary": summary,
        "status_code": reply.status_code,
        "terminal_status": subject,
    }
