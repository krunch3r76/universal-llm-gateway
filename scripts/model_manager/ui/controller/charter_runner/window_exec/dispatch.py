"""Materialize + dispatch admitted windows (spec §B row 6 — Phase 3 cutover)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
from universal_logging import get_logger

from scripts.model_manager import observation_event as events
from scripts.model_manager.ui.charter_scoreboard_objective import (
    read_objective_for_root,
)

from .. import bus_client, dispatch_client, window_log
from ..admission import ADMISSION_SUBJECT_PREFIX, CapStore
from ..checkpoint_schema import (
    ParsedCheckpoint,
    parse_checkpoint,
    resolve_checkpoint_body,
)
from ..executor_routing import resolve_charter_executor
from ..gate_admission_defer import (
    admission_mode_requires_write_fence,
    clear_gate_defer,
    preflight_write_lease,
)
from ..r_corpus_sha import (
    clear_r_corpus_refusals,
    refuse_stale_r_admit,
    verify_r_corpus_sha,
)
from ..root_health import AdmitResult, FireAttemptOutcome
from ..root_ledger import RootLedgerRow
from ..telemetry import (
    emit_admission_defer_escalated,
    emit_admission_deferred_gate_held,
)
from ..window_terminal_contract import implement_ready_declared, is_pickup_append
from .materializer_autonomous import select_packet
from .materializer_consult import (
    LayerConsultGateUnresolvedError,
    _open_layer_consult_gate,
    consult_subject_for_arc,
    materialize_consult_packet,
)

logger = get_logger(__name__)


def _fail(outcome: FireAttemptOutcome, reason: str) -> AdmitResult:
    return AdmitResult(False, outcome, reason)


def _ok(*, dispatch_id: str | None = None, thread_id: str | None = None) -> AdmitResult:
    return AdmitResult(
        True,
        FireAttemptOutcome.FIRED,
        "",
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )


def _charter_objective_for_emit(root_id: str) -> str | None:
    """Scoreboard objective mirrored on ``manage.charter.tick.admitted`` when present."""
    return read_objective_for_root(root_id)


def count_admissions(turns: list[dict]) -> int:
    prefix = ADMISSION_SUBJECT_PREFIX.upper()
    return sum(
        1 for t in turns if str(t.get("subject") or "").upper().startswith(prefix)
    )


def latest_checkpoint(turns: list[dict]) -> dict | None:
    """Newest window-terminal CHECKPOINT (skip conveyor pickup appends).

    Pickup appends are tip-class for Next-pickup merge, but admit materialization
    must not bind ``executor=pending`` from an inter-window state post.
    """
    return max(
        (
            t
            for t in turns
            if str(t.get("subject") or "").upper().startswith("CHECKPOINT")
            and not is_pickup_append(t.get("subject"))
        ),
        key=lambda t: int(t.get("turn_number") or 0),
        default=None,
    )


def parse_tip_checkpoint(turns: list[dict]) -> tuple[dict, ParsedCheckpoint] | None:
    checkpoint = latest_checkpoint(turns)
    if checkpoint is None:
        return None
    body = resolve_checkpoint_body(
        str(checkpoint.get("body") or ""),
        sidecar_uri=(
            checkpoint.get("sidecar_uri")
            if isinstance(checkpoint.get("sidecar_uri"), str)
            else None
        ),
    )
    return checkpoint, parse_checkpoint(body)


async def admit_worker_window(
    *,
    root_id: str,
    turns: list[dict],
    caps: CapStore,
    workspace_root: Path,
    admission_mode: str,
    window_index: int | None = None,
    on_admit: Callable[[str], None] | None = None,
    arc_lane: str = "layer",
    work_key: str | None = None,
    parsed=None,
) -> AdmitResult:
    """Fire one mechanical/attended worker window from the tip CHECKPOINT.

    ``window_index`` comes from the kernel, which reconciles the bus pointers with
    the ledger and transcript; the bus-only fallback here restarts numbering
    whenever a turn fetch comes back short (a:26628) and exists only for callers
    with no ledger.
    """
    tip = parse_tip_checkpoint(turns)
    if tip is None:
        return _fail(FireAttemptOutcome.ERRORED_PRE_FIRE, "no_checkpoint")
    checkpoint, parsed = tip
    try:
        await bus_client.ensure_root_so_what(root_id)
    except Exception:  # noqa: BLE001
        logger.debug(
            "charter-runner so-what ensure failed root=%s", root_id, exc_info=True
        )
    if window_index is None:
        window_index = count_admissions(turns) + 1
    consult_role: str | None = None
    if parsed.consult_pending:
        admission_mode = "consult"
        consult_role = parsed.consult_role
        if parsed.executor_lane == "implement" and not implement_ready_declared(parsed):
            logger.warning(
                "classifier_consult_overrides_implement_lane root=%s",
                root_id,
            )
    if consult_role == "r_admit":
        sha_check = verify_r_corpus_sha(str(checkpoint.get("body") or ""))
        if not sha_check.ok:
            await refuse_stale_r_admit(
                root_id=root_id,
                checkpoint=checkpoint,
                result=sha_check,
                events_module=events,
                log=logger,
            )
            return _fail(FireAttemptOutcome.REFUSED_PRE_FIRE, "stale_r_corpus")
        clear_r_corpus_refusals(root_id)
    bind = resolve_charter_executor(
        parsed=parsed,
        admission_mode=admission_mode,
        consult_role=consult_role,
        arc_lane=arc_lane,
    )
    packet, subject = select_packet(
        root_id,
        parsed,
        scoreboard_uri=parsed.scoreboard_uri,
        window_index=window_index,
        admission_mode=admission_mode,
        consult_role=consult_role,
        source_ref=bind.source_ref,
        arc_lane=arc_lane,
    )
    return await _fire_and_pointer(
        root_id=root_id,
        window_index=window_index,
        packet=packet,
        subject=subject,
        caps=caps,
        workspace_root=workspace_root,
        admission_mode=admission_mode,
        consult_role=consult_role,
        implement_source_ref=bind.source_ref,
        on_admit=on_admit,
        is_implement=bind.is_implement,
        work_key=work_key,
    )


async def admit_consult_window(
    *,
    row: RootLedgerRow,
    turns: list[dict],
    caps: CapStore,
    workspace_root: Path,
    consult_role: str,
    window_index: int | None = None,
    on_admit: Callable[[str], None] | None = None,
    arc_lane: str = "layer",
    work_key: str | None = None,
    consult_role_at_admit: str | None = None,
) -> AdmitResult:
    """Fire a depth-1 consult seat window for a ledger root."""
    tip = parse_tip_checkpoint(turns)
    if tip is None:
        return _fail(FireAttemptOutcome.ERRORED_PRE_FIRE, "no_checkpoint")
    _checkpoint, parsed = tip
    if window_index is None:
        window_index = count_admissions(turns) + 1
    if arc_lane == "layer" and _open_layer_consult_gate(parsed) is None:
        return _fail(
            FireAttemptOutcome.REFUSED_PRE_FIRE,
            "layer_consult_gate_unresolved",
        )
    try:
        packet = materialize_consult_packet(
            row.root_id,
            parsed,
            scoreboard_uri=row.scoreboard_uri,
            window_index=window_index,
            arc_lane=arc_lane,
        )
    except LayerConsultGateUnresolvedError:
        return _fail(
            FireAttemptOutcome.REFUSED_PRE_FIRE,
            "layer_consult_gate_unresolved",
        )
    gate_id = _open_layer_consult_gate(parsed) if arc_lane == "layer" else None
    subject = consult_subject_for_arc(
        row.root_id,
        window_index,
        consult_role=consult_role,
        arc_lane=arc_lane,
        gate_id=gate_id,
    )
    return await _fire_and_pointer(
        root_id=row.root_id,
        window_index=window_index,
        packet=packet,
        subject=subject,
        caps=caps,
        workspace_root=workspace_root,
        admission_mode="consult",
        consult_role=consult_role_at_admit or consult_role,
        implement_source_ref=None,
        on_admit=on_admit,
        is_implement=False,
        work_key=work_key,
    )


async def _fire_and_pointer(
    *,
    root_id: str,
    window_index: int,
    packet: str,
    subject: str,
    caps: CapStore,
    workspace_root: Path,
    admission_mode: str,
    consult_role: str | None,
    implement_source_ref: str | None,
    on_admit: Callable[[str], None] | None,
    is_implement: bool,
    work_key: str | None = None,
) -> AdmitResult:
    if admission_mode_requires_write_fence(admission_mode):
        preflight = await preflight_write_lease(root_id=root_id)
        if preflight.outcome == "escalate":
            caps.mark_failed(
                root_id,
                f"gate_defer_escalated:{preflight.escalation_reason}",
            )
            await emit_admission_defer_escalated(
                root=root_id,
                reason=str(preflight.escalation_reason or "unknown"),
                holder_dispatch_id=preflight.holder_dispatch_id,
                defer_count=preflight.defer_count,
                holder_age_s=preflight.holder_age_s,
            )
            logger.error(
                "charter-runner gate defer escalated root=%s reason=%s holder=%s",
                root_id,
                preflight.escalation_reason,
                preflight.holder_dispatch_id,
            )
            return _fail(
                FireAttemptOutcome.REFUSED_PRE_FIRE,
                f"gate_defer_escalated:{preflight.escalation_reason or 'unknown'}",
            )
        if preflight.outcome == "defer":
            await emit_admission_deferred_gate_held(
                root=root_id,
                holder_dispatch_id=preflight.holder_dispatch_id,
                holder_age_s=preflight.holder_age_s,
                defer_count=preflight.defer_count,
                queue_depth=preflight.queue_depth,
            )
            logger.info(
                "charter-runner admission deferred (gate held) root=%s holder=%s "
                "defer_count=%s",
                root_id,
                preflight.holder_dispatch_id,
                preflight.defer_count,
            )
            return _fail(FireAttemptOutcome.DEFERRED_LEGAL, "gate_defer")

    caps.mark_admit_intent(root_id, window_index)
    try:
        result = await dispatch_client.fire_window(
            root_id,
            packet,
            workspace_root=workspace_root,
            window_index=window_index,
            subject=subject,
            admission_mode=admission_mode,
            consult_role=consult_role,
            implement_source_ref=implement_source_ref,
            work_key=work_key,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body_snippet = (exc.response.text or "")[:500]
        if status == 409 and "CURSOR_WRITE_LEASE_HELD" in body_snippet:
            caps.clear_admit_intent(root_id, window_index)
            await emit_admission_deferred_gate_held(
                root=root_id,
                holder_dispatch_id=None,
                holder_age_s=None,
                defer_count=1,
            )
            logger.info(
                "charter-runner admission refused at ledger (409 lease held) root=%s",
                root_id,
            )
            return _fail(FireAttemptOutcome.DEFERRED_LEGAL, "lease_held")
        if status == 503 and "GIT_WORKER_DRAINING" in body_snippet:
            caps.clear_admit_intent(root_id, window_index)
            await emit_admission_deferred_gate_held(
                root=root_id,
                holder_dispatch_id=None,
                holder_age_s=None,
                defer_count=1,
            )
            logger.info(
                "charter-runner admission deferred (GIW draining, no CapStore stop) "
                "root=%s status=%s",
                root_id,
                status,
            )
            return _fail(FireAttemptOutcome.DEFERRED_LEGAL, "giw_draining")
        if 400 <= status < 500:
            caps.clear_admit_intent(root_id, window_index)
            # CURSOR_SDK_PARITY is a live substrate/PATH gap — permanent CapStore
            # stop turns every tick into pager spam (blocked:1) until manage restart.
            if "CURSOR_SDK_PARITY" in body_snippet:
                await events.emit_manage_charter_tick_window_failed(
                    root=root_id, reason="admission_parity"
                )
                logger.error(
                    "charter-runner admission parity (no CapStore stop) "
                    "root=%s status=%s body=%s",
                    root_id,
                    status,
                    body_snippet,
                )
                return _fail(FireAttemptOutcome.REFUSED_PRE_FIRE, "admission_parity")
            caps.mark_failed(root_id, "admission_rejected")
            await events.emit_manage_charter_tick_window_failed(
                root=root_id, reason="admission_rejected"
            )
            logger.error(
                "charter-runner admission rejected root=%s status=%s body=%s",
                root_id,
                status,
                body_snippet,
            )
            return _fail(FireAttemptOutcome.REFUSED_PRE_FIRE, "admission_rejected")
        caps.mark_failed(root_id, "admission_transport_error")
        await events.emit_manage_charter_tick_window_failed(
            root=root_id, reason="admission_transport_error"
        )
        logger.error(
            "charter-runner admission transport error root=%s status=%s body=%s",
            root_id,
            status,
            body_snippet,
        )
        return _fail(FireAttemptOutcome.ERRORED_PRE_FIRE, "admission_transport_error")
    except Exception as exc:
        caps.mark_failed(root_id, "admission_exception")
        await events.emit_manage_charter_tick_window_failed(
            root=root_id, reason="admission_exception"
        )
        logger.exception(
            "charter-runner admission exception root=%s: %s",
            root_id,
            exc,
        )
        return _fail(FireAttemptOutcome.ERRORED_PRE_FIRE, "admission_exception")
    clear_gate_defer(root_id)
    caps.record_admit(root_id)
    worker_thread = str(result.get("thread_id") or "")
    caps.bind_intent_worker(root_id, window_index, worker_thread)
    packet_path = str(result.get("packet_path") or "")
    now_iso = datetime.now(UTC).isoformat()
    try:
        await bus_client.post_admission_pointer(
            root_id,
            window_index=window_index,
            posted_at_iso=now_iso,
            worker_thread=worker_thread,
            packet_path=packet_path,
            admission_mode=admission_mode,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "charter-runner pointer post failed for root %s after fire: %s",
            root_id,
            exc,
        )
        caps.mark_failed(root_id, "pointer_post_failed")
        await events.emit_manage_charter_tick_window_failed(
            root=root_id, reason="pointer_post_failed"
        )
        return _fail(FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED, "pointer_post_failed")
    await events.emit_manage_charter_tick_admitted(
        root=root_id,
        dispatch_id=str(result.get("dispatch_id") or worker_thread),
        worker_thread=worker_thread,
        objective=_charter_objective_for_emit(root_id),
    )
    try:
        window_log.append_admit(
            root_id=root_id,
            window_index=window_index,
            worker_thread=worker_thread,
            packet_path=packet_path,
            packet_text=packet,
            push_reminder=str(result.get("push_reminder") or ""),
            dispatch_id=str(result.get("dispatch_id") or ""),
        )
        window_log.append_executor_note(worker_thread, result.get("executor") or {})
    except Exception:  # noqa: BLE001
        logger.exception("charter-runner window_log append_admit failed")
    executor = result.get("executor") or {}
    fired_model = str(executor.get("model") or executor.get("role") or "")
    if admission_mode == "consult":
        mode_note = (
            " (CONSULT_PENDING — R-admit host → cdp/opus-5)"
            if consult_role == "r_admit"
            else " (CONSULT_PENDING — judgment_gap host → cdp/opus-5)"
        )
    elif admission_mode == "autonomous":
        lane = "implement" if is_implement else "background lead"
        mode_note = f" (autonomous {lane} — {fired_model})"
    else:
        mode_note = f" ({fired_model})"
    msg = f"charter-runner: admitted {worker_thread} for root {root_id}" + mode_note
    if on_admit is not None:
        try:
            on_admit(msg)
        except Exception:  # noqa: BLE001
            logger.exception("charter-runner on_admit notify failed")
    return _ok(
        dispatch_id=str(result.get("dispatch_id") or worker_thread or "") or None,
        thread_id=worker_thread or None,
    )


__all__ = [
    "admit_consult_window",
    "admit_worker_window",
    "count_admissions",
    "latest_checkpoint",
    "parse_tip_checkpoint",
]
