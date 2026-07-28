"""Relay a terminal nested SDK episode back to the operator seat.

Two shapes: ``confer`` posts prose without a CLOSEOUT envelope, everything else
selects a §2 closeout payload and follows it with WAKE + substrate feedback.
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_auto.closeout_relay import (
    read_repo_closeout_sidecar,
    select_closeout_relay_payload,
)
from services.git_integration_worker.cursor_auto.directive import (
    corpus_guard_uris,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    post_operator_closeout,
    post_operator_confer,
    post_operator_wake,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.substrate_feedback import (
    maybe_post_substrate_feedback,
)
from services.git_integration_worker.cursor_auto.work_journal import (
    append_journal_entry,
)
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)
from services.git_integration_worker.cursor_sdk_events import emit_sdk_closeout_relayed
from systems.frontier_consult.story_wire import (
    build_association_envelope,
    safe_emit_observation,
)


async def relay_confer_outcome(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    dispatch_id: str,
    model: dict[str, Any],
    effort: dict[str, Any],
    gate_plan: dict[str, Any],
    sdk_body: str | None,
    terminal_status: str,
) -> dict[str, Any]:
    """Select §2 closeout payload, apply confer fence, and relay to operator."""
    directive = parse_request_body(job.body)
    guard = corpus_guard_uris(directive)
    payload = select_closeout_relay_payload(
        sdk_body=sdk_body,
        sidecar_text=read_repo_closeout_sidecar(dispatch_id),
        ledger_status=terminal_status,
        dispatch_id=dispatch_id,
        guard_uris=guard,
    )
    fence_violation = "fence_violation:" in payload.body.lower()
    relay = await post_operator_confer(
        job,
        dispatch_id=dispatch_id,
        model_id=str(model["resolved_model_id"]),
        status=payload.status,
        closeout_body=payload.body,
        bus=client,
    )
    failed = not relay.get("ok") or terminal_status == "failed"
    queue.mark_done(job.job_id, failed=failed)
    journal_status = (
        "status:failed"
        if failed
        else ("status:partial" if payload.status != "complete" else "status:done")
    )
    disposition = "fence_violation" if fence_violation else "conferred"
    append_journal_entry(
        thread_id=job.thread_id,
        dispatch_id=dispatch_id,
        contract=job.contract,
        terminal_status=journal_status,
        disposition=disposition,
        extra={
            "closeout_source": payload.source,
            "closeout_status": payload.status,
            "fence_violation": fence_violation,
        },
    )
    wake = (
        await post_operator_wake(
            job,
            dispatch_id=dispatch_id,
            request_turn=str(job.turn_number),
            closeout_status=payload.status,
            bus=client,
        )
        if relay.get("ok")
        else {"ok": False, "skipped": True, "reason": "confer_not_ok"}
    )
    return {
        "ok": not failed,
        "phase": "nested_confer",
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


async def _emit_closeout_relayed_observation(
    job: AutoJob,
    *,
    dispatch_id: str,
    execution_id: str,
    closeout_status: str,
) -> None:
    def _emit() -> None:
        envelope = build_association_envelope(
            purpose_body=job.body,
            from_agent=job.from_agent,
            dispatch_id=dispatch_id,
        )
        emit_sdk_closeout_relayed(
            dispatch_id=dispatch_id,
            thread_id=job.thread_id,
            execution_id=execution_id,
            closeout_status=closeout_status,
            receipt_path=sidecar_workspaces_ref(dispatch_id),
            asked_by=envelope.asked_by,
            purpose=envelope.purpose,
            story_id=envelope.story_id,
        )

    safe_emit_observation(_emit, label="frontier.sdk.closeout.relayed")


async def relay_closeout_outcome(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    dispatch_id: str,
    model: dict[str, Any],
    effort: dict[str, Any],
    gate_plan: dict[str, Any],
    contract_info: dict[str, Any],
    sdk_body: str | None,
    terminal_status: str,
    nest_under: str | None,
    execution_id: str | None = None,
) -> dict[str, Any]:
    """Select the closeout payload, relay it, then WAKE + substrate feedback."""
    payload = select_closeout_relay_payload(
        sdk_body=sdk_body,
        sidecar_text=read_repo_closeout_sidecar(dispatch_id),
        ledger_status=terminal_status,
        dispatch_id=dispatch_id,
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
    resolved_execution_id = execution_id or f"exec-{dispatch_id}"
    await _emit_closeout_relayed_observation(
        job,
        dispatch_id=dispatch_id,
        execution_id=resolved_execution_id,
        closeout_status=payload.status,
    )
    if relay.get("ok"):
        wake = await post_operator_wake(
            job,
            dispatch_id=dispatch_id,
            request_turn=str(job.turn_number),
            closeout_status=payload.status,
            bus=client,
        )
        await maybe_post_substrate_feedback(
            job,
            sdk_body=sdk_body,
            closeout_body=payload.body,
            bus=client,
        )
        try:
            from pager_notify.closeout import notify_closeout_complete

            await notify_closeout_complete(
                thread_id=str(job.thread_id),
                status=str(payload.status or "complete"),
                dispatch_id=str(dispatch_id),
                closeout_body=str(payload.body or ""),
                sdk_body=str(sdk_body or ""),
            )
        except Exception:
            pass
    else:
        wake = {"ok": False, "skipped": True, "reason": "closeout_not_ok"}
    failed = not relay.get("ok") or terminal_status == "failed"
    append_journal_entry(
        thread_id=job.thread_id,
        dispatch_id=dispatch_id,
        contract=job.contract,
        terminal_status="status:done" if not failed else "status:failed",
        disposition=str(contract_info["disposition_hint"]),
        extra={
            "closeout_source": payload.source,
            "closeout_status": payload.status,
        },
    )
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
