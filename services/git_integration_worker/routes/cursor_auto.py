"""HTTP routes for Cursor Auto admit path (enqueue + liveness)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.continuity_hop import (
    run_continuity_hop_concurrent,
)
from services.git_integration_worker.cursor_auto.directive import (
    is_continuity_hop_request,
    is_mission_negotiation_directive,
    split_continuity_hop_legs,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.hop_cadence import (
    observe_lane_from_enqueue,
)
from services.git_integration_worker.cursor_auto.job_ledger import get_ledger
from services.git_integration_worker.cursor_auto.job_lifecycle import (
    job_state_response,
)
from services.git_integration_worker.cursor_auto.liveness import (
    get_registry,
    queue_admission_health,
)
from services.git_integration_worker.cursor_auto.mission_negotiation_wire import (
    negotiation_hop_conflict,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, get_queue
from services.git_integration_worker.cursor_auto.static_pin_refusal import (
    assess_static_pin_refusal,
)
from services.git_integration_worker.cursor_auto.supersede import (
    supersede_same_thread_inflight,
)
from services.git_integration_worker.cursor_auto.wire_map import (
    coalesce_cdp_desired_model_into_escalation,
)
from services.git_integration_worker.cursor_auto.wire_skew_events import (
    note_dropped_fields,
)
from services.git_integration_worker.cursor_bus import CursorBusClient

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/git/cursor-auto", tags=["cursor-auto"])


class EnqueueBody(BaseModel):
    """Payload from MCP ``agent_bus.request`` after turn write."""

    model_config = ConfigDict(extra="ignore")

    thread_id: str
    turn_number: int = Field(ge=1)
    subject: str
    body: str = ""
    from_agent: str
    to_agent: str = "cursor"
    desired_model: str = "auto"
    desired_effort: str = "medium"
    escalation: str | None = None
    contract: str = "answer"
    require_attended: bool = False
    # Idempotency key minted or validated at MCP intake; echoed on the closeout.
    request_id: str | None = None
    cse_chat_url: str | None = None
    cse_registration_id: str | None = None
    # Row 21: structural hop flag (OR with first-line TYPE: CONTINUITY_HANDOFF).
    continuity_hop: bool = False
    # GIW checkout isolation — same lever as POST /api/v1/cursor/dispatch.
    # Distinct from lane_role (bus parentage) and tag lane:cursor-auto.
    lane: Literal["A", "B"] | None = None
    # Declared execution mode (S-3, mission 9440) -- structural predicate input
    # for concurrent admission opt-in. Default preserves today's exclusive-
    # serial-slot behavior. Never inferred from `contract`.
    execution_mode: str = "serial"
    wire_dropped_fields: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _log_dropped_wire_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        known = set(cls.model_fields)
        dropped = sorted(key for key in data if key not in known)
        if dropped:
            sender = str(data.get("from_agent") or "unknown")
            note_dropped_fields(
                boundary="mcp→giw/enqueue",
                dropped_fields=dropped,
                sender=sender,
            )
            data["wire_dropped_fields"] = dropped
        return data


@router.get("/liveness")
async def liveness() -> dict[str, Any]:
    """Arm-predicate probe (handler heartbeat) + admit-eligible queue-health
    projection (S-4). The ``queue_health`` key is report-only — see
    ``queue_admission_health()``'s docstring; nothing here terminalizes.
    """
    snapshot = get_registry().snapshot()
    snapshot["queue_health"] = queue_admission_health()
    return snapshot


@router.get("/queue")
async def queue_snapshot() -> dict[str, Any]:
    return get_queue().snapshot()


@router.get("/job-state")
async def job_state(
    job_id: str | None = None,
    thread_id: str | None = None,
    include_terminal: bool = False,
) -> dict[str, Any]:
    """Keyed read of live cursor-auto phase+clocks (same shape as thread_get)."""
    if not job_id and not thread_id:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "job-state requires job_id and/or thread_id",
                "reason": "missing_key",
            },
        )
    view = get_ledger().observer_state(
        job_id=job_id,
        thread_id=thread_id,
        include_terminal=include_terminal,
    )
    return job_state_response(job_id=job_id, thread_id=thread_id, view=view)


@router.post("/enqueue")
async def enqueue(body: EnqueueBody, request: Request):
    """Admit-on-request enqueue. Requires a live Auto handler (else 503)."""
    registry = get_registry()
    if not registry.is_live():
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "handler_status": "no-auto-handler",
                "reason": "no_live_auto_handler",
                "liveness": registry.snapshot(),
            },
        )
    queue = get_queue()
    is_hop, matched_token = is_continuity_hop_request(
        body.body, wire_flag=bool(body.continuity_hop)
    )
    if negotiation_hop_conflict(body.body, continuity_hop=bool(body.continuity_hop)):
        logger.info(
            "cursor-auto negotiation hop conflict thread=%s turn=%s",
            body.thread_id,
            body.turn_number,
        )
    desired_model, escalation, coalesce_meta = coalesce_cdp_desired_model_into_escalation(
        body.desired_model,
        body.escalation,
    )
    if coalesce_meta.get("coalesced"):
        logger.info(
            "cursor-auto coalesced cdp desired_model thread=%s turn=%s %s",
            body.thread_id,
            body.turn_number,
            coalesce_meta.get("notes"),
        )
    if not is_hop:
        static_refusal = assess_static_pin_refusal(
            desired_model=desired_model,
            desired_effort=body.desired_effort,
            escalation=escalation,
            contract=body.contract,
            body=body.body,
        )
        if static_refusal is not None:
            job_stub = AutoJob(
                job_id=str(uuid.uuid4()),
                thread_id=body.thread_id,
                turn_number=body.turn_number,
                subject=body.subject,
                body=body.body,
                from_agent=body.from_agent,
                to_agent=body.to_agent,
                desired_model=desired_model,
                desired_effort=body.desired_effort,
                contract=static_refusal.contract,
                escalation=escalation,
                require_attended=body.require_attended,
                request_id=body.request_id,
            )
            terminal = await post_terminal_status(
                job_stub,
                client=CursorBusClient(),
                queue=queue,
                summary=static_refusal.summary,
                disposition="blocked",
                contract=static_refusal.contract,
                terminal_status="status:blocked",
                payload=static_refusal.payload,
                failed=True,
            )
            logger.info(
                "cursor-auto static pin refused thread=%s turn=%s reason=%s",
                body.thread_id,
                body.turn_number,
                static_refusal.reason,
            )
            return JSONResponse(
                status_code=200,
                content={
                    "ok": True,
                    "handler_status": "static-pin-refused",
                    "static_refusal": True,
                    "terminal_status": terminal.get("terminal_status"),
                    "reason": static_refusal.reason,
                    "queue": queue.snapshot(),
                },
            )
    # Dual-envelope hop: strip TYPE:DIRECTIVE sibling before hop job lands so
    # CDP prompt cannot double-execute the deferred leg (loop guard). Sibling
    # is queue.enqueue'd directly — ¬ HTTP re-admit, ¬ supersede.
    hop_body = body.body
    deferred_body: str | None = None
    if is_hop:
        hop_body, deferred_body = split_continuity_hop_legs(
            body.body, matched_token=matched_token
        )
    job = queue.enqueue(
        thread_id=body.thread_id,
        turn_number=body.turn_number,
        subject=body.subject,
        body=hop_body,
        from_agent=body.from_agent,
        to_agent=body.to_agent,
        desired_model=desired_model,
        desired_effort=body.desired_effort,
        escalation=escalation,
        contract=body.contract,
        require_attended=body.require_attended,
        request_id=body.request_id,
        cse_chat_url=body.cse_chat_url,
        cse_registration_id=body.cse_registration_id,
        continuity_hop=is_hop,
        continuity_matched_token=matched_token,
        wire_dropped_fields=tuple(body.wire_dropped_fields),
        lane=body.lane,
        execution_mode=body.execution_mode,
    )
    deferred_job_id: str | None = None
    if deferred_body is not None:
        deferred = queue.enqueue(
            thread_id=body.thread_id,
            turn_number=body.turn_number,
            subject=f"{body.subject} — deferred non-hop leg",
            body=deferred_body,
            from_agent=body.from_agent,
            to_agent=body.to_agent,
            desired_model=desired_model,
            desired_effort=body.desired_effort,
            escalation=escalation,
            contract=body.contract,
            require_attended=body.require_attended,
            request_id=(
                f"{body.request_id}:deferred" if body.request_id else None
            ),
            cse_chat_url=body.cse_chat_url,
            cse_registration_id=body.cse_registration_id,
            continuity_hop=False,
            continuity_matched_token=None,
            lane=body.lane,
            execution_mode="serial",
        )
        deferred_job_id = deferred.job_id
        logger.info(
            "cursor-auto deferred non-hop leg job=%s from hop=%s thread=%s",
            deferred.job_id,
            job.job_id,
            body.thread_id,
        )
    # Cadence ownership: enroll/refresh CSE-age watch on operator-proxy admits
    # (web-* or cdp-operator-*; not hops).
    observe_lane_from_enqueue(job)
    logger.info(
        "cursor-auto enqueued job=%s thread=%s turn=%s request_id=%s "
        "continuity_hop=%s matched_token=%s deferred_job_id=%s",
        job.job_id,
        body.thread_id,
        body.turn_number,
        body.request_id,
        is_hop,
        matched_token,
        deferred_job_id,
    )
    interrupt: dict[str, Any] | None = None
    if is_hop:
        # Row 21: hop ≠ backtrack — leave any claimed *or queued* commission
        # running. F5: always commission CDP before serial process_job / admit
        # gates; incumbent is optional (hop with empty lane still launches).
        incumbent = queue.incumbent_for_thread(
            body.thread_id, exclude_job_id=job.job_id
        )
        controller = getattr(request.app.state, "admission_controller", None)
        if controller is None:
            logger.error(
                "cursor-auto enqueue hop rejected: admission_controller missing "
                "job=%s thread=%s",
                job.job_id,
                body.thread_id,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "error": "admission_controller_unavailable",
                    "reason": "missing_admission_controller",
                    "job_id": job.job_id,
                },
            )
        controller.create_tracked_task(
            run_continuity_hop_concurrent(
                job, queue=queue, incumbent=incumbent
            ),
            op_id=f"cursor-auto-continuity-hop:{job.job_id}",
        )
    else:
        # Negotiation turns skip same-thread supersede — they are pre-birth only.
        if not is_mission_negotiation_directive(body.body):
            # A second request on a private thread is a backtrack, not a queue append:
            # interrupt the live episode or withdraw a queued predecessor so the new
            # DIRECTIVE does not wait it out. Continuity hops skip (Gate A).
            interrupt = await supersede_same_thread_inflight(job, queue=queue)
    # Peers only (exclude self): alone → 0; queued predecessors → N. Same lock
    # as snapshot(); do not disturb supersede vocabulary beside this field.
    lane = queue.thread_lane_counts(
        body.thread_id, exclude_job_id=job.job_id
    )
    waiter = queue.waiter_receipt(job.job_id)
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "handler_status": "auto-admit-armed",
            "job_id": job.job_id,
            "request_id": job.request_id,
            "superseded": interrupt,
            "same_thread_pending": lane["same_thread_pending"],
            "same_thread_claimed": lane["same_thread_claimed"],
            "continuity_hop": is_hop,
            "matched_token": matched_token,
            "deferred_job_id": deferred_job_id,
            "deferred_leg_enqueued": deferred_job_id is not None,
            "queue_position": waiter["queue_position"],
            "queued_age_s": waiter["queued_age_s"],
            "queue": queue.snapshot(),
        },
    )
