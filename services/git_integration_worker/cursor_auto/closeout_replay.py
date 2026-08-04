"""Boot-time replay of persisted cursor-auto CLOSEOUT envelopes."""

from __future__ import annotations

import re
from typing import Any

from claude_bundles.lane_a_closeout_checkpoint import (
    validate_lane_a_closeout_checkpoint,
)
from universal_logging import get_logger

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_auto.closeout_bus_scan import (
    fetch_turns_from,
    find_closeout_for_dispatch,
)
from services.git_integration_worker.cursor_auto.closeout_outbox import (
    OutboxRow,
    get_outbox_store,
)
from services.git_integration_worker.cursor_auto.closeout_outbox_events import (
    emit_closeout_replay_abandoned,
    emit_closeout_replay_deferred,
    emit_closeout_replay_discarded,
    emit_closeout_replay_skipped,
    emit_closeout_replay_suppressed_loss_report,
    emit_closeout_replayed,
)
from services.git_integration_worker.cursor_auto.closeout_relay import (
    read_repo_closeout_sidecar,
    select_closeout_relay_payload,
)
from services.git_integration_worker.cursor_auto.closeout_tree_state import (
    compute_closeout_tree_state,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    RELAY_PHASE_CLOSEOUT_POSTED,
    RELAY_PHASE_DISPATCHED,
    RELAY_PHASE_NONE,
    RELAY_PHASE_SDK_TERMINAL,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
    derive_tree_residue,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    fetch_sdk_closeout_body,
    post_operator_closeout,
)
from services.git_integration_worker.cursor_auto.queue import get_queue
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_events import emit_sdk_closeout_relayed

logger = get_logger(__name__)

_MAX_REPLAY_ATTEMPTS = 3
_REPLAYED_HEADER = "replayed_after_restart: true"
_TREE_STATE_PREFIX = "tree_state_at_replay:"


async def startup_closeout_outbox_replay(app: Any) -> None:
    """Deliver persisted CLOSEOUT envelopes before open-job terminalize."""
    worker_id = str(getattr(app.state, "worker_id", "") or "")
    worker_boot_ts = str(getattr(app.state, "worker_boot_ts", "") or "")
    if not worker_id:
        return
    store = get_outbox_store()
    client = CursorBusClient()
    for row in store.list_replayable(exclude_worker_id=worker_id):
        await _replay_one_row(
            row,
            client=client,
            store=store,
            worker_id=worker_id,
        )
    await _triage_sdk_terminal_without_outbox(
        app,
        client=client,
        worker_id=worker_id,
        worker_boot_ts=worker_boot_ts,
    )


async def _replay_one_row(
    row: OutboxRow,
    *,
    client: CursorBusClient,
    store: Any,
    worker_id: str,
) -> None:
    ledger = get_ledger()
    job_state = ledger.read_relay_state(row.job_id)
    if job_state.get("status") == "superseded":
        store.discard(row.dispatch_id, reason="superseded")
        emit_closeout_replay_discarded(
            dispatch_id=row.dispatch_id,
            thread_id=row.thread_id,
            discarded_reason="superseded",
        )
        return

    turns, scan_err = await fetch_turns_from(
        row.thread_id,
        after_turn=row.request_turn,
    )
    if turns is None:
        attempts = store.increment_attempts(row.dispatch_id)
        emit_closeout_replay_deferred(
            dispatch_id=row.dispatch_id,
            thread_id=row.thread_id,
            attempts=attempts,
            reason=scan_err or "bus_unreachable",
        )
        return

    if find_closeout_for_dispatch(turns, dispatch_id=row.dispatch_id):
        store.mark_confirmed(row.dispatch_id)
        ledger.set_relay_phase(row.job_id, relay_phase=RELAY_PHASE_CLOSEOUT_POSTED)
        emit_closeout_replay_skipped(
            dispatch_id=row.dispatch_id,
            thread_id=row.thread_id,
            confirmed_by="bus_scan",
        )
        emit_closeout_replay_suppressed_loss_report(
            dispatch_id=row.dispatch_id,
            job_id=row.job_id,
            thread_id=row.thread_id,
        )
        return

    body = _compose_replay_body(row)
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    if not verdict.ok:
        attempts = store.increment_attempts(row.dispatch_id)
        emit_closeout_replay_deferred(
            dispatch_id=row.dispatch_id,
            thread_id=row.thread_id,
            attempts=attempts,
            reason=verdict.reason or "checkpoint_invalid_at_replay",
        )
        return

    resp = await client.reply(
        thread_id=row.thread_id,
        to_agent=row.to_agent,
        from_agent=row.from_agent,
        subject=row.subject,
        body=body,
        allow_long_body=True,
    )
    relay = {
        "ok": resp.status_code < 400,
        "status_code": resp.status_code,
        "body": resp.body,
        "reason": None if resp.status_code < 400 else resp.body,
    }
    job = ledger.get_by_dispatch_id(row.dispatch_id)
    if not relay.get("ok"):
        attempts = store.increment_attempts(row.dispatch_id)
        if attempts >= _MAX_REPLAY_ATTEMPTS:
            store.abandon(row.dispatch_id)
            emit_closeout_replay_abandoned(
                dispatch_id=row.dispatch_id,
                thread_id=row.thread_id,
                envelope_sha256=row.envelope_sha256,
                attempts=attempts,
            )
            if job is not None:
                await _post_abandon_notice(job, row, client=client)
        else:
            emit_closeout_replay_deferred(
                dispatch_id=row.dispatch_id,
                thread_id=row.thread_id,
                attempts=attempts,
                reason=str(relay.get("reason") or relay.get("body") or "post_failed"),
            )
        return

    store.mark_posted(row.dispatch_id)
    if job is not None:
        ledger.set_relay_phase(row.job_id, relay_phase=RELAY_PHASE_CLOSEOUT_POSTED)
    stored_cp, stored_tr, recomputed_cp, recomputed_tr = _probe_tree_drift(row)
    emit_closeout_replayed(
        dispatch_id=row.dispatch_id,
        thread_id=row.thread_id,
        envelope_sha256=row.envelope_sha256,
        stored_checkpoint=stored_cp,
        recomputed_checkpoint=recomputed_cp,
        stored_tree_residue=stored_tr,
        recomputed_tree_residue=recomputed_tr,
    )
    if job is not None:
        from services.git_integration_worker.cursor_auto.cse_wake_delivery import (
            pay_wake_unit,
        )

        await pay_wake_unit(
            job,
            dispatch_id=row.dispatch_id,
            request_turn=str(row.request_turn),
            closeout_status=row.closeout_status,
            bus=client,
        )
    emit_sdk_closeout_relayed(
        dispatch_id=row.dispatch_id,
        thread_id=row.thread_id,
        execution_id=f"exec-{row.dispatch_id}",
        closeout_status=row.closeout_status,
        receipt_path="",
        asked_by="",
        purpose="",
        story_id="",
    )
    emit_closeout_replay_suppressed_loss_report(
        dispatch_id=row.dispatch_id,
        job_id=row.job_id,
        thread_id=row.thread_id,
    )


def _compose_replay_body(row: OutboxRow) -> str:
    body = row.envelope_body
    delta = _tree_state_delta_line(row)
    if not delta:
        if _REPLAYED_HEADER not in body:
            return _insert_after_status(body, _REPLAYED_HEADER)
        return body
    lines_to_insert = [_REPLAYED_HEADER, delta]
    for line in lines_to_insert:
        if line.split(":")[0] in body:
            continue
        body = _insert_after_status(body, line)
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    if not verdict.ok:
        return row.envelope_body
    return body


def _insert_after_status(body: str, line: str) -> str:
    match = re.search(r"(?im)^status:\s*\S+\s*$", body)
    if match is None:
        return body.rstrip() + f"\n{line}\n"
    insert_at = match.end()
    return f"{body[:insert_at]}\n{line}{body[insert_at:]}"


def _tree_state_delta_line(row: OutboxRow) -> str | None:
    source_repo = load_config().source_repo
    tree_state = compute_closeout_tree_state(
        source_repo=source_repo,
        dispatch_id=row.dispatch_id,
        wrapper_text=None,
    )
    residue = derive_tree_residue(
        source_repo=source_repo,
        dispatch_id=row.dispatch_id,
    )
    stored_cp = row.checkpoint_value
    stored_tr = row.tree_residue
    if stored_cp == tree_state.checkpoint and stored_tr == residue.count:
        return None
    return (
        f"{_TREE_STATE_PREFIX} checkpoint={tree_state.checkpoint} "
        f"tree_residue={residue.count} "
        "(compose-time values stand above; tree moved during the outage)"
    )


def _probe_tree_drift(row: OutboxRow) -> tuple[str | None, int | None, str | None, int | None]:
    source_repo = load_config().source_repo
    tree_state = compute_closeout_tree_state(
        source_repo=source_repo,
        dispatch_id=row.dispatch_id,
        wrapper_text=None,
    )
    residue = derive_tree_residue(
        source_repo=source_repo,
        dispatch_id=row.dispatch_id,
    )
    return (
        row.checkpoint_value,
        row.tree_residue,
        tree_state.checkpoint,
        residue.count,
    )


def _extract_model(envelope_body: str) -> str:
    match = re.search(r"(?im)^model:\s*(\S+)", envelope_body)
    return match.group(1) if match else "auto"


async def _post_abandon_notice(
    job: Any,
    row: OutboxRow,
    *,
    client: CursorBusClient,
) -> None:
    summary = (
        f"Closeout replay abandoned after {_MAX_REPLAY_ATTEMPTS} attempts — "
        f"dispatch_id={row.dispatch_id} envelope_sha256={row.envelope_sha256}"
    )
    from services.git_integration_worker.cursor_auto.handler_terminal import (
        post_terminal_status,
    )

    await post_terminal_status(
        job,
        client=client,
        queue=get_queue(),
        summary=summary,
        disposition="failed",
        contract=job.contract,
        terminal_status="status:failed",
        payload={"summary": summary, "dispatch_id": row.dispatch_id},
        failed=True,
        dispatch_id=row.dispatch_id,
    )


async def _triage_sdk_terminal_without_outbox(
    app: Any,
    *,
    client: CursorBusClient,
    worker_id: str,
    worker_boot_ts: str,
) -> None:
    """AC-8: relay blocked/partial CLOSEOUT when SDK terminal but no envelope."""
    ledger = get_ledger()
    dispatch_ledger = CursorDispatchLedger.instance()
    for job in ledger.list_open():
        state = ledger.read_relay_state(job.job_id)
        phase = state.get("relay_phase") or RELAY_PHASE_NONE
        dispatch_id = state.get("dispatch_id")
        if phase != RELAY_PHASE_SDK_TERMINAL or not dispatch_id:
            continue
        if get_outbox_store().get(dispatch_id) is not None:
            continue
        row = dispatch_ledger.dispatch_status_by_id(dispatch_id=dispatch_id)
        terminal_status = str((row or {}).get("status") or "failed")
        sdk_body = await fetch_sdk_closeout_body(
            thread_id=job.thread_id,
            dispatch_id=dispatch_id,
            bus=client,
        )
        payload = select_closeout_relay_payload(
            sdk_body=sdk_body,
            sidecar_text=read_repo_closeout_sidecar(dispatch_id),
            ledger_status=terminal_status,
            dispatch_id=dispatch_id,
            caller_auditable=True,
        )
        await post_operator_closeout(
            job,
            status=payload.status if payload.status != "complete" else "partial",
            dispatch_id=dispatch_id,
            model_id="auto",
            sdk_body=sdk_body,
            closeout_body=payload.body,
            closeout_source=payload.source,
            relay_note="replayed_after_giw_restart",
            bus=client,
            skip_outbox_persist=True,
            replay_mode=True,
        )


def job_should_skip_loss_report(job_id: str) -> bool:
    """True when outbox confirms delivery for this job."""
    return get_outbox_store().has_delivered_for_job(job_id)


def job_has_pending_outbox(job_id: str) -> bool:
    """True when a pending outbox row defers terminalize (fail-closed)."""
    return get_outbox_store().has_pending_for_job(job_id)


def relay_phase_for_job(job_id: str) -> tuple[str | None, str | None]:
    state = get_ledger().read_relay_state(job_id)
    return state.get("dispatch_id"), state.get("relay_phase")


def is_never_dispatched(job_id: str) -> bool:
    dispatch_id, phase = relay_phase_for_job(job_id)
    if not dispatch_id:
        return True
    if phase in (RELAY_PHASE_NONE, RELAY_PHASE_DISPATCHED):
        row = CursorDispatchLedger.instance().dispatch_status_by_id(
            dispatch_id=dispatch_id
        )
        return row is None
    return False
