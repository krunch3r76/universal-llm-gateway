"""Live kernel tick apply — decide + execute ≤1 transition per root (Phase 3)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from .admission import CapsView, EnvFacts, decide
from .caps import CapStore
from .consult_lane import (
    _backoff_s,
    _consult_role_for_row,
    _load_queue_row,
    enqueue_consult,
)
from .env_snapshot import EnvSnapshot
from .root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
    load_root,
    open_default_ledger,
    upsert_root,
    write_cortex_mirror,
)
from .telemetry import emit_consult_deferred, emit_consult_queued, emit_tick_transition
from .window_exec import admit_consult_window, admit_worker_window

logger = get_logger(__name__)


@dataclass(frozen=True)
class KernelTickOutcome:
    old_decision_label: str
    admitted: bool = False
    skipped_reason: str | None = None


def _ledger_row_from_state(
    conn,
    root_id: str,
    *,
    status: RootStatus,
    transition: Transition,
    wip: str | None = None,
    consult_attempts: int | None = None,
    consult_next_retry: float | None = None,
) -> RootLedgerRow:
    existing = load_root(conn, root_id)
    if existing is None:
        raise KeyError(f"ledger row missing for {root_id}")
    row = RootLedgerRow(
        root_id=existing.root_id,
        status=status,
        pickup_gid=existing.pickup_gid,
        pickup_lane=existing.pickup_lane,
        pickup_executor=existing.pickup_executor,
        attendance=existing.attendance,
        scoreboard_uri=existing.scoreboard_uri,
        wip_window_id=wip if wip is not None else existing.wip_window_id,
        revise_count=existing.revise_count,
        consult_role=existing.consult_role or _consult_role_for_row(existing),
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
        last_window_id=existing.last_window_id,
        last_transition=transition.value,
        last_error=existing.last_error,
        env_facts_json=existing.env_facts_json,
        updated_at=time.time(),
    )
    upsert_root(conn, row)
    write_cortex_mirror(row)
    return row


def _tip_has_wip(turns: list[dict]) -> bool:
    from .eligibility import ADMISSION_SUBJECT_PREFIX, _latest_matching, _turn_number
    from .window_terminal_contract import is_tip_class

    tip = _latest_matching(turns, is_tip_class)
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
) -> KernelTickOutcome:
    """Live kernel path — sole admitter for ledger-seeded enrolled roots."""
    from .seed_phase1 import ensure_root_ledger_seed

    conn = open_default_ledger()
    try:
        row = load_root(conn, root_id)
        if row is None:
            # Known PHASE1 / conveyor seeds self-heal once (a:26619).
            if ensure_root_ledger_seed(root_id):
                row = load_root(conn, root_id)
        if row is None:
            return KernelTickOutcome("kernel_unseeded")
        has_wip = _tip_has_wip(turns)
        facts = env.facts_for_root(root_id, has_wip=has_wip)
        facts = EnvFacts(
            substrate_up=facts.substrate_up,
            has_wip=facts.has_wip,
            attendance=row.attendance,
            propagation_residue=env.propagation_residue,
            giw_holder_lease=env.giw_holder_lease,
            restart_shaped=env.restart_shaped_for_root(root_id),
        )
        caps_view = CapsView.from_cap_store(caps, root_id)
        transition = decide(row, facts, caps_view)
        if transition == Transition.QUEUE_CONSULT:
            return await _queue_consult(conn, row, root_id, transition)
        if transition == Transition.DEFER_CONSULT:
            return await _defer_consult(conn, row, root_id)
        if transition == Transition.ADMIT_CONSULT:
            return await _admit_consult(
                conn,
                row,
                root_id,
                turns,
                caps=caps,
                workspace_root=workspace_root,
                on_admit=on_admit,
            )
        if transition == Transition.ADMIT_WORKER:
            return await _admit_worker(
                conn,
                root_id,
                turns,
                caps=caps,
                workspace_root=workspace_root,
                admission_mode=admission_mode,
                on_admit=on_admit,
            )
        if transition == Transition.BLOCK:
            return KernelTickOutcome("kernel_blocked", skipped_reason="blocked")
        return KernelTickOutcome(transition.value)
    finally:
        conn.close()


async def _queue_consult(conn, row, root_id: str, transition: Transition) -> KernelTickOutcome:
    role = _consult_role_for_row(row)
    gid = row.pickup_gid or "G?"
    if row.status in (RootStatus.CONSULT_QUEUED, RootStatus.CONSULT_ADMITTED):
        return KernelTickOutcome("kernel_consult_already_queued")
    existing = _load_queue_row(conn, root_id, gid, role)
    if existing is not None and existing.status in ("queued", "admitted"):
        return KernelTickOutcome("kernel_consult_already_queued")
    enqueue_consult(conn, row=row, consult_role=role)
    updated = _ledger_row_from_state(
        conn,
        root_id,
        status=RootStatus.CONSULT_QUEUED,
        transition=Transition.QUEUE_CONSULT,
    )
    await emit_consult_queued(root=root_id, gid=updated.pickup_gid or "?", role=role)
    await emit_tick_transition(
        root=root_id,
        from_status=row.status.value,
        to_status=updated.status.value,
        transition=transition.value,
        gid=updated.pickup_gid,
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
    caps: CapStore,
    workspace_root: Path | None,
    on_admit,
) -> KernelTickOutcome:
    if workspace_root is None:
        return KernelTickOutcome("kernel_no_workspace")
    if row.consult_next_retry and time.time() < row.consult_next_retry:
        return KernelTickOutcome("kernel_consult_backoff")
    role = _consult_role_for_row(row)
    admitted = await admit_consult_window(
        row=row,
        turns=turns,
        caps=caps,
        workspace_root=workspace_root,
        consult_role=role,
        on_admit=on_admit,
    )
    if admitted:
        _ledger_row_from_state(
            conn,
            root_id,
            status=RootStatus.CONSULT_ADMITTED,
            transition=Transition.ADMIT_CONSULT,
            wip=f"charter-{root_id}-consult",
        )
    return KernelTickOutcome(
        "kernel_admit_consult" if admitted else "kernel_admit_failed",
        admitted=admitted,
    )


async def _admit_worker(
    conn,
    root_id: str,
    turns: list[dict],
    *,
    caps: CapStore,
    workspace_root: Path | None,
    admission_mode: str,
    on_admit,
) -> KernelTickOutcome:
    if workspace_root is None:
        return KernelTickOutcome("kernel_no_workspace")
    admitted = await admit_worker_window(
        root_id=root_id,
        turns=turns,
        caps=caps,
        workspace_root=workspace_root,
        admission_mode=admission_mode,
        on_admit=on_admit,
    )
    if admitted:
        _ledger_row_from_state(
            conn,
            root_id,
            status=RootStatus.ADMITTED,
            transition=Transition.ADMIT_WORKER,
            wip=f"charter-{root_id}-w",
        )
    return KernelTickOutcome(
        "kernel_admit_worker" if admitted else "kernel_admit_failed",
        admitted=admitted,
    )


__all__ = [
    "KernelTickOutcome",
    "apply_kernel_tick_for_root",
]
