"""Relay a terminal nested SDK episode back to the operator seat.

Two shapes: ``confer`` posts prose without a CLOSEOUT envelope, everything else
selects a §2 closeout payload and follows it with WAKE + substrate feedback.
"""

from __future__ import annotations

from typing import Any

from claude_bundles.lane_a_closeout_checkpoint import (
    validate_lane_a_closeout_checkpoint,
)
from systems.frontier_consult.story_wire import (
    build_association_envelope,
    safe_emit_observation,
)

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_auto.caller_auditable import (
    caller_auditable,
)
from services.git_integration_worker.cursor_auto.closeout_relay import (
    read_repo_closeout_sidecar,
    select_closeout_relay_payload,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    RELAY_PARSE_FAILED_STATUS,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_spill import (
    promote_clamped_closeout_to_cortex,
)
from services.git_integration_worker.cursor_auto.directive import (
    corpus_guard_uris,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.closeout_tree_state import (
    compute_closeout_tree_state,
    strip_deployment_state_line,
)
from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
    derive_tree_residue,
    inject_checkpoint_line,
    inject_tree_residue_line,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    post_operator_closeout,
    post_operator_confer,
    post_operator_wake,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.reflex_packet import (
    inject_second_read_block,
)
from services.git_integration_worker.cursor_auto.reflex_read import ReflexOutcome
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


def _relay_model_bind(model: dict[str, Any]) -> dict[str, str | None]:
    requested = str(model.get("requested") or "")
    resolved = str(model.get("resolved_model_id") or "")
    return {
        "requested_model": requested or None,
        "resolved_model": resolved or None,
    }


def _journal_terminal_status(*, payload_status: str, failed: bool) -> str:
    if failed:
        return "status:failed"
    if payload_status == RELAY_PARSE_FAILED_STATUS:
        return "status:relay_parse_failed"
    if payload_status != "complete":
        return "status:partial"
    return "status:done"


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
        caller_auditable=caller_auditable(from_agent=job.from_agent),
        **_relay_model_bind(model),
    )
    payload = await promote_clamped_closeout_to_cortex(
        payload,
        dispatch_id=dispatch_id,
        thread_id=job.thread_id,
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
    journal_status = _journal_terminal_status(payload_status=payload.status, failed=failed)
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
            "request_id": job.request_id,
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
    second_read: ReflexOutcome | None = None,
) -> dict[str, Any]:
    """Select the closeout payload, relay it, then WAKE + substrate feedback."""
    sidecar_text = read_repo_closeout_sidecar(dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=sdk_body,
        sidecar_text=sidecar_text,
        ledger_status=terminal_status,
        dispatch_id=dispatch_id,
        caller_auditable=caller_auditable(from_agent=job.from_agent),
        **_relay_model_bind(model),
    )
    payload = await promote_clamped_closeout_to_cortex(
        payload,
        dispatch_id=dispatch_id,
        thread_id=job.thread_id,
    )
    source_repo = load_config().source_repo
    residue_before = derive_tree_residue(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
    )
    relay_body = strip_deployment_state_line(payload.body)
    relay_body = inject_tree_residue_line(relay_body, count=residue_before.count)
    tree_state = compute_closeout_tree_state(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
        wrapper_text=sdk_body,
    )
    relay_body = inject_checkpoint_line(relay_body, value=tree_state.checkpoint)
    if second_read is not None:
        relay_body = inject_second_read_block(
            relay_body,
            text=second_read.text,
            model=second_read.model,
            reflex_dispatch_id=second_read.dispatch_id,
            reason=second_read.reason,
        )
    checkpoint_verdict = validate_lane_a_closeout_checkpoint(
        body=relay_body,
        require_closeout_type=False,
    )
    if not checkpoint_verdict.ok:
        from services.git_integration_worker.cursor_auto.fix_hints import (
            LANE_A_CHECKPOINT_FIX_HINT,
        )
        from services.git_integration_worker.cursor_auto.handler_terminal import (
            post_terminal_status,
        )

        summary = (
            "Lane-A CLOSEOUT refused — checkpoint disposition missing or invalid "
            f"({checkpoint_verdict.reason})."
        )
        blocked = await post_terminal_status(
            job,
            client=client,
            queue=queue,
            summary=summary,
            disposition="blocked",
            contract=job.contract,
            terminal_status="status:blocked",
            payload={
                "summary": summary,
                "reason": checkpoint_verdict.reason or "lane_a_checkpoint_missing",
                "missed_tokens": list(checkpoint_verdict.missed_tokens),
                "fix_hint": LANE_A_CHECKPOINT_FIX_HINT,
                "tree_residue_before": residue_before.count,
            },
            failed=True,
            dispatch_id=dispatch_id,
        )
        queue.mark_done(job.job_id, failed=True)
        return {
            "ok": False,
            "phase": "nested_dispatch",
            "terminal_status": terminal_status,
            "closeout_status": "blocked",
            "dispatch_id": dispatch_id,
            "blocked": blocked,
            "tree_residue_before": residue_before.count,
        }
    relay = await post_operator_closeout(
        job,
        status=payload.status,
        dispatch_id=dispatch_id,
        model_id=str(model["resolved_model_id"]),
        sdk_body=sdk_body,
        closeout_body=relay_body,
        closeout_source=payload.source,
        relay_note=payload.relay_note,
        deployment_state=tree_state.deployment_state,
        extra={
            "gate_plan": gate_plan,
            "terminal_status": terminal_status,
            "nest_under": nest_under,
            "request_id": job.request_id,
            "second_read": (
                f"{second_read.model}@{second_read.dispatch_id}:{second_read.reason}"
                if second_read is not None
                else None
            ),
            "tree_residue_before": residue_before.count,
            "tree_residue_after": derive_tree_residue(
                source_repo=source_repo,
                dispatch_id=dispatch_id,
            ).count,
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
            closeout_body=payload.body_full or payload.body,
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
                job_subject=str(job.subject or ""),
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
        terminal_status=_journal_terminal_status(payload_status=payload.status, failed=failed),
        disposition=str(contract_info["disposition_hint"]),
        extra={
            "closeout_source": payload.source,
            "closeout_status": payload.status,
            "request_id": job.request_id,
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
        "second_read": second_read.dispatch_id if second_read is not None else None,
    }
