"""L1 identical-work refire gate — charter-runner pre-ADMIT (spec §2.1, §3)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .giw_live_hold import (
    dispatch_ids_from_active_work,
    fetch_giw_active_work_payload,
    read_giw_active_work,
)
from .root_ledger import RootLedgerRow, Transition
from .storm_fuse import FuseIdentity, record_park_friction
from .telemetry import emit_identical_work_refire_refused
from .window_sequence import window_index_from_id
from .work_key import WORK_KEY_VERSION, compute_work_key
from .work_key_store import (
    find_record_by_window_id,
    harvested_for_key,
    live_undispositioned_for_key,
    stamp_disposition,
)

FRICTION_ID = 27259
SKIP_REASON = f"identical_work_refire:a{FRICTION_ID}"
MALFORMED_NEST_SKIP_REASON = f"{SKIP_REASON}:malformed_nest:a27245"

_ADMIT_TRANSITIONS = frozenset({Transition.ADMIT_CONSULT, Transition.ADMIT_WORKER})


@dataclass(frozen=True)
class RefireGateContext:
    """Carve-out knobs for one pre-admit evaluation."""

    force: bool = False
    nest_under: str | None = None
    holder_dispatch_id: str | None = None
    supersede_window_id: str | None = None
    incoming_window_index: int | None = None
    incoming_dispatch_id: str | None = None


@dataclass(frozen=True)
class RefireGateOutcome:
    refused: bool
    skipped_reason: str | None = None
    carve_out: str | None = None
    work_key: str = ""
    probe_status: str = "ok"
    holder_window_id: str | None = None
    holder_dispatch_id: str | None = None
    holder_thread_id: str | None = None
    holder_age_s: float | None = None


def resolve_admission_mode(
    transition: Transition,
    *,
    admission_mode: str = "autonomous",
) -> str:
    if transition == Transition.ADMIT_CONSULT:
        return "consult"
    if transition == Transition.ADMIT_WORKER:
        return admission_mode
    return admission_mode


def _holder_is_non_terminal(
    holder: Any,
    *,
    active_dispatch_ids: set[str],
) -> bool:
    dispatch_id = str(holder.dispatch_id or "").strip()
    if dispatch_id and dispatch_id in active_dispatch_ids:
        return True
    return bool(dispatch_id)


def _of2_resume(
    holder: Any,
    ctx: RefireGateContext,
) -> bool:
    if ctx.incoming_window_index is None or not ctx.incoming_dispatch_id:
        return False
    holder_index = window_index_from_id(holder.window_id)
    if holder_index <= 0 or holder_index != ctx.incoming_window_index:
        return False
    return str(holder.dispatch_id or "") == str(ctx.incoming_dispatch_id)


async def _giw_probe() -> tuple[str, dict[str, Any] | None]:
    read = await read_giw_active_work()
    if read.status == "degraded" and read.error_class == "ConnectError":
        return "degraded", None
    if read.status != "ok":
        return "error", None
    payload = await fetch_giw_active_work_payload()
    if payload is None:
        return "error", None
    if not isinstance(payload, dict):
        return "error", None
    return "ok", payload


async def evaluate_identical_work_refire(
    conn,
    *,
    row: RootLedgerRow,
    root_id: str,
    transition: Transition,
    source_ref: str | None,
    consult_role: str | None,
    admission_mode: str,
    ctx: RefireGateContext | None = None,
    giw_payload: dict[str, Any] | None = None,
) -> RefireGateOutcome:
    """Return ``refused=True`` when a live or harvested same-key holder blocks admit.

    Path B (6486): a prior ``disposition='harvested'`` row fences re-admit of the
    same work_key; advance ``pickup_gid`` (new key) to unblock.
    """
    if transition not in _ADMIT_TRANSITIONS:
        return RefireGateOutcome(refused=False)

    gate_ctx = ctx or RefireGateContext()
    mode = resolve_admission_mode(transition, admission_mode=admission_mode)
    work_key = compute_work_key(
        root_id=root_id,
        source_ref=source_ref,
        pickup_gid=row.pickup_gid,
        consult_role=consult_role,
        admission_mode=mode,
    )

    async def _emit(
        *,
        carve_out: str | None,
        probe_status: str,
        holder: Any | None = None,
    ) -> None:
        age_s = None
        if holder is not None:
            age_s = max(0.0, time.time() - float(holder.admitted_at))
        await emit_identical_work_refire_refused(
            root=root_id,
            work_key=work_key,
            work_key_version=WORK_KEY_VERSION,
            source_ref=source_ref,
            pickup_gid=row.pickup_gid,
            consult_role=consult_role,
            admission_mode=mode,
            refuse_locus="charter_runner_pre_admit",
            holder_window_id=holder.window_id if holder is not None else None,
            holder_dispatch_id=holder.dispatch_id if holder is not None else None,
            holder_thread_id=holder.thread_id if holder is not None else None,
            holder_age_s=age_s,
            carve_out=carve_out,
            friction_id=FRICTION_ID,
            probe_status=probe_status,
        )

    if gate_ctx.force:
        await _emit(carve_out="force", probe_status="ok")
        return RefireGateOutcome(
            refused=False,
            carve_out="force",
            work_key=work_key,
            probe_status="ok",
        )

    if gate_ctx.supersede_window_id:
        prior = find_record_by_window_id(conn, gate_ctx.supersede_window_id)
        if prior is not None and prior.work_key != work_key:
            return RefireGateOutcome(
                refused=True,
                skipped_reason=SKIP_REASON,
                work_key=work_key,
            )
        if prior is not None:
            stamp_disposition(
                conn,
                work_key=prior.work_key,
                window_id=prior.window_id,
                disposition="superseded",
            )
        await _emit(carve_out="supersede", probe_status="ok", holder=prior)
        return RefireGateOutcome(
            refused=False,
            carve_out="supersede",
            work_key=work_key,
            probe_status="ok",
        )

    probe_status, payload = (
        ("ok", giw_payload) if giw_payload is not None else await _giw_probe()
    )
    if probe_status == "degraded":
        await _emit(carve_out=None, probe_status="degraded")
        return RefireGateOutcome(
            refused=False,
            work_key=work_key,
            probe_status="degraded",
        )
    if probe_status == "error":
        await _emit(carve_out=None, probe_status="error")
        record_park_friction(
            FuseIdentity(
                category="identical_work_refire",
                tip_gid=row.pickup_gid or "?",
                mismatch_class=work_key[:16],
            ),
            FRICTION_ID,
        )
        return RefireGateOutcome(
            refused=True,
            skipped_reason=SKIP_REASON,
            work_key=work_key,
            probe_status="error",
        )

    active_ids = dispatch_ids_from_active_work(payload or {})
    holders = live_undispositioned_for_key(conn, work_key)
    live_holders = [
        holder
        for holder in holders
        if _holder_is_non_terminal(holder, active_dispatch_ids=active_ids)
    ]
    live_holder_id = (gate_ctx.holder_dispatch_id or "").strip() or None
    if not live_holder_id:
        for holder in live_holders:
            dispatch_id = str(holder.dispatch_id or "").strip()
            if dispatch_id:
                live_holder_id = dispatch_id
                break

    if gate_ctx.nest_under:
        nest_token = gate_ctx.nest_under.strip()
        if (
            nest_token in active_ids
            and live_holder_id
            and nest_token == live_holder_id
        ):
            await _emit(carve_out="nest", probe_status="ok")
            return RefireGateOutcome(
                refused=False,
                carve_out="nest",
                work_key=work_key,
                probe_status="ok",
            )
        if live_holders and (
            nest_token not in active_ids
            or (live_holder_id and nest_token != live_holder_id)
        ):
            holder = live_holders[0]
            age_s = max(0.0, time.time() - float(holder.admitted_at))
            await _emit(carve_out=None, probe_status="ok", holder=holder)
            record_park_friction(
                FuseIdentity(
                    category="identical_work_refire",
                    tip_gid=row.pickup_gid or "?",
                    mismatch_class=work_key[:16],
                ),
                FRICTION_ID,
            )
            return RefireGateOutcome(
                refused=True,
                skipped_reason=MALFORMED_NEST_SKIP_REASON,
                work_key=work_key,
                probe_status="ok",
                holder_window_id=holder.window_id,
                holder_dispatch_id=holder.dispatch_id,
                holder_thread_id=holder.thread_id,
                holder_age_s=age_s,
            )

    holders = live_holders
    for holder in holders:
        if not _holder_is_non_terminal(holder, active_dispatch_ids=active_ids):
            continue
        if _of2_resume(holder, gate_ctx):
            await _emit(carve_out="of2_resume", probe_status="ok", holder=holder)
            return RefireGateOutcome(
                refused=False,
                carve_out="of2_resume",
                work_key=work_key,
                probe_status="ok",
                holder_window_id=holder.window_id,
                holder_dispatch_id=holder.dispatch_id,
            )
        age_s = max(0.0, time.time() - float(holder.admitted_at))
        await _emit(carve_out=None, probe_status="ok", holder=holder)
        record_park_friction(
            FuseIdentity(
                category="identical_work_refire",
                tip_gid=row.pickup_gid or "?",
                mismatch_class=work_key[:16],
            ),
            FRICTION_ID,
        )
        return RefireGateOutcome(
            refused=True,
            skipped_reason=SKIP_REASON,
            work_key=work_key,
            probe_status="ok",
            holder_window_id=holder.window_id,
            holder_dispatch_id=holder.dispatch_id,
            holder_thread_id=holder.thread_id,
            holder_age_s=age_s,
        )

    harvested = harvested_for_key(conn, work_key)
    if harvested:
        holder = harvested[0]
        age_s = max(0.0, time.time() - float(holder.admitted_at))
        await _emit(carve_out=None, probe_status="ok", holder=holder)
        record_park_friction(
            FuseIdentity(
                category="identical_work_refire",
                tip_gid=row.pickup_gid or "?",
                mismatch_class=work_key[:16],
            ),
            FRICTION_ID,
        )
        return RefireGateOutcome(
            refused=True,
            skipped_reason=SKIP_REASON,
            work_key=work_key,
            probe_status="ok",
            holder_window_id=holder.window_id,
            holder_dispatch_id=holder.dispatch_id,
            holder_thread_id=holder.thread_id,
            holder_age_s=age_s,
        )

    return RefireGateOutcome(refused=False, work_key=work_key, probe_status=probe_status)


__all__ = [
    "FRICTION_ID",
    "MALFORMED_NEST_SKIP_REASON",
    "RefireGateContext",
    "RefireGateOutcome",
    "SKIP_REASON",
    "evaluate_identical_work_refire",
    "resolve_admission_mode",
]
