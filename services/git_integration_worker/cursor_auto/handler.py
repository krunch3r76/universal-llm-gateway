"""Auto job processor — admit → nested SDK dispatch → operator CLOSEOUT + WAKE."""

from __future__ import annotations

import json
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.closeout_relay import (
    read_repo_closeout_sidecar,
    select_closeout_relay_payload,
)
from services.git_integration_worker.cursor_auto.directive import (
    build_sdk_message,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.gate_serialize import (
    plan_nested_dispatch,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    fetch_sdk_closeout_body,
    poll_dispatch_terminal,
    post_operator_closeout,
    post_operator_wake,
    submit_nested_dispatch,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, get_queue
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
_NESTED_CONTRACTS = frozenset({"implement", "investigate", "verify"})


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
    """
    client = bus or CursorBusClient()
    queue = get_queue()
    model = resolve_desired_model(job.desired_model, contract=job.contract)
    effort = resolve_desired_effort(job.desired_effort)
    contract_info = resolve_contract_disposition(job.contract)
    handoff_contract = resolve_handoff_contract(job.contract)
    directive = parse_request_body(job.body)
    work_bounded = job.contract == "answer" or (
        directive is not None and directive.density == "sparse"
    )
    gate_plan = plan_nested_dispatch(work_bounded=work_bounded)

    admit = await client.reply(
        thread_id=job.thread_id,
        to_agent=job.from_agent,
        from_agent=_FROM_AUTO,
        subject=f"status:admitted — {job.subject[:80]}",
        body=(
            "Auto admitted lane:cursor-auto request.\n"
            f"requested_model={model['requested']} "
            f"resolved={model['resolved_model_id']}\n"
            f"requested_effort={effort['requested']} "
            f"resolved={effort['resolved_effort']}\n"
            f"contract={contract_info['contract']} "
            f"handoff={handoff_contract}\n"
            f"gate_plan={gate_plan['action']}\n"
            f"directive={directive is not None}"
        ),
    )
    if admit.status_code >= 400:
        queue.mark_done(job.job_id, failed=True)
        return {
            "ok": False,
            "phase": "admit",
            "status_code": admit.status_code,
            "body": admit.body,
        }

    if job.contract not in _NESTED_CONTRACTS:
        return await _terminal_in_seat(
            job,
            client=client,
            queue=queue,
            model=model,
            effort=effort,
            contract_info=contract_info,
            gate_plan=gate_plan,
        )

    if gate_plan["action"] == "in_seat":
        return await _terminal_needs_attended(
            job,
            client=client,
            queue=queue,
            reason="gate_in_seat_fallback",
            gate_plan=gate_plan,
        )

    nest_under: str | None = None
    if gate_plan["action"] == "nest_park":
        snap = CursorDispatchLedger.instance().lease_snapshot()
        nest_under = snap.get("holder_dispatch_id")
        if not nest_under:
            return await _terminal_needs_attended(
                job,
                client=client,
                queue=queue,
                reason="nest_park_without_holder",
                gate_plan=gate_plan,
            )

    message = build_sdk_message(job.body, contract=job.contract)
    submit = await submit_nested_dispatch(
        job,
        model_id=str(model["resolved_model_id"]),
        handoff_contract=handoff_contract,
        message=message,
        nest_under=nest_under,
        model_knobs=model.get("model_knobs"),
    )
    if not submit.get("ok"):
        return await _terminal_failed(
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
    )
    if not polled.get("terminal"):
        return await _terminal_failed(
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
    payload = select_closeout_relay_payload(
        sdk_body=sdk_body,
        sidecar_text=read_repo_closeout_sidecar(dispatch_id),
        ledger_status=terminal_status,
    )
    relay = await post_operator_closeout(
        job,
        status=payload.status,
        dispatch_id=dispatch_id,
        model_id=str(model["resolved_model_id"]),
        sdk_body=sdk_body,
        closeout_body=payload.body,
        closeout_source=payload.source,
        extra={
            "gate_plan": gate_plan,
            "terminal_status": terminal_status,
            "nest_under": nest_under,
        },
        bus=client,
    )
    if relay.get("ok"):
        wake = await post_operator_wake(
            job,
            dispatch_id=dispatch_id,
            request_turn=str(job.turn_number),
            closeout_status=payload.status,
            bus=client,
        )
    else:
        wake = {"ok": False, "skipped": True, "reason": "closeout_not_ok"}
    failed = not relay.get("ok") or terminal_status == "failed"
    queue.mark_done(job.job_id, failed=failed)
    return {
        "ok": not failed,
        "phase": "nested_dispatch",
        "terminal_status": terminal_status,
        "closeout_status": payload.status,
        "closeout_source": payload.source,
        "dispatch_id": dispatch_id,
        "relay": relay,
        "wake": wake,
        "model": model,
        "effort": effort,
        "gate_plan": gate_plan,
    }


async def _terminal_in_seat(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    model: dict[str, Any],
    effort: dict[str, Any],
    contract_info: dict[str, Any],
    gate_plan: dict[str, Any],
) -> dict[str, Any]:
    summary = (
        f"v0 in-seat Auto handled contract={contract_info['contract']} "
        f"(gate_plan={gate_plan['action']})."
    )
    return await _post_status_done(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition=str(contract_info["disposition_hint"]),
        payload={
            "summary": summary,
            "disposition": contract_info["disposition_hint"],
            "requested_model": model["requested"],
            "actual_model": model["resolved_model_id"],
            "requested_effort": effort["requested"],
            "actual_effort": effort["resolved_effort"],
            "gate_plan": gate_plan,
            "request_turn": job.turn_number,
        },
    )


async def _terminal_needs_attended(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    reason: str,
    gate_plan: dict[str, Any],
) -> dict[str, Any]:
    summary = f"Auto cannot run unattended: {reason}"
    return await _post_status_done(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="needs-attended",
        terminal_status="status:needs-attended",
        payload={"summary": summary, "reason": reason, "gate_plan": gate_plan},
        failed=True,
    )


async def _terminal_failed(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    summary: str,
    extra: dict[str, Any],
    dispatch_id: str | None = None,
) -> dict[str, Any]:
    payload = {"summary": summary, **extra}
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    return await _post_status_done(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="blocked",
        terminal_status="status:failed",
        payload=payload,
        failed=True,
    )


async def _post_status_done(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    summary: str,
    disposition: str,
    payload: dict[str, Any],
    terminal_status: str = "status:done",
    failed: bool = False,
) -> dict[str, Any]:
    terminal = await client.reply(
        thread_id=job.thread_id,
        to_agent=job.from_agent,
        from_agent=_FROM_AUTO,
        subject=f"{terminal_status} — {job.subject[:60]}",
        body=json.dumps(payload, indent=2),
        allow_long_body=True,
    )
    queue.mark_done(job.job_id, failed=failed)
    return {
        "ok": not failed and terminal.status_code < 400,
        "phase": "terminal",
        "terminal_status": terminal_status,
        "disposition": disposition,
        "status_code": terminal.status_code,
        "summary": summary,
    }
