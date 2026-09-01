"""Auto job processor — admit → nested SDK dispatch → operator CLOSEOUT + WAKE."""

from __future__ import annotations

from typing import Any

from agent_seat.registry import normalize_bus_address
from contract_vocab import nested_scope_contracts
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.admit_gates import (
    AdmitGateResult,
    blocking_admit_gate,
)
from services.git_integration_worker.cursor_auto.admit_report import (
    build_admit_report_body,
)
from services.git_integration_worker.cursor_auto.cdp_escalation import (
    commission_cdp_escalation,
    escalation_lane_refusal,
    read_cdp_lane_snapshot,
)
from services.git_integration_worker.cursor_auto.checkout_lane import (
    resolve_nested_checkout_lane,
)
from services.git_integration_worker.cursor_auto.continuity_hop import (
    complete_continuity_hop,
)
from services.git_integration_worker.cursor_auto.directive import (
    attendance_surface,
    build_sdk_message,
    effective_contract,
    effective_require_attended,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.dispatch_bounds import (
    clamp_effort_to_model_card,
    redirect_mechanical_executor,
)
from services.git_integration_worker.cursor_auto.dispatch_progress import (
    ProgressEmitter,
)
from services.git_integration_worker.cursor_auto.envelope_fields import (
    envelope_values_from_job,
)
from services.git_integration_worker.cursor_auto.episode_briefing import (
    compose_admit_body,
    maybe_briefing_for_admit,
)
from services.git_integration_worker.cursor_auto.execute_admission import (
    EXECUTE_CONTRACT,
)
from services.git_integration_worker.cursor_auto.field_parity import (
    compute_field_parity_for_job,
)
from services.git_integration_worker.cursor_auto.gate_serialize import (
    NESTED_IN_SEAT_REASON,
    plan_nested_dispatch,
    prefer_dispatch_over_park,
)
from services.git_integration_worker.cursor_auto.handler_deadline import (
    deadline_terminal,
)
from services.git_integration_worker.cursor_auto.handler_execute import (
    run_execute_in_seat,
)
from services.git_integration_worker.cursor_auto.handler_propagation import (
    run_propagation_in_seat,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
    terminal_failed,
    terminal_in_seat,
    terminal_needs_attended,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    RELAY_PHASE_SDK_TERMINAL,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.knob_compose import compose_model_knobs
from services.git_integration_worker.cursor_auto.nest_parent import resolve_nest_under
from services.git_integration_worker.cursor_auto.nested_outcome import (
    relay_closeout_outcome,
    relay_confer_outcome,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    CloseoutRelayContext,
    fetch_sdk_closeout_body,
    poll_dispatch_terminal_with_liveness,
    submit_nested_dispatch,
)
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PROPAGATE_CONTRACT,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, get_queue
from services.git_integration_worker.cursor_auto.reflex_events import (
    emit_cdp_effort_bind,
    emit_mechanical_executor_redirected,
    maybe_emit_premium_bind,
)
from services.git_integration_worker.cursor_auto.reflex_read import (
    maybe_run_second_read,
)
from services.git_integration_worker.cursor_auto.static_pin_refusal import (
    assess_static_pin_refusal,
)
from services.git_integration_worker.cursor_auto.supersede import (
    compose_supersede_preamble,
    post_superseded_terminal,
    settle_supersede,
)
from services.git_integration_worker.cursor_auto.wire_map import (
    admit_effort_override_rule_line,
    admit_model_override_rule_line,
    admit_model_pin_flags,
    coalesce_cdp_desired_model_into_escalation,
    resolve_contract_disposition,
    resolve_desired_effort,
    resolve_desired_model,
    resolve_escalation,
    resolve_handoff_contract,
)
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)

logger = get_logger(__name__)

_FROM_AUTO = "cursor-auto"
_NESTED_CONTRACTS = nested_scope_contracts() | {"confer", "ask"}


def _close_dispatch_ticket(
    admission_controller: Any | None,
    dispatch_id: str | None,
    *,
    terminal_status: str,
) -> None:
    if admission_controller is None or not dispatch_id:
        return
    admission_controller.close_ticket(dispatch_id, terminal_status=terminal_status)


async def process_job(
    job: AutoJob,
    *,
    bus: CursorBusClient | None = None,
    admission_controller: Any | None = None,
    worker_id: str = "",
    worker_started_at: str = "",
) -> dict[str, Any]:
    """Process one Auto job: admit → nested SDK (when armed) → terminal reply.

    Nested ``implement|investigate|verify`` success returns a ``wake`` key:
    attempted ``{ok, status_code, body}``; skipped
    ``{ok: False, skipped: True, reason: \"closeout_not_ok\"}`` when CLOSEOUT
    relay failed; failed / token-guard ``{ok: False, reason: …}`` when wake
    post was blocked or errored. Wake failure does not flip ``failed`` when
    CLOSEOUT succeeded.

    A job displaced mid-flight by a newer same-thread request returns
    ``phase: superseded`` with terminal ``status:superseded`` — never a
    success-shaped CLOSEOUT for the void episode.
    """
    client = bus or CursorBusClient()
    queue = get_queue()
    # F5: hop must never enter contract grading / vision-scope admit.
    # Concurrent enqueue task normally claims first; this is the claim-race
    # and defense-in-depth path when the serial worker holds the hop.
    if job.continuity_hop:
        incumbent = queue.incumbent_for_thread(job.thread_id, exclude_job_id=job.job_id)
        return await complete_continuity_hop(
            job,
            queue=queue,
            incumbent=incumbent,
            client=client,
        )
    from services.git_integration_worker.cursor_auto.directive import (
        is_mission_negotiation_directive,
    )
    from services.git_integration_worker.cursor_auto.mission_negotiation_handler import (
        process_mission_negotiation,
    )

    if is_mission_negotiation_directive(job.body):
        return await process_mission_negotiation(job, bus=client, queue=queue)
    directive = parse_request_body(job.body)
    contract = effective_contract(job.contract, job.body)
    # Downstream closeout/journal/meta read job.contract — stamp effective.
    job.contract = contract
    expired = await deadline_terminal(job, client=client, queue=queue)
    if expired is not None:
        return expired
    desired_model, escalation, coalesce_meta = (
        coalesce_cdp_desired_model_into_escalation(
            job.desired_model,
            job.escalation,
        )
    )
    if coalesce_meta.get("coalesced"):
        logger.info(
            "cursor-auto coalesced cdp desired_model job=%s %s",
            job.job_id,
            coalesce_meta.get("notes"),
        )
        job.desired_model = desired_model
        job.escalation = escalation
    static_refusal = assess_static_pin_refusal(
        desired_model=job.desired_model,
        desired_effort=job.desired_effort,
        escalation=job.escalation,
        contract=job.contract,
        body=job.body,
    )
    if static_refusal is not None:
        return await post_terminal_status(
            job,
            client=client,
            queue=queue,
            summary=static_refusal.summary,
            disposition="blocked",
            contract=static_refusal.contract,
            terminal_status="status:blocked",
            payload=static_refusal.payload,
            failed=True,
        )
    model = resolve_desired_model(job.desired_model, contract=contract)
    handoff_contract = resolve_handoff_contract(contract, body=job.body)
    model, displaced_model = redirect_mechanical_executor(
        model, contract=contract, handoff_contract=handoff_contract
    )
    if displaced_model:
        emit_mechanical_executor_redirected(
            thread_id=job.thread_id,
            requested_model=displaced_model,
            executor_model=str(model["resolved_model_id"]),
            contract=contract,
            handoff_contract=handoff_contract,
        )
    wire_effort = resolve_desired_effort(job.desired_effort, contract=contract)
    effort = clamp_effort_to_model_card(model["resolved_model_id"], wire_effort)
    escalation = resolve_escalation(job.escalation)
    contract_info = resolve_contract_disposition(contract)
    gate_result = AdmitGateResult()
    if (
        directive is not None
        or contract in _NESTED_CONTRACTS
        or contract
        in {
            EXECUTE_CONTRACT,
            PROPAGATE_CONTRACT,
        }
    ):
        gate_result = await blocking_admit_gate(job, client=client, queue=queue)
        if gate_result.blocked is not None:
            return gate_result.blocked

    work_bounded = contract == "answer" or (
        directive is not None and directive.density == "sparse"
    )
    gate_plan = prefer_dispatch_over_park(
        plan_nested_dispatch(work_bounded=work_bounded),
        work_bounded=work_bounded,
    )

    override_rule = admit_model_override_rule_line(model)
    effort_rule = admit_effort_override_rule_line(effort)
    pin_flags = admit_model_pin_flags(model, effort)
    parity_report = compute_field_parity_for_job(
        body=job.body,
        contract=str(contract),
        propagate_admission=gate_result.propagate_admission,
        execute_admission=gate_result.execute_admission,
        envelope=envelope_values_from_job(job),
        wire_dropped=tuple(job.wire_dropped_fields),
    )
    base_admit_body = build_admit_report_body(
        model=model,
        effort=effort,
        escalation=escalation,
        contract=str(contract_info["contract"]),
        handoff_contract=handoff_contract,
        gate_action=str(gate_plan["action"]),
        gate_occupancy_source=str(
            gate_plan.get("gate", {}).get("occupancy_source", "gate_only")
        ),
        directive_present=directive is not None,
        continuity_hop=bool(job.continuity_hop),
        matched_token=job.continuity_matched_token,
        override_rule=override_rule,
        effort_rule=effort_rule,
        pin_flags=pin_flags,
        field_parity_report=parity_report,
    )
    briefing = await maybe_briefing_for_admit(job.thread_id, contract=contract)
    admit = await client.reply(
        thread_id=job.thread_id,
        to_agent=normalize_bus_address(job.from_agent),
        from_agent=_FROM_AUTO,
        subject=f"status:admitted — {job.subject[:80]}",
        body=compose_admit_body(base_admit_body, briefing),
    )
    if admit.status_code >= 400:
        from services.git_integration_worker.cursor_auto.terminal_post_outcome import (
            terminal_reason_for_status,
        )

        queue.mark_done(
            job.job_id,
            failed=True,
            terminal_reason=terminal_reason_for_status(admit.status_code),
        )
        return {
            "ok": False,
            "phase": "admit",
            "status_code": admit.status_code,
            "body": admit.body,
        }

    # Claim≠admit: stamp the admit clock on successful bus reply so observers
    # can distinguish wedged-pre-admit from wedged-post-admit-pre-bind.
    get_ledger().mark_admitted(job.job_id)

    # Settle before any refusal branch: the superseded episode is void whatever
    # this job's own fate, so its writes must come back regardless.
    settlement = await settle_supersede(job)

    if effective_require_attended(job, directive):
        surface = attendance_surface(job, directive)
        logger.info(
            "cursor-auto require_attended short-circuit job=%s surface=%s "
            "preempted_gate_action=%s",
            job.job_id,
            surface,
            gate_plan["action"],
        )
        return await terminal_needs_attended(
            job,
            client=client,
            queue=queue,
            reason="operator_require_attended",
            gate_plan=gate_plan,
        )

    if contract == EXECUTE_CONTRACT:
        return await run_execute_in_seat(
            job,
            client=client,
            queue=queue,
            model=model,
            effort=effort,
            gate_plan=gate_plan,
        )

    if contract == PROPAGATE_CONTRACT:
        return await run_propagation_in_seat(
            job,
            client=client,
            queue=queue,
            model=model,
            effort=effort,
            gate_plan=gate_plan,
        )

    cdp_model = escalation.get("resolved_escalation")
    if cdp_model:
        lane_block = await _terminalize_cdp_lane_full_if_blocked(
            job,
            client=client,
            queue=queue,
            contract=contract,
            unattended=not effective_require_attended(job, directive),
        )
        if lane_block is not None:
            return lane_block

    if contract not in _NESTED_CONTRACTS:
        if cdp_model:
            cdp_effort = str(wire_effort.get("resolved_effort") or "") or None
            commissioned = await commission_cdp_escalation(
                job,
                model=str(cdp_model),
                reasoning_effort=cdp_effort,
            )
            if not commissioned.get("ok"):
                return await terminal_failed(
                    job,
                    client=client,
                    queue=queue,
                    summary=(
                        f"cdp escalation commission failed: {commissioned.get('error')}"
                    ),
                    extra=commissioned,
                )
            from services.git_integration_worker.cursor_auto.disposition_outcome import (
                m1_cdp_commission,
                outcome_disposition_for_stamp,
            )

            cdp_execution_id = commissioned.get("execution_id")
            emit_cdp_effort_bind(
                thread_id=job.thread_id,
                execution_id=str(cdp_execution_id or ""),
                model=str(cdp_model),
                requested_effort=str(wire_effort.get("requested") or ""),
                resolved_effort=str(cdp_effort or ""),
                lane="cursor-auto-cdp-escalation",
            )
            cdp_disposition = outcome_disposition_for_stamp(
                "dispatched-and-relayed",
                m1_satisfied=m1_cdp_commission(execution_id=cdp_execution_id),
            )
            cdp_payload: dict[str, Any] = {
                "summary": f"CDP escalation commissioned model={cdp_model}",
                "reason": "cdp_escalation_commissioned",
                "escalation_model": cdp_model,
                "execution_id": cdp_execution_id,
                "requested_effort": wire_effort.get("requested"),
                "resolved_effort": cdp_effort,
            }
            if cdp_disposition is not None:
                cdp_payload["disposition"] = cdp_disposition
            get_ledger().merge_record_json(
                job.job_id,
                {"escalation_harvest": "open", "cdp_execution_id": cdp_execution_id},
            )
            return await post_terminal_status(
                job,
                client=client,
                queue=queue,
                summary=f"CDP escalation commissioned model={cdp_model}",
                disposition=cdp_disposition,
                contract=contract,
                terminal_status="status:done",
                payload=cdp_payload,
            )
        return await terminal_in_seat(
            job,
            client=client,
            queue=queue,
            model=model,
            effort=effort,
            contract_info=contract_info,
            gate_plan=gate_plan,
        )

    if gate_plan["action"] == "in_seat":
        # Nested contracts refuse in-seat execution; nest_park is the capacity path.
        return await terminal_needs_attended(
            job,
            client=client,
            queue=queue,
            reason=NESTED_IN_SEAT_REASON,
            gate_plan=gate_plan,
        )

    nest_under = await resolve_nest_under(
        job,
        client=client,
        queue=queue,
        gate_plan=gate_plan,
        work_bounded=work_bounded,
        contract=contract,
    )
    if isinstance(nest_under, dict):
        return nest_under

    read_only = contract in {"ask", "recon"}
    resolved_lane, _lane_reason = resolve_nested_checkout_lane(
        job, read_only=read_only
    )
    message = build_sdk_message(
        job.body,
        contract=contract,
        lane=resolved_lane,
    )
    if settlement is not None:
        message = f"{compose_supersede_preamble(settlement)}\n\n{message}"
    if queue.is_superseded(job.job_id):
        return await post_superseded_terminal(
            job, client=client, queue=queue, dispatch_id=None
        )

    knobs = compose_model_knobs(model, effort, contract=contract)
    relay_ctx = CloseoutRelayContext(
        worker_id=worker_id,
        worker_started_at=worker_started_at,
        admission_controller=admission_controller,
        skip_outbox=queue.is_superseded(job.job_id),
    )
    if cdp_model:
        cdp_effort = str(wire_effort.get("resolved_effort") or "") or None
        commissioned = await commission_cdp_escalation(
            job,
            model=str(cdp_model),
            reasoning_effort=cdp_effort,
        )
        if not commissioned.get("ok"):
            return await terminal_failed(
                job,
                client=client,
                queue=queue,
                summary=(
                    f"cdp escalation commission failed: {commissioned.get('error')}"
                ),
                extra=commissioned,
            )
        emit_cdp_effort_bind(
            thread_id=job.thread_id,
            execution_id=str(commissioned.get("execution_id") or ""),
            model=str(cdp_model),
            requested_effort=str(wire_effort.get("requested") or ""),
            resolved_effort=str(cdp_effort or ""),
            lane="cursor-auto-cdp-escalation-nested",
        )
        get_ledger().merge_record_json(
            job.job_id,
            {
                "escalation_harvest": "open",
                "cdp_execution_id": commissioned.get("execution_id"),
            },
        )
    submit = await submit_nested_dispatch(
        job,
        model_id=str(model["resolved_model_id"]),
        handoff_contract=handoff_contract,
        message=message,
        nest_under=nest_under,
        model_knobs=knobs or None,
        read_only=True if read_only else None,
        relay_ctx=relay_ctx,
    )
    if submit.get("reason") == "worker_draining":
        return await terminal_failed(
            job,
            client=client,
            queue=queue,
            summary="git_integration_worker draining — re-send DIRECTIVE after restart",
            extra=submit,
        )
    # Auto POSTs the worker directly, so Stargate's sdk_cost_risk guard never sees
    # this bind — announce it here or premium spend on this lane stays invisible.
    maybe_emit_premium_bind(
        thread_id=job.thread_id,
        dispatch_id=str(submit.get("dispatch_id") or ""),
        model=str(model["resolved_model_id"]),
        handoff_contract=handoff_contract,
        lane="cursor-auto-executor",
        knobs=knobs,
    )
    if not submit.get("ok"):
        return await terminal_failed(
            job,
            client=client,
            queue=queue,
            summary=f"nested dispatch submit failed: {submit.get('error')}",
            extra=submit,
        )

    dispatch_id = str(submit["dispatch_id"])
    if cdp_model:
        CursorDispatchLedger.instance().merge_record_json(
            dispatch_id=dispatch_id,
            patch={"escalation_harvest": "open"},
        )
    progress = ProgressEmitter(job, client=client)
    polled = await poll_dispatch_terminal_with_liveness(
        thread_id=job.thread_id,
        dispatch_id=dispatch_id,
        superseded=lambda: queue.is_superseded(job.job_id),
        on_tick=progress.maybe_emit,
    )
    if polled.get("superseded"):
        _close_dispatch_ticket(
            admission_controller, dispatch_id, terminal_status="superseded"
        )
        return await post_superseded_terminal(
            job, client=client, queue=queue, dispatch_id=dispatch_id
        )
    if not polled.get("terminal"):
        _close_dispatch_ticket(
            admission_controller, dispatch_id, terminal_status="failed"
        )
        return await terminal_failed(
            job,
            client=client,
            queue=queue,
            summary="nested dispatch poll timeout",
            extra=polled,
            dispatch_id=dispatch_id,
        )

    terminal_status = str(polled.get("status") or "failed")
    # Claim-window cut: exclude from supersede *without* mark_done — keep
    # claimed so relay death stays noticeable; CLOSEOUT path terminalizes later.
    queue.mark_nested_sdk_finished(job.job_id)
    get_ledger().set_relay_phase(job.job_id, relay_phase=RELAY_PHASE_SDK_TERMINAL)
    sdk_body = await fetch_sdk_closeout_body(
        thread_id=job.thread_id,
        dispatch_id=dispatch_id,
        bus=client,
    )

    if contract == "confer":
        return await relay_confer_outcome(
            job,
            client=client,
            queue=queue,
            dispatch_id=dispatch_id,
            model=model,
            effort=effort,
            gate_plan=gate_plan,
            sdk_body=sdk_body,
            terminal_status=terminal_status,
        )

    second_read = await maybe_run_second_read(
        job,
        contract=contract,
        terminal_status=terminal_status,
        sdk_body=sdk_body,
        executor_model=str(model["resolved_model_id"]),
        executor_dispatch_id=dispatch_id,
        density=directive.density if directive is not None else None,
        bus=client,
        superseded=lambda: queue.is_superseded(job.job_id),
    )

    return await relay_closeout_outcome(
        job,
        client=client,
        queue=queue,
        dispatch_id=dispatch_id,
        model=model,
        effort=effort,
        gate_plan=gate_plan,
        contract_info=contract_info,
        sdk_body=sdk_body,
        terminal_status=terminal_status,
        nest_under=nest_under,
        execution_id=str(submit.get("execution_id") or f"exec-{dispatch_id}"),
        second_read=second_read,
        relay_ctx=relay_ctx,
        admission_controller=admission_controller,
    )


async def _terminalize_cdp_lane_full_if_blocked(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    contract: str,
    unattended: bool,
) -> dict[str, Any] | None:
    """Refuse escalation when CDP lane is at soft (unattended) or hard limit."""
    snap = read_cdp_lane_snapshot()
    refuse, lane = escalation_lane_refusal(snap, unattended=unattended)
    if not refuse:
        return None
    free_slots = snap.get("free_slots", 0)
    summary = f"cdp lane full ({lane}); free_slots={free_slots} — escalation refused"
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="blocked",
        contract=contract,
        terminal_status="status:blocked",
        payload={
            "summary": summary,
            "reason": "cdp_lane_full",
            "lane": lane,
            "free_slots": free_slots,
        },
        failed=True,
    )
