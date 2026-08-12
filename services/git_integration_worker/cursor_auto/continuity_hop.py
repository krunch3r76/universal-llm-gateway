"""Continuity-hop path — CDP successor launch without supersede or implement admit.

Row 21: a structural ``TYPE: CONTINUITY_HANDOFF`` must not interrupt an
in-flight commission. F5: classification alone is insufficient — the hop must
reach CDP commission **before** contract grading / vision-scope admit gates.
A handoff has no ACs; routing it as ``contract: implement`` is a category error.
"""

from __future__ import annotations

import json
from typing import Any

from agent_seat.registry import normalize_bus_address
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.admit_report import (
    build_admit_report_body,
)
from services.git_integration_worker.cursor_auto.field_parity import (
    compute_field_parity_for_job,
)
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PROPAGATE_CONTRACT,
    admit_propagate_body,
)
from services.git_integration_worker.cursor_auto.cdp_escalation import (
    commission_cdp_escalation,
)
from services.git_integration_worker.cursor_auto.directive import (
    split_continuity_hop_legs,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, AutoJobQueue
from services.git_integration_worker.cursor_auto.reflex_events import (
    emit_cdp_effort_bind,
)
from services.git_integration_worker.cursor_auto.wire_map import (
    admit_effort_override_rule_line,
    admit_model_override_rule_line,
    admit_model_pin_flags,
    resolve_desired_effort,
    resolve_desired_model,
    resolve_escalation,
    resolve_handoff_contract,
)
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_sdk_supersede import live_run_for_thread

logger = get_logger(__name__)

_FROM_AUTO = "cursor-auto"
_DEFAULT_CDP_MODEL = "cdp/opus-5"
# Schema default on agent_bus.request / AutoJob; CDP sealed-ask defaults Opus to High.
_UNPINNED_EFFORT = "medium"


def _hop_cdp_model(job: AutoJob) -> str:
    desired = (job.desired_model or "").strip()
    if desired.startswith("cdp/"):
        return desired
    return _DEFAULT_CDP_MODEL


def _hop_reasoning_effort(job: AutoJob) -> dict[str, Any]:
    """Resolve wire effort for a hop CDP commission.

    Schema-default ``medium`` is treated as unpinned so sealed-ask High remains
    the picker default. Explicit pins (incl. ``xhigh`` / ``extra`` aliases) forward.
    """
    effort = resolve_desired_effort(job.desired_effort)
    resolved = str(effort.get("resolved_effort") or "").strip().lower()
    if resolved == _UNPINNED_EFFORT:
        return {**effort, "wire_effort": None}
    return {**effort, "wire_effort": resolved or None}


async def _post_hop_admit_report(
    job: AutoJob,
    *,
    client: CursorBusClient,
    cdp_model: str,
    effort: dict[str, Any],
) -> None:
    """Post report-only admit lines for a hop (no gating, no ledger admit stamp)."""
    try:
        sdk_model = resolve_desired_model(
            job.desired_model, contract=job.contract or "answer"
        )
        escalation = resolve_escalation(job.escalation or cdp_model)
        if not escalation.get("resolved_escalation"):
            escalation = {
                **escalation,
                "requested": job.escalation or cdp_model,
                "resolved_escalation": cdp_model,
            }
        handoff = resolve_handoff_contract(job.contract or "light-bounded")
        contract = job.contract or "light-bounded"
        propagate_admission = None
        if contract.strip().lower() == PROPAGATE_CONTRACT:
            propagate_admission = admit_propagate_body(job.body)
        parity_report = compute_field_parity_for_job(
            body=job.body,
            contract=contract,
            propagate_admission=propagate_admission,
            wire_dropped=tuple(job.wire_dropped_fields),
        )
        body = build_admit_report_body(
            model=sdk_model,
            effort=effort,
            escalation=escalation,
            contract=contract,
            handoff_contract=handoff,
            continuity_hop=True,
            matched_token=job.continuity_matched_token,
            report_only=True,
            override_rule=admit_model_override_rule_line(sdk_model),
            effort_rule=admit_effort_override_rule_line(effort),
            pin_flags=admit_model_pin_flags(sdk_model, effort),
            field_parity_report=parity_report,
        )
        await client.reply(
            thread_id=job.thread_id,
            to_agent=normalize_bus_address(job.from_agent),
            from_agent=_FROM_AUTO,
            subject=f"status:admit-report — {job.subject[:80]}",
            body=body,
        )
    except Exception as exc:  # noqa: BLE001 — report must not block hop commission
        logger.warning(
            "continuity hop admit-report failed job=%s: %s", job.job_id, exc
        )


async def post_harvest_residual(
    job: AutoJob,
    *,
    client: CursorBusClient,
    incumbent: AutoJob | None,
    dispatch_id: str | None,
) -> dict[str, Any]:
    """Name any live commission so the successor has a harvest target."""
    mailbox = normalize_bus_address(job.from_agent)
    if incumbent is None:
        payload = {
            "type": "CONTINUITY_HARVEST_RESIDUAL",
            "incumbent_job_id": None,
            "incumbent_dispatch_id": dispatch_id,
            "incumbent_subject": None,
            "hop_job_id": job.job_id,
            "hop_matched_token": job.continuity_matched_token,
            "re_issue_subject": None,
            "note": (
                "No claimed Auto commission on this lane at hop time. "
                "CDP successor still commissioned; harvest any non-Auto "
                "in-flight work from the lane tip."
            ),
        }
    else:
        payload = {
            "type": "CONTINUITY_HARVEST_RESIDUAL",
            "incumbent_job_id": incumbent.job_id,
            "incumbent_dispatch_id": dispatch_id,
            "incumbent_subject": incumbent.subject,
            "hop_job_id": job.job_id,
            "hop_matched_token": job.continuity_matched_token,
            "re_issue_subject": incumbent.subject,
            "note": (
                "In-flight commission on this lane was preserved (hop≠backtrack). "
                "Harvest its CLOSEOUT; do not treat it as superseded."
            ),
        }
    reply = await client.reply(
        thread_id=job.thread_id,
        to_agent=mailbox,
        from_agent=_FROM_AUTO,
        subject=(
            f"continuity harvest residual — "
            f"{(incumbent.job_id if incumbent else job.job_id)[:12]}"
        ),
        body=json.dumps(payload, indent=2),
        allow_long_body=True,
    )
    return {
        "ok": reply.status_code < 400,
        "status_code": reply.status_code,
        "to_agent": mailbox,
        "payload": payload,
    }


def _enqueue_deferred_non_hop_leg(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    deferred_body: str,
) -> str:
    """Queue the stripped DIRECTIVE sibling without HTTP re-admit / supersede."""
    sibling = queue.enqueue(
        thread_id=job.thread_id,
        turn_number=job.turn_number,
        subject=f"{job.subject} — deferred non-hop leg",
        body=deferred_body,
        from_agent=job.from_agent,
        to_agent=job.to_agent,
        desired_model=job.desired_model,
        desired_effort=job.desired_effort,
        escalation=job.escalation,
        contract=job.contract,
        require_attended=job.require_attended,
        request_id=(
            f"{job.request_id}:deferred" if job.request_id else None
        ),
        cse_chat_url=job.cse_chat_url,
        cse_registration_id=job.cse_registration_id,
        continuity_hop=False,
        continuity_matched_token=None,
    )
    logger.info(
        "continuity hop deferred non-hop leg job=%s from hop=%s thread=%s",
        sibling.job_id,
        job.job_id,
        job.thread_id,
    )
    return sibling.job_id


async def complete_continuity_hop(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    incumbent: AutoJob | None = None,
    client: CursorBusClient | None = None,
) -> dict[str, Any]:
    """Harvest residual → CDP commission → terminal (job already claimed).

    Called from the concurrent enqueue task after ``claim_job``, or from
    ``process_job`` when the serial worker won the claim race — never runs
    ``effective_contract`` / admit gates.

    Defense-in-depth: if the claimed body still carries a trailing
    ``TYPE: DIRECTIVE`` (enqueue fork missed), strip it before CDP commission
    and enqueue the sibling so the leg is not lost and not double-prompted.
    """
    bus = client or CursorBusClient()
    hop_body, deferred_body = split_continuity_hop_legs(
        job.body, matched_token=job.continuity_matched_token
    )
    deferred_job_id: str | None = None
    if deferred_body is not None:
        job.body = hop_body
        deferred_job_id = _enqueue_deferred_non_hop_leg(
            job, queue=queue, deferred_body=deferred_body
        )
    live = live_run_for_thread(job.thread_id)
    dispatch_id = live.dispatch_id if live else None
    residual = await post_harvest_residual(
        job,
        client=bus,
        incumbent=incumbent,
        dispatch_id=dispatch_id,
    )
    model = _hop_cdp_model(job)
    effort = _hop_reasoning_effort(job)
    wire_effort = effort.get("wire_effort")
    await _post_hop_admit_report(job, client=bus, cdp_model=model, effort=effort)
    commissioned = await commission_cdp_escalation(
        job,
        model=model,
        reasoning_effort=str(wire_effort) if wire_effort else None,
        purpose="operator-proxy",
        mission_kind="hop",
        parent_thread=str(job.thread_id),
    )
    effort_echo = {
        "requested_effort": effort.get("requested"),
        "resolved_effort": effort.get("resolved_effort"),
        "wire_effort": wire_effort,
    }
    if not commissioned.get("ok"):
        terminal = await post_terminal_status(
            job,
            client=bus,
            queue=queue,
            summary=(
                "continuity hop CDP commission failed: "
                f"{commissioned.get('error')}"
            ),
            disposition="failed",
            contract=job.contract,
            terminal_status="status:failed",
            failed=True,
            payload={
                "summary": "continuity hop CDP commission failed",
                "continuity_hop": True,
                "matched_token": job.continuity_matched_token,
                "harvest_residual": residual,
                "commission": commissioned,
                "deferred_job_id": deferred_job_id,
                "deferred_leg_enqueued": deferred_job_id is not None,
                **effort_echo,
            },
        )
        return terminal
    from services.git_integration_worker.cursor_auto.disposition_outcome import (
        m1_cdp_commission,
        outcome_disposition_for_stamp,
    )

    execution_id = commissioned.get("execution_id")
    emit_cdp_effort_bind(
        thread_id=job.thread_id,
        execution_id=str(execution_id or ""),
        model=model,
        requested_effort=str(effort.get("requested") or ""),
        resolved_effort=str(wire_effort or effort.get("resolved_effort") or ""),
        lane="cursor-auto-continuity-hop",
    )
    hop_disposition = outcome_disposition_for_stamp(
        "dispatched-and-relayed",
        m1_satisfied=m1_cdp_commission(execution_id=execution_id),
    )
    hop_payload: dict[str, Any] = {
        "summary": f"continuity hop CDP commissioned model={model}",
        "reason": "continuity_hop_cdp_commissioned",
        "continuity_hop": True,
        "matched_token": job.continuity_matched_token,
        "harvest_residual": residual,
        "execution_id": execution_id,
        "incumbent_job_id": incumbent.job_id if incumbent else None,
        "incumbent_dispatch_id": dispatch_id,
        "deferred_job_id": deferred_job_id,
        "deferred_leg_enqueued": deferred_job_id is not None,
        **effort_echo,
    }
    if hop_disposition is not None:
        hop_payload["disposition"] = hop_disposition
    terminal = await post_terminal_status(
        job,
        client=bus,
        queue=queue,
        summary=f"continuity hop CDP commissioned model={model}",
        disposition=hop_disposition,
        contract=job.contract,
        terminal_status="status:done",
        payload=hop_payload,
    )
    if execution_id and not terminal.get("execution_id"):
        terminal = {**terminal, "execution_id": execution_id}
    return terminal


async def run_continuity_hop_concurrent(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    incumbent: AutoJob | None = None,
) -> dict[str, Any]:
    """Claim hop (if still queued), then commission CDP — leave any incumbent."""
    claimed = queue.claim_job(job.job_id)
    if claimed is None:
        logger.warning(
            "continuity hop concurrent skip job=%s — not queued",
            job.job_id,
        )
        return {"ok": False, "reason": "hop_not_queued"}
    return await complete_continuity_hop(
        claimed,
        queue=queue,
        incumbent=incumbent,
    )
