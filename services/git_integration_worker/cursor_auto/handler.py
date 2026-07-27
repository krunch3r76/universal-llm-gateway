"""Auto job processor — admit → nested SDK dispatch → operator CLOSEOUT + WAKE."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.admit_gates import (
    blocking_admit_gate,
)
from services.git_integration_worker.cursor_auto.directive import (
    attendance_surface,
    build_sdk_message,
    effective_require_attended,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.episode_briefing import (
    compose_admit_body,
    maybe_briefing_for_admit,
)
from services.git_integration_worker.cursor_auto.gate_serialize import (
    NESTED_IN_SEAT_REASON,
    plan_nested_dispatch,
    prefer_dispatch_over_park,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    terminal_failed,
    terminal_in_seat,
    terminal_needs_attended,
)
from services.git_integration_worker.cursor_auto.nested_outcome import (
    relay_closeout_outcome,
    relay_confer_outcome,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    fetch_sdk_closeout_body,
    poll_dispatch_terminal,
    submit_nested_dispatch,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, get_queue
from services.git_integration_worker.cursor_auto.supersede import (
    compose_supersede_preamble,
    post_superseded_terminal,
    settle_supersede,
)
from services.git_integration_worker.cursor_auto.wire_map import (
    resolve_contract_disposition,
    resolve_desired_effort,
    resolve_desired_model,
    resolve_handoff_contract,
)
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)

logger = get_logger(__name__)

_FROM_AUTO = "cursor-auto"
_NESTED_CONTRACTS = frozenset({"confer", "implement", "investigate", "verify"})


async def process_job(
    job: AutoJob,
    *,
    bus: CursorBusClient | None = None,
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
    model = resolve_desired_model(job.desired_model, contract=job.contract)
    effort = resolve_desired_effort(job.desired_effort)
    contract_info = resolve_contract_disposition(job.contract)
    handoff_contract = resolve_handoff_contract(job.contract)
    directive = parse_request_body(job.body)
    if directive is not None:
        blocked = await blocking_admit_gate(job, client=client, queue=queue)
        if blocked is not None:
            return blocked

    work_bounded = job.contract == "answer" or (
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
        f"requested_effort={effort['requested']} "
        f"resolved={effort['resolved_effort']}\n"
        f"contract={contract_info['contract']} "
        f"handoff={handoff_contract}\n"
        f"gate_plan={gate_plan['action']}\n"
        f"directive={directive is not None}"
    )
    briefing = await maybe_briefing_for_admit(job.thread_id, contract=job.contract)
    admit = await client.reply(
        thread_id=job.thread_id,
        to_agent=job.from_agent,
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

    if job.contract not in _NESTED_CONTRACTS:
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

    message = build_sdk_message(job.body, contract=job.contract)
    if settlement is not None:
        message = f"{compose_supersede_preamble(settlement)}\n\n{message}"
    if queue.is_superseded(job.job_id):
        return await post_superseded_terminal(
            job, client=client, queue=queue, dispatch_id=None
        )

    submit = await submit_nested_dispatch(
        job,
        model_id=str(model["resolved_model_id"]),
        handoff_contract=handoff_contract,
        message=message,
        nest_under=nest_under,
        model_knobs=model.get("model_knobs"),
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
    polled = await poll_dispatch_terminal(
        thread_id=job.thread_id,
        dispatch_id=dispatch_id,
        superseded=lambda: queue.is_superseded(job.job_id),
    )
    if polled.get("superseded"):
        return await post_superseded_terminal(
            job, client=client, queue=queue, dispatch_id=dispatch_id
        )
    if not polled.get("terminal"):
        return await terminal_failed(
            job,
            client=client,
            queue=queue,
            summary="nested dispatch poll timeout",
            extra=polled,
            dispatch_id=dispatch_id,
        )

    terminal_status = str(polled.get("status") or "failed")
    sdk_body = await fetch_sdk_closeout_body(
        thread_id=job.thread_id,
        dispatch_id=dispatch_id,
        bus=client,
    )

    if job.contract == "confer":
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
