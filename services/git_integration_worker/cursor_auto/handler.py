"""Auto job processor — admit → nested SDK dispatch → operator CLOSEOUT + WAKE."""

from __future__ import annotations

from typing import Any

from agent_seat.registry import normalize_bus_address
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.admit_gates import (
    blocking_admit_gate,
)
from services.git_integration_worker.cursor_auto.cdp_escalation import (
    commission_cdp_escalation,
    escalation_lane_refusal,
    read_cdp_lane_snapshot,
)
from services.git_integration_worker.cursor_auto.directive import (
    attendance_surface,
    build_sdk_message,
    effective_contract,
    effective_require_attended,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.dispatch_progress import (
    ProgressEmitter,
)
from services.git_integration_worker.cursor_auto.episode_briefing import (
    compose_admit_body,
    maybe_briefing_for_admit,
)
from services.git_integration_worker.cursor_auto.execute_admission import (
    EXECUTE_CONTRACT,
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
from services.git_integration_worker.cursor_auto.nested_outcome import (
    relay_closeout_outcome,
    relay_confer_outcome,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    CloseoutRelayContext,
    fetch_sdk_closeout_body,
    poll_dispatch_terminal,
    submit_nested_dispatch,
)
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PROPAGATE_CONTRACT,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, get_queue
from services.git_integration_worker.cursor_auto.reflex_events import (
    maybe_emit_premium_bind,
)
from services.git_integration_worker.cursor_auto.reflex_read import (
    maybe_run_second_read,
)
from services.git_integration_worker.cursor_auto.supersede import (
    compose_supersede_preamble,
    post_superseded_terminal,
    settle_supersede,
)
from services.git_integration_worker.cursor_auto.wire_map import (
    BINDABLE_EFFORT_VALUES,
    admit_effort_override_rule_line,
    admit_model_override_rule_line,
    admit_model_pin_flags,
    assess_effort_pin,
    assess_escalation_pin,
    assess_model_pin,
    compose_model_knobs,
    resolve_contract_disposition,
    resolve_handoff_contract,
)
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)

logger = get_logger(__name__)

_FROM_AUTO = "cursor-auto"
_NESTED_CONTRACTS = frozenset({"confer", "implement", "investigate", "verify"})


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
    directive = parse_request_body(job.body)
    contract = effective_contract(job.contract, job.body)
    # Downstream closeout/journal/meta read job.contract — stamp effective.
    job.contract = contract
    expired = await deadline_terminal(job, client=client, queue=queue)
    if expired is not None:
        return expired
    model, model_block = assess_model_pin(
        job.desired_model,
        contract=contract,
        body=job.body,
    )
    if model_block is not None:
        return await post_terminal_status(
            job,
            client=client,
            queue=queue,
            summary=model_block,
            disposition="blocked",
            contract=contract,
            terminal_status="status:blocked",
            payload={
                "summary": model_block,
                "reason": "model_pin_refused",
                "requested_model": model.get("requested"),
                "bindable": list(model.get("bindable") or ()),
            },
            failed=True,
        )
    effort, effort_block = assess_effort_pin(job.desired_effort, body=job.body)
    if effort_block is not None:
        return await post_terminal_status(
            job,
            client=client,
            queue=queue,
            summary=effort_block,
            disposition="blocked",
            contract=contract,
            terminal_status="status:blocked",
            payload={
                "summary": effort_block,
                "reason": "effort_pin_refused",
                "requested_effort": effort.get("requested"),
                "bindable": list(BINDABLE_EFFORT_VALUES),
            },
            failed=True,
        )
    escalation, escalation_block = assess_escalation_pin(
        job.escalation,
        body=job.body,
    )
    if escalation_block is not None:
        return await post_terminal_status(
            job,
            client=client,
            queue=queue,
            summary=escalation_block,
            disposition="blocked",
            contract=contract,
            terminal_status="status:blocked",
            payload={
                "summary": escalation_block,
                "reason": "escalation_refused",
                "requested_escalation": escalation.get("requested"),
                "bindable": list(escalation.get("bindable") or ()),
            },
            failed=True,
        )
    contract_info = resolve_contract_disposition(contract)
    handoff_contract = resolve_handoff_contract(contract)
    if directive is not None or contract in _NESTED_CONTRACTS or contract in {
        EXECUTE_CONTRACT,
        PROPAGATE_CONTRACT,
    }:
        blocked = await blocking_admit_gate(job, client=client, queue=queue)
        if blocked is not None:
            return blocked

    work_bounded = contract == "answer" or (
        directive is not None and directive.density == "sparse"
    )
    gate_plan = prefer_dispatch_over_park(
        plan_nested_dispatch(work_bounded=work_bounded),
        work_bounded=work_bounded,
    )

    base_admit_body = (
        "Auto admitted lane:cursor-auto request.\n"
        f"requested_model={model['requested']} "
        f"resolved={model['resolved_model_id']}\n"
        f"model_honored={model['honored']}\n"
        f"requested_effort={effort['requested']} "
        f"resolved={effort['resolved_effort']}\n"
        f"requested_escalation={escalation['requested'] or '(none)'} "
        f"resolved={escalation.get('resolved_escalation') or '(none)'}\n"
        f"contract={contract_info['contract']} "
        f"handoff={handoff_contract}\n"
        f"gate_plan={gate_plan['action']}\n"
        f"gate_occupancy_source={gate_plan.get('gate', {}).get('occupancy_source', 'gate_only')}\n"
        f"directive={directive is not None}\n"
        f"continuity_hop={str(bool(job.continuity_hop)).lower()} "
        f"matched_token={job.continuity_matched_token or 'none'}"
    )
    override_rule = admit_model_override_rule_line(model)
    if override_rule is not None:
        base_admit_body += f"\n{override_rule}"
    effort_rule = admit_effort_override_rule_line(effort)
    if effort_rule is not None:
        base_admit_body += f"\n{effort_rule}"
    pin_flags = admit_model_pin_flags(model, effort)
    if pin_flags:
        base_admit_body += "\nflags: " + "; ".join(pin_flags)
    briefing = await maybe_briefing_for_admit(job.thread_id, contract=contract)
    admit = await client.reply(
        thread_id=job.thread_id,
        to_agent=normalize_bus_address(job.from_agent),
        from_agent=_FROM_AUTO,
        subject=f"status:admitted — {job.subject[:80]}",
        body=compose_admit_body(base_admit_body, briefing),
    )
    if admit.status_code >= 400:
        queue.mark_done(job.job_id, failed=True)
        return {
            "ok": False,
            "phase": "admit",
            "status_code": admit.status_code,
            "body": admit.body,
        }

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
            commissioned = await commission_cdp_escalation(
                job,
                model=str(cdp_model),
                reasoning_effort=str(effort.get("resolved_effort") or "") or None,
            )
            if not commissioned.get("ok"):
                return await terminal_failed(
                    job,
                    client=client,
                    queue=queue,
                    summary=(
                        "cdp escalation commission failed: "
                        f"{commissioned.get('error')}"
                    ),
                    extra=commissioned,
                )
            return await post_terminal_status(
                job,
                client=client,
                queue=queue,
                summary=f"CDP escalation commissioned model={cdp_model}",
                disposition="dispatched-and-relayed",
                contract=contract,
                terminal_status="status:done",
                payload={
                    "summary": f"CDP escalation commissioned model={cdp_model}",
                    "reason": "cdp_escalation_commissioned",
                    "escalation_model": cdp_model,
                    "execution_id": commissioned.get("execution_id"),
                },
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

    nest_under = await _resolve_nest_under(
        job,
        client=client,
        queue=queue,
        gate_plan=gate_plan,
        work_bounded=work_bounded,
    )
    if isinstance(nest_under, dict):
        return nest_under

    message = build_sdk_message(job.body, contract=contract)
    if settlement is not None:
        message = f"{compose_supersede_preamble(settlement)}\n\n{message}"
    if queue.is_superseded(job.job_id):
        return await post_superseded_terminal(
            job, client=client, queue=queue, dispatch_id=None
        )

    knobs = compose_model_knobs(model, effort)
    relay_ctx = CloseoutRelayContext(
        worker_id=worker_id,
        worker_started_at=worker_started_at,
        admission_controller=admission_controller,
        skip_outbox=queue.is_superseded(job.job_id),
    )
    if cdp_model:
        commissioned = await commission_cdp_escalation(
            job,
            model=str(cdp_model),
            reasoning_effort=str(effort.get("resolved_effort") or "") or None,
        )
        if not commissioned.get("ok"):
            return await terminal_failed(
                job,
                client=client,
                queue=queue,
                summary=(
                    "cdp escalation commission failed: "
                    f"{commissioned.get('error')}"
                ),
                extra=commissioned,
            )
    submit = await submit_nested_dispatch(
        job,
        model_id=str(model["resolved_model_id"]),
        handoff_contract=handoff_contract,
        message=message,
        nest_under=nest_under,
        model_knobs=knobs or None,
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
    progress = ProgressEmitter(job, client=client)
    polled = await poll_dispatch_terminal(
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


async def _resolve_nest_under(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    gate_plan: dict[str, Any],
    work_bounded: bool,
) -> str | None | dict[str, Any]:
    """Resolve the park parent for ``nest_park``; a dict means terminal refusal."""
    if gate_plan["action"] != "nest_park":
        return None
    snap = CursorDispatchLedger.instance().lease_snapshot()
    nest_under = snap.get("holder_dispatch_id")
    if nest_under:
        return str(nest_under)
    replan = prefer_dispatch_over_park(
        {**gate_plan, "action": "in_seat", "reason": "nest_park_without_holder"},
        work_bounded=work_bounded,
    )
    gate_plan.update(replan)
    if replan["action"] == "dispatch_now":
        return None
    return await terminal_needs_attended(
        job,
        client=client,
        queue=queue,
        reason="nest_park_without_holder",
        gate_plan=gate_plan,
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
    summary = (
        f"cdp lane full ({lane}); free_slots={free_slots} — escalation refused"
    )
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
