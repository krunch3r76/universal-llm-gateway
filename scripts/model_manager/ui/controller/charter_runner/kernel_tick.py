"""Live kernel tick apply — decide + execute ≤1 transition per root (Phase 3)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from .admission import CapStore, CapsView, EnvFacts, decide, layer_independence_unproven
from .checkpoint_schema import resolve_checkpoint_body
from .checkpoint_schema.footer import is_exhausted_hopper_footer
from .consult_lane import (
    _backoff_s,
    _consult_role_for_row,
    _load_queue_row,
    enqueue_consult,
    resolve_consult_role,
    sync_ledger_consult_queued,
)
from .conveyor_phase import (
    record_admit_cursor,
    wake_conveyor_if_fresh_append,
)
from .env_snapshot import EnvSnapshot
from .identical_work_refire import (
    SKIP_REASON,
    RefireGateContext,
    evaluate_identical_work_refire,
    resolve_admission_mode,
)
from .pickup_advance import (
    advance_pickup_gid,
    gated_pickup_from_parsed,
    tip_executor_is_cdp_family,
    tip_is_empty_hopper,
    worker_substrate_compatible,
)
from .root_health import FireAttemptOutcome
from .root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
    load_root,
    open_default_ledger,
    typed_record_valid,
    upsert_root,
    write_cortex_mirror,
)
from .telemetry import emit_consult_deferred, emit_consult_queued, emit_tick_transition
from .window_exec import admit_consult_window, admit_worker_window, parse_tip_checkpoint
from .window_sequence import (
    clear_uncorrelatable_wip,
    next_window_index,
    window_id_for,
    window_index_from_id,
)
from .work_key import compute_work_key
from .work_key_store import record_admit

logger = get_logger(__name__)

_EXECUTION_ID_RE = re.compile(r"execution_id=(\S+)", re.IGNORECASE)


def _source_ref_for_admit(parsed, *, transition: Transition, admission_mode: str):
    if parsed is None:
        return None
    if transition == Transition.ADMIT_WORKER:
        from .executor_routing import resolve_charter_executor

        bind = resolve_charter_executor(
            parsed=parsed,
            admission_mode=admission_mode,
            consult_role=parsed.consult_role,
        )
        return bind.source_ref or parsed.source_ref
    return parsed.source_ref


def _incoming_dispatch_id(parsed) -> str | None:
    if parsed is None:
        return None
    for row in parsed.next_pickup:
        match = _EXECUTION_ID_RE.search(row)
        if match:
            return match.group(1)
    return None


def _refire_context_for_row(row, parsed) -> RefireGateContext:
    wip_index = window_index_from_id(row.wip_window_id)
    return RefireGateContext(
        incoming_window_index=wip_index if wip_index > 0 else None,
        incoming_dispatch_id=_incoming_dispatch_id(parsed),
    )


def _refused_refire_outcome(outcome) -> KernelTickOutcome:
    return KernelTickOutcome(
        "kernel_identical_work_refire",
        skipped_reason=outcome.skipped_reason or SKIP_REASON,
        fire_attempt_outcome=FireAttemptOutcome.REFUSED_PRE_FIRE,
        fire_attempt_reason="identical_work_refire",
    )

# Gated tip required for admit/queue (a:26596; enqueue-then-stall never closed).
# DEFER stays listed so IDLE→DEFER still fails closed on idle tips; already-queued
# CONSULT_QUEUED/DEFERRED deferrals are exempted at the call site (P4-AC1 / G4a).
_NEEDS_GATED_PICKUP = frozenset(
    {
        Transition.ADMIT_CONSULT,
        Transition.ADMIT_WORKER,
        Transition.QUEUE_CONSULT,
        Transition.DEFER_CONSULT,
    }
)


@dataclass(frozen=True)
class KernelTickOutcome:
    old_decision_label: str
    admitted: bool = False
    skipped_reason: str | None = None
    fire_attempt_outcome: FireAttemptOutcome | None = None
    fire_attempt_reason: str | None = None


def _ledger_row_from_state(
    conn,
    root_id: str,
    *,
    status: RootStatus,
    transition: Transition,
    wip: str | None = None,
    last_window: str | None = None,
    consult_attempts: int | None = None,
    consult_next_retry: float | None = None,
    consult_role: str | None = None,
) -> RootLedgerRow:
    existing = load_root(conn, root_id)
    if existing is None:
        raise KeyError(f"ledger row missing for {root_id}")
    # A hold armed mid-pass must survive the terminal write (6489). Window fields
    # still record so harvest / worker-failed release stay correlated.
    next_status = (
        existing.status
        if existing.status in (RootStatus.BLOCKED, RootStatus.CLOSED)
        else status
    )
    row = RootLedgerRow(
        root_id=existing.root_id,
        status=next_status,
        pickup_gid=existing.pickup_gid,
        pickup_lane=existing.pickup_lane,
        pickup_executor=existing.pickup_executor,
        attendance=existing.attendance,
        scoreboard_uri=existing.scoreboard_uri,
        wip_window_id=wip if wip is not None else existing.wip_window_id,
        revise_count=existing.revise_count,
        consult_role=(
            consult_role
            if consult_role is not None
            else (existing.consult_role or _consult_role_for_row(existing))
        ),
        consult_attempts=(
            consult_attempts
            if consult_attempts is not None
            else existing.consult_attempts
        ),
        consult_next_retry=(
            consult_next_retry
            if consult_next_retry is not None
            else existing.consult_next_retry
        ),
        consult_poll_from=existing.consult_poll_from,
        harvest_deadline=existing.harvest_deadline,
        last_window_id=last_window or existing.last_window_id,
        last_transition=transition.value,
        last_error=existing.last_error,
        env_facts_json=existing.env_facts_json,
        conveyor_phase=existing.conveyor_phase,
        pickup_append_cursor=existing.pickup_append_cursor,
        updated_at=time.time(),
    )
    upsert_root(conn, row)
    write_cortex_mirror(row)
    return row


def _tip_has_wip(turns: list[dict]) -> bool:
    from .admission import ADMISSION_SUBJECT_PREFIX, _latest_matching, _turn_number
    from .window_terminal_contract import is_window_terminal

    tip = _latest_matching(turns, is_window_terminal)
    tip_n = _turn_number(tip) if tip is not None else 0
    prefix = ADMISSION_SUBJECT_PREFIX.upper()
    return any(
        _turn_number(t) > tip_n
        and str(t.get("subject") or "").upper().startswith(prefix)
        for t in turns
    )


async def apply_kernel_tick_for_root(
    root_id: str,
    turns: list[dict],
    *,
    caps: CapStore,
    workspace_root: Path | None,
    env: EnvSnapshot,
    on_admit=None,
    admission_mode: str = "autonomous",
    pass_source: str | None = None,
) -> KernelTickOutcome:
    """Execute one harvest→dispatch transition for a typed work-item root."""
    conn = open_default_ledger()
    try:
        row = load_root(conn, root_id)
        if row is None:
            return KernelTickOutcome(
                "kernel_unseeded",
                skipped_reason="migrate_typed_admit",
                fire_attempt_outcome=FireAttemptOutcome.REFUSED_PRE_FIRE,
                fire_attempt_reason="migrate_typed_admit",
            )
        typed_authority = typed_record_valid(row)
        has_wip = _tip_has_wip(turns)
        tip = parse_tip_checkpoint(turns)
        parsed = tip[1] if tip is not None else None
        if not typed_authority:
            from .seed_phase1 import ensure_root_ledger_seed

            if ensure_root_ledger_seed(root_id):
                row = load_root(conn, root_id) or row
                typed_authority = typed_record_valid(row)
        row = wake_conveyor_if_fresh_append(conn, row, parsed)
        row = load_root(conn, root_id) or row
        if (
            row.conveyor_phase == "dormant"
            and not has_wip
            and not row.wip_window_id
            and not typed_authority
        ):
            return KernelTickOutcome(
                "kernel_dormant",
                skipped_reason="dormant",
                fire_attempt_outcome=FireAttemptOutcome.NO_ATTEMPT_QUIET,
                fire_attempt_reason="dormant",
            )
        row = clear_uncorrelatable_wip(conn, row)
        if not has_wip and not row.wip_window_id:
            row = await _maybe_advance_pickup(
                conn,
                row,
                parsed,
                pass_source=pass_source,
                typed_authority=typed_authority,
            )
        facts = env.facts_for_root(root_id, has_wip=has_wip)
        arc_lane = env.arc_lane_by_root.get(root_id, "layer")
        empty_hopper = False
        checkpoint_body = ""
        if tip is not None:
            checkpoint_body = resolve_checkpoint_body(
                str(tip[0].get("body") or ""),
                sidecar_uri=(
                    tip[0].get("sidecar_uri")
                    if isinstance(tip[0].get("sidecar_uri"), str)
                    else None
                ),
            )
        if (
            checkpoint_body
            and is_exhausted_hopper_footer(checkpoint_body)
            and gated_pickup_from_parsed(parsed) is None
            and not has_wip
            and not row.wip_window_id
        ):
            return KernelTickOutcome(
                "kernel_exhausted_hopper",
                skipped_reason="exhausted_hopper",
                fire_attempt_outcome=FireAttemptOutcome.NO_ATTEMPT_QUIET,
                fire_attempt_reason="exhausted_hopper",
            )
        if not typed_authority:
            empty_hopper = tip_is_empty_hopper(
                parsed,
                has_wip=has_wip,
                wip_window_id=row.wip_window_id,
            )
        tip_executor = None
        consult_pending = False
        if parsed is not None:
            consult_pending = bool(parsed.consult_pending)
            live_pickup = gated_pickup_from_parsed(parsed)
            if live_pickup is not None:
                tip_executor = live_pickup.executor
        independence_block = layer_independence_unproven(
            arc_lane=arc_lane,
            attendance=row.attendance or facts.attendance,
            parsed=parsed,
            checkpoint_body=checkpoint_body,
            pickup_lane=(row.pickup_lane or "judgment"),
        )
        facts = EnvFacts(
            substrate_up=facts.substrate_up,
            has_wip=facts.has_wip,
            attendance=row.attendance,
            propagation_residue=env.propagation_residue,
            giw_holder_lease=env.giw_holder_lease,
            restart_shaped=env.restart_shaped_for_root(root_id),
            empty_hopper=empty_hopper,
            consult_pending=consult_pending,
            tip_executor=tip_executor,
            arc_lane=arc_lane,
            layer_independence_block=independence_block,
        )
        caps_view = CapsView.from_cap_store(caps, root_id)
        transition = decide(row, facts, caps_view)
        if transition == Transition.NOOP and empty_hopper:
            return KernelTickOutcome(
                "kernel_empty_hopper",
                skipped_reason="empty_hopper",
                fire_attempt_outcome=FireAttemptOutcome.NO_ATTEMPT_QUIET,
                fire_attempt_reason="empty_hopper",
            )
        # Already-queued/deferred consults may DEFER on substrate_down even when
        # the tip gated lane is idle — blocking that (a:26596 admit/re-queue
        # fence) left P4-AC1 unobservable for unenrolled-idle tips (G4a).
        _defer_existing = transition == Transition.DEFER_CONSULT and row.status in (
            RootStatus.CONSULT_QUEUED,
            RootStatus.CONSULT_DEFERRED,
        )
        if (
            transition in _NEEDS_GATED_PICKUP
            and gated_pickup_from_parsed(parsed) is None
            and not _defer_existing
            and not typed_authority
        ):
            # decide() is ledger-only; idle tip must not admit or re-queue (a:26596).
            return KernelTickOutcome(
                "kernel_no_gated_pickup",
                skipped_reason="no_gated_pickup",
                fire_attempt_outcome=FireAttemptOutcome.NO_ATTEMPT_QUIET,
                fire_attempt_reason="no_gated_pickup",
            )
        if transition in _NEEDS_GATED_PICKUP and typed_authority and parsed is None:
            logger.info(
                "charter-runner typed_admit_dispatch root=%s gid=%s lane=%s",
                root_id,
                row.pickup_gid,
                row.pickup_lane,
            )
        if transition == Transition.QUEUE_CONSULT:
            return await _queue_consult(
                conn, row, root_id, transition, parsed, pass_source=pass_source
            )
        if transition == Transition.DEFER_CONSULT:
            return await _defer_consult(conn, row, root_id)
        pending_work_key: str | None = None
        if transition in (Transition.ADMIT_CONSULT, Transition.ADMIT_WORKER):
            consult_role = (
                resolve_consult_role(row, parsed)
                if transition == Transition.ADMIT_CONSULT
                else None
            )
            source_ref = _source_ref_for_admit(
                parsed, transition=transition, admission_mode=admission_mode
            )
            refire = await evaluate_identical_work_refire(
                conn,
                row=row,
                root_id=root_id,
                transition=transition,
                source_ref=source_ref,
                consult_role=consult_role,
                admission_mode=admission_mode,
                ctx=_refire_context_for_row(row, parsed),
            )
            if refire.refused:
                return _refused_refire_outcome(refire)
            pending_work_key = refire.work_key or compute_work_key(
                root_id=root_id,
                source_ref=source_ref,
                pickup_gid=row.pickup_gid,
                consult_role=consult_role,
                admission_mode=resolve_admission_mode(
                    transition, admission_mode=admission_mode
                ),
            )
        window_index = next_window_index(root_id, turns, row=row)
        if transition == Transition.ADMIT_CONSULT:
            return await _admit_consult(
                conn,
                row,
                root_id,
                turns,
                window_index=window_index,
                caps=caps,
                workspace_root=workspace_root,
                on_admit=on_admit,
                parsed=parsed,
                arc_lane=arc_lane,
                work_key=pending_work_key,
            )
        if transition == Transition.ADMIT_WORKER:
            # Tip next_pickup.executor is admit-substrate authority (a:26659).
            # Ledger pickup_executor / attendance→generate must not override a
            # non-worker family (e.g. cdp/opus) into admit_worker_window.
            # Stage-B: cdp/* tips positively rebind to QUEUE_CONSULT (never
            # ADMIT_WORKER; bare refuse parks forever — 6163 completion monitor).
            # CONSULT_PENDING tips likewise rebind — ``executor=pending`` is
            # worker_substrate_compatible but must not ADMIT_WORKER (6237 G3).
            if parsed is not None and parsed.consult_pending:
                logger.info(
                    "charter-runner consult_pending_rebind_consult root=%s",
                    root_id,
                )
                return await _queue_consult(
                    conn,
                    row,
                    root_id,
                    Transition.QUEUE_CONSULT,
                    parsed,
                    pass_source=pass_source,
                )
            live = gated_pickup_from_parsed(parsed)
            tip_executor = live.executor if live is not None else None
            if not worker_substrate_compatible(tip_executor):
                if tip_executor_is_cdp_family(tip_executor):
                    logger.info(
                        "charter-runner executor_mismatch_rebind_consult "
                        "root=%s tip_executor=%s",
                        root_id,
                        tip_executor,
                    )
                    return await _queue_consult(
                        conn,
                        row,
                        root_id,
                        Transition.QUEUE_CONSULT,
                        parsed,
                        pass_source=pass_source,
                    )
                logger.info(
                    "charter-runner executor_mismatch root=%s tip_executor=%s",
                    root_id,
                    tip_executor,
                )
                return KernelTickOutcome(
                    "kernel_executor_mismatch",
                    skipped_reason="executor_mismatch",
                    fire_attempt_outcome=FireAttemptOutcome.REFUSED_PRE_FIRE,
                    fire_attempt_reason="executor_mismatch",
                )
            return await _admit_worker(
                conn,
                root_id,
                turns,
                window_index=window_index,
                caps=caps,
                workspace_root=workspace_root,
                admission_mode=admission_mode,
                on_admit=on_admit,
                arc_lane=arc_lane,
                work_key=pending_work_key,
                parsed=parsed,
            )
        if transition == Transition.BLOCK:
            # Prefer durable stop/revise reason over opaque "blocked" so pager
            # standing-dedupe and SMS bodies name the CapStore stop (e.g.
            # stopped:admission_transport_error).
            block_reason = "blocked"
            if independence_block:
                block_reason = "layer_independence_unproven"
            elif caps_view.stopped_reason:
                block_reason = f"stopped:{caps_view.stopped_reason}"
            elif caps_view.revise_reason:
                block_reason = caps_view.revise_reason
            return KernelTickOutcome(
                "kernel_blocked",
                skipped_reason=block_reason,
                fire_attempt_outcome=FireAttemptOutcome.REFUSED_PRE_FIRE,
                fire_attempt_reason=block_reason,
            )
        return KernelTickOutcome(transition.value)
    finally:
        conn.close()


async def _maybe_advance_pickup(
    conn,
    row: RootLedgerRow,
    parsed,
    *,
    pass_source: str | None = None,
    typed_authority: bool = False,
) -> RootLedgerRow:
    """Realign ``pickup_gid`` with the tip before ``decide`` reads it.

    Only runs at a clean tip (no in-flight window): mutating the pickup while a
    window is out would decorrelate the consult queue key from the packet the
    worker is holding. Typed authority skips advance when tip is absent.
    """
    if typed_authority and parsed is None:
        return row
    live = advance_pickup_gid(conn, row, parsed)
    if live is None:
        return row
    await emit_tick_transition(
        root=row.root_id,
        from_status=row.status.value,
        to_status=row.status.value,
        transition=Transition.ADVANCE_PICKUP.value,
        gid=live.gid,
        pass_source=pass_source,
    )
    return load_root(conn, row.root_id) or row


async def _queue_consult(
    conn,
    row,
    root_id: str,
    transition: Transition,
    parsed,
    *,
    pass_source: str | None = None,
) -> KernelTickOutcome:
    role = resolve_consult_role(row, parsed)
    gid = row.pickup_gid or "G?"
    if row.status in (RootStatus.CONSULT_QUEUED, RootStatus.CONSULT_ADMITTED):
        return KernelTickOutcome("kernel_consult_already_queued")
    existing = _load_queue_row(conn, root_id, gid, role)
    if existing is not None and existing.status in ("queued", "admitted"):
        if row.status not in (RootStatus.CONSULT_QUEUED, RootStatus.CONSULT_ADMITTED):
            updated = sync_ledger_consult_queued(conn, row=row, consult_role=role)
            await emit_consult_queued(
                root=root_id, gid=updated.pickup_gid or "?", role=role
            )
            await emit_tick_transition(
                root=root_id,
                from_status=row.status.value,
                to_status=updated.status.value,
                transition=transition.value,
                gid=updated.pickup_gid,
                pass_source=pass_source,
            )
            return KernelTickOutcome("kernel_queue_consult")
        return KernelTickOutcome("kernel_consult_already_queued")
    enqueue_consult(conn, row=row, consult_role=role)
    updated = load_root(conn, root_id)
    if updated is None:
        return KernelTickOutcome("kernel_queue_consult")
    await emit_consult_queued(root=root_id, gid=updated.pickup_gid or "?", role=role)
    await emit_tick_transition(
        root=root_id,
        from_status=row.status.value,
        to_status=updated.status.value,
        transition=transition.value,
        gid=updated.pickup_gid,
        pass_source=pass_source,
    )
    return KernelTickOutcome("kernel_queue_consult")


async def _defer_consult(conn, row, root_id: str) -> KernelTickOutcome:
    retry = time.time() + _backoff_s(max(row.consult_attempts, 1))
    _ledger_row_from_state(
        conn,
        root_id,
        status=RootStatus.CONSULT_DEFERRED,
        transition=Transition.DEFER_CONSULT,
        consult_next_retry=retry,
        consult_attempts=row.consult_attempts + 1,
    )
    await emit_consult_deferred(
        root=root_id, gid=row.pickup_gid or "?", next_retry=retry
    )
    return KernelTickOutcome("kernel_defer_consult")


async def _admit_consult(
    conn,
    row,
    root_id: str,
    turns: list[dict],
    *,
    window_index: int,
    caps: CapStore,
    workspace_root: Path | None,
    on_admit,
    parsed=None,
    arc_lane: str = "layer",
    work_key: str | None = None,
) -> KernelTickOutcome:
    if workspace_root is None:
        return KernelTickOutcome(
            "kernel_no_workspace",
            fire_attempt_outcome=FireAttemptOutcome.REFUSED_PRE_FIRE,
            fire_attempt_reason="kernel_no_workspace",
        )
    if row.consult_next_retry and time.time() < row.consult_next_retry:
        return KernelTickOutcome(
            "kernel_consult_backoff",
            fire_attempt_outcome=FireAttemptOutcome.DEFERRED_LEGAL,
            fire_attempt_reason="consult_backoff",
        )
    role = resolve_consult_role(row, parsed)
    result = await admit_consult_window(
        row=row,
        turns=turns,
        caps=caps,
        workspace_root=workspace_root,
        consult_role=role,
        window_index=window_index,
        on_admit=on_admit,
        arc_lane=arc_lane,
        work_key=work_key,
        consult_role_at_admit=role,
    )
    if result.admitted:
        wid = window_id_for(root_id, window_index)
        if work_key:
            record_admit(
                conn,
                work_key=work_key,
                root_id=root_id,
                window_id=wid,
                dispatch_id=str(result.dispatch_id or "") or None,
                thread_id=str(result.thread_id or "") or None,
            )
        _ledger_row_from_state(
            conn,
            root_id,
            status=RootStatus.CONSULT_ADMITTED,
            transition=Transition.ADMIT_CONSULT,
            wip=window_id_for(root_id, window_index),
            last_window=window_id_for(root_id, window_index),
        )
        tip = parse_tip_checkpoint(turns)
        parsed = tip[1] if tip is not None else None
        existing = load_root(conn, root_id)
        if existing is not None:
            record_admit_cursor(conn, existing, parsed)
    return KernelTickOutcome(
        "kernel_admit_consult" if result.admitted else "kernel_admit_failed",
        admitted=result.admitted,
        fire_attempt_outcome=result.fire_attempt_outcome,
        fire_attempt_reason=result.fire_attempt_reason or None,
    )


async def _admit_worker(
    conn,
    root_id: str,
    turns: list[dict],
    *,
    window_index: int,
    caps: CapStore,
    workspace_root: Path | None,
    admission_mode: str,
    on_admit,
    arc_lane: str = "layer",
    work_key: str | None = None,
    parsed=None,
) -> KernelTickOutcome:
    if workspace_root is None:
        return KernelTickOutcome(
            "kernel_no_workspace",
            fire_attempt_outcome=FireAttemptOutcome.REFUSED_PRE_FIRE,
            fire_attempt_reason="kernel_no_workspace",
        )
    result = await admit_worker_window(
        root_id=root_id,
        turns=turns,
        caps=caps,
        workspace_root=workspace_root,
        admission_mode=admission_mode,
        window_index=window_index,
        on_admit=on_admit,
        arc_lane=arc_lane,
        work_key=work_key,
        parsed=parsed,
    )
    if result.admitted:
        wid = window_id_for(root_id, window_index)
        if work_key:
            record_admit(
                conn,
                work_key=work_key,
                root_id=root_id,
                window_id=wid,
                dispatch_id=result.dispatch_id,
                thread_id=result.thread_id,
            )
        _ledger_row_from_state(
            conn,
            root_id,
            status=RootStatus.ADMITTED,
            transition=Transition.ADMIT_WORKER,
            wip=window_id_for(root_id, window_index),
            last_window=window_id_for(root_id, window_index),
        )
        tip = parse_tip_checkpoint(turns)
        parsed = tip[1] if tip is not None else None
        existing = load_root(conn, root_id)
        if existing is not None:
            record_admit_cursor(conn, existing, parsed)
    return KernelTickOutcome(
        "kernel_admit_worker" if result.admitted else "kernel_admit_failed",
        admitted=result.admitted,
        fire_attempt_outcome=result.fire_attempt_outcome,
        fire_attempt_reason=result.fire_attempt_reason or None,
    )


__all__ = [
    "KernelTickOutcome",
    "apply_kernel_tick_for_root",
]
