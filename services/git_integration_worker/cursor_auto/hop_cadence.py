"""Cursor-auto hop-cadence actuator — fire continuity hops without CDP self-drive.

The operator CSE cannot perceive its own depth and cannot ``team_dispatch`` from
the life seat. This module is the external actuator: age enrolled watches and
self-enqueue structural ``TYPE: CONTINUITY_HANDOFF`` when the threshold elapses.

Watch ledger / evaluate live in ``hop_cadence_watch``. This file owns capacity
gating, hop body authorship (thin adapter over ``hop_handoff``), enqueue+commission,
and the background loop.

Invariants:
- Hop ≠ close: only the existing continuity-hop path; never ``MISSION_CLOSEOUT``.
- Silent/degraded seats still hop once a watch is enrolled.
- Wake-count is rejected as primary signal (Cowork-internal, falsified at #6).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_bundles.hop_seat_cutover import refuse_cadence_hop_for_live_seat
from hop_handoff import build_continuity_handoff_body
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.cdp_escalation import (
    read_cdp_lane_snapshot,
)
from services.git_integration_worker.cursor_auto.continuity_hop import (
    run_continuity_hop_concurrent,
)
from services.git_integration_worker.cursor_auto.hop_cadence_events import (
    emit_cadence_refuse,
)
from services.git_integration_worker.cursor_auto.hop_cadence_inflight import (
    lane_in_flight_commission,
)
from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
    reconcile_stall_revocations,
    reconcile_succession_confirmations,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    HopDecision,
    age_threshold_s,
    assess_standing_handoff,
    cooldown_s,
    evaluate_watch,
    load_watches,
    mark_hop_failed,
    mark_hop_fired,
    observe_lane_from_enqueue,
    scan_interval_s,
)
from services.git_integration_worker.cursor_auto.queue import AutoJobQueue
from services.git_integration_worker.cursor_auto.silence_visibility_events import (
    emit_liveness_probe_failed,
    emit_succession_claim_missing_execution_id,
)

logger = get_logger(__name__)

# Re-export for routes / tests (stable import path).
__all__ = [
    "observe_lane_from_enqueue",
    "hop_cadence_loop",
    "scan_and_fire",
    "build_cadence_hop_body",
    "evaluate_watch",
    "assess_standing_handoff",
    "age_threshold_s",
]


def build_cadence_hop_body(
    decision: HopDecision,
    *,
    registration_id: str | None = None,
    chat_url: str | None = None,
    successor_birth_id: str | None = None,
) -> str:
    """Author the structural CONTINUITY_HANDOFF body Auto self-enqueues on fire.

    Thin adapter over ``hop_handoff.build_continuity_handoff_body`` — cadence
    supplies source/trigger/age; the identity and keep-alive doctrine live
    in the shared author. The Read-URI instruction is gated on
    ``assess_standing_handoff`` status: ``missing`` is a job for the arriving
    seat (author the S7 state file), not a broken link. First-hop vs
    later-missing is not distinguishable from ``HopDecision`` at this call
    site — one missing branch.
    """
    handoff = decision.handoff or assess_standing_handoff(decision.thread_id)
    return build_continuity_handoff_body(
        thread_id=decision.thread_id,
        trigger=decision.signal or "watch_seated_at",
        source="cursor-auto-hop-cadence",
        handoff=handoff,
        you_are=(chat_url or "").strip() or None,
        age_s=decision.age_s,
        threshold_s=(
            decision.threshold_s
            if decision.threshold_s is not None
            else age_threshold_s()
        ),
        superseded_registration_id=registration_id,
        successor_birth_id=successor_birth_id,
    )


@dataclass(frozen=True)
class CapacityGateResult:
    """Capacity snapshot + admit/block verdict for one hop-cadence decision."""

    blocked: bool
    label: str | None
    free_slots: int
    running_count: int
    at_soft_limit: bool
    at_hard_limit: bool

    @classmethod
    def fail_open(cls) -> CapacityGateResult:
        """Probe failed — capacity gate defers to liveness path (not blocked)."""
        return cls(
            blocked=False,
            label=None,
            free_slots=0,
            running_count=0,
            at_soft_limit=False,
            at_hard_limit=False,
        )

    def as_decision_dict(self) -> dict[str, Any]:
        return {
            "free_slots": self.free_slots,
            "running_count": self.running_count,
            "at_soft_limit": self.at_soft_limit,
            "at_hard_limit": self.at_hard_limit,
            "label": self.label,
        }


def _capacity_fields_from_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    return {
        "free_slots": int(snap.get("free_slots") or 0),
        "running_count": int(snap.get("running_count") or 0),
        "at_soft_limit": bool(snap.get("at_soft_limit")),
        "at_hard_limit": bool(snap.get("at_hard_limit")),
    }


def evaluate_capacity_gate(snap: dict[str, Any]) -> CapacityGateResult:
    """Evaluate hop capacity gate from an already-read CDP lane snapshot."""
    fields = _capacity_fields_from_snapshot(snap)
    if fields["at_hard_limit"]:
        return CapacityGateResult(blocked=True, label="hard", **fields)
    if fields["free_slots"] < 1:
        return CapacityGateResult(blocked=True, label="hard", **fields)
    return CapacityGateResult(blocked=False, label=None, **fields)


def capacity_blocks_hop(
    *,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
    snap: dict[str, Any] | None = None,
) -> CapacityGateResult:
    """Return capacity verdict + snapshot fields for a hop-cadence admit decision.

    Hop cadence is a seat *replacement* within hard=3: admit when ``free_slots >= 1``
    so a successor window can occupy the last hard slot while the predecessor
    streams out. The generic unattended soft gate (``escalation_lane_refusal`` with
    ``at_soft_limit``) requires ``free_slots >= 2`` and must not apply here.
    """
    if snap is not None:
        return evaluate_capacity_gate(snap)
    reader = snapshot_reader or read_cdp_lane_snapshot
    try:
        snap = reader()
    except Exception as exc:  # noqa: BLE001 — cadence must not crash the worker
        logger.warning("hop_cadence capacity probe failed: %s", exc)
        return CapacityGateResult.fail_open()
    if not isinstance(snap, dict):
        return CapacityGateResult.fail_open()
    return evaluate_capacity_gate(snap)


async def fire_hop_for_decision(
    decision: HopDecision,
    *,
    queue: AutoJobQueue,
    row: dict[str, Any],
    path: Path | None = None,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Self-enqueue a continuity hop and commission CDP for one fire decision."""
    if decision.action != "fire":
        return {"ok": False, "reason": decision.reason, "action": decision.action}
    reader = snapshot_reader or read_cdp_lane_snapshot
    snap_for_capacity: dict[str, Any] | None = None
    try:
        snap_for_capacity = reader()
    except Exception as exc:  # noqa: BLE001 — cadence must not crash the worker
        logger.warning("hop_cadence capacity probe failed: %s", exc)
    cap = capacity_blocks_hop(
        snapshot_reader=reader,
        snap=snap_for_capacity if isinstance(snap_for_capacity, dict) else None,
    )
    cap_fields = cap.as_decision_dict()
    if cap.blocked:
        logger.warning(
            "hop_cadence defer thread=%s reason=capacity label=%s "
            "free_slots=%s running_count=%s at_soft_limit=%s at_hard_limit=%s",
            decision.thread_id,
            cap_fields["label"],
            cap_fields["free_slots"],
            cap_fields["running_count"],
            cap_fields["at_soft_limit"],
            cap_fields["at_hard_limit"],
        )
        return {
            "ok": False,
            "reason": "capacity_blocked",
            "capacity_label": cap.label,
            "thread_id": decision.thread_id,
            "decision": {
                "reason": "capacity_blocked",
                **cap_fields,
            },
        }
    liveness_probe: dict[str, Any] = {"fail_open": False}
    snap: dict[str, Any]
    if isinstance(snap_for_capacity, dict):
        snap = snap_for_capacity
    else:
        try:
            snap = reader()
        except Exception as exc:  # noqa: BLE001 — cadence must not crash the worker
            logger.warning("hop_cadence liveness probe failed: %s", exc)
            snap = {"fail_open": True}
            liveness_probe = {"fail_open": True, "error": str(exc)}
            emit_liveness_probe_failed(
                thread_id=decision.thread_id,
                error=str(exc),
            )
    refuse, refuse_reason, refuse_evidence = refuse_cadence_hop_for_live_seat(
        row, snap if isinstance(snap, dict) else {}
    )
    if refuse:
        logger.warning(
            "hop_cadence refuse thread=%s reason=%s evidence=%s",
            decision.thread_id,
            refuse_reason,
            refuse_evidence,
        )
        emit_cadence_refuse(
            thread_id=decision.thread_id,
            reason=str(refuse_reason or ""),
            registration_id=str(refuse_evidence.get("registration_id") or ""),
            signal=str(refuse_evidence.get("signal") or ""),
        )
        return {
            "ok": False,
            "reason": refuse_reason,
            "thread_id": decision.thread_id,
            "refusal": refuse_evidence,
            "liveness_probe": liveness_probe,
        }
    body = build_cadence_hop_body(
        decision,
        registration_id=str(row.get("registration_id") or "") or None,
        chat_url=str(row.get("successor_chat_url") or "") or None,
    )
    from_agent = str(row.get("from_agent") or "web-anthropic")
    job = queue.enqueue(
        thread_id=decision.thread_id,
        turn_number=1,
        subject=f"cursor-auto hop cadence — continuity hop thread={decision.thread_id}",
        body=body,
        from_agent=from_agent,
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="high",
        escalation="cdp/opus-5",
        contract="light-bounded",
        require_attended=False,
        request_id=f"hop-cadence:{decision.thread_id}:{int(time.time())}",
        cse_chat_url=str(row.get("chat_url") or "") or None,
        cse_registration_id=str(row.get("registration_id") or "") or None,
        continuity_hop=True,
        continuity_matched_token="cadence:auto",
    )
    # Same lookup as HTTP hop (routes/cursor_auto.py): claimed in-flight first,
    # else oldest queued. Hop still skips supersede — reporting only.
    incumbent = queue.incumbent_for_thread(
        decision.thread_id, exclude_job_id=job.job_id
    )
    result = await run_continuity_hop_concurrent(job, queue=queue, incumbent=incumbent)
    execution_id = str(result.get("execution_id") or "").strip() or None
    if result.get("reason") == "hop_not_queued":
        hop_ok = False
        mark_hop_failed(decision.thread_id, reason="hop_not_queued", path=path)
    elif not execution_id:
        # Claim-only / commission without joinable id — do not advance succession.
        # Still take the cooldown: an unjoinable hop is a failed hop, and leaving
        # last_hop_at unadvanced re-fires it every scan.
        hop_ok = False
        mark_hop_failed(decision.thread_id, reason="missing_execution_id", path=path)
        emit_succession_claim_missing_execution_id(
            thread_id=decision.thread_id,
            job_id=job.job_id,
        )
    else:
        from hop_handoff import parse_successor_birth_id

        fired = mark_hop_fired(
            decision.thread_id,
            execution_id=execution_id,
            path=path,
            active_work_snap=snap if isinstance(snap, dict) else None,
            successor_birth_id=parse_successor_birth_id(body),
        )
        hop_ok = fired is not False
    logger.info(
        "hop_cadence fire thread=%s job=%s hop_ok=%s execution_id=%s",
        decision.thread_id,
        job.job_id,
        hop_ok,
        execution_id,
    )
    return {
        "ok": hop_ok,
        "thread_id": decision.thread_id,
        "job_id": job.job_id,
        "execution_id": execution_id,
        "decision": {
            "reason": decision.reason,
            "age_s": decision.age_s,
            "threshold_s": decision.threshold_s,
            "signal": decision.signal,
            "handoff_status": decision.handoff.status if decision.handoff else None,
            **cap_fields,
        },
        "hop_result": result,
        "liveness_probe": liveness_probe,
    }


async def scan_and_fire(
    *,
    queue: AutoJobQueue,
    path: Path | None = None,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Evaluate all watches and fire at most one due hop per scan pass."""
    ts = time.time() if now is None else now
    watches = load_watches(path)
    results: list[dict[str, Any]] = []
    fired = False
    for thread_id, row in sorted(watches.items()):
        decision = evaluate_watch(
            row,
            now=ts,
            in_flight_probe=lambda tid, q=queue: lane_in_flight_commission(
                tid, queue=q
            ),
        )
        if decision.action != "fire":
            results.append(
                {
                    "thread_id": thread_id,
                    "action": decision.action,
                    "reason": decision.reason,
                    "age_s": decision.age_s,
                }
            )
            continue
        if fired:
            results.append(
                {
                    "thread_id": thread_id,
                    "action": "skip",
                    "reason": "one_fire_per_scan",
                    "age_s": decision.age_s,
                }
            )
            continue
        outcome = await fire_hop_for_decision(
            decision,
            queue=queue,
            row=row,
            path=path,
            snapshot_reader=snapshot_reader,
        )
        results.append(outcome)
        if outcome.get("ok"):
            fired = True
    return results


async def hop_cadence_loop(app: Any) -> None:
    """Background loop: age enrolled operator lanes and fire continuity hops."""
    from services.git_integration_worker.cursor_auto.queue import get_queue

    _ = app  # lifespan parity with sibling loops; queue is process-global
    logger.info(
        "cursor-auto hop cadence loop started threshold_s=%.1f cooldown_s=%.1f scan_s=%.1f",
        age_threshold_s(),
        cooldown_s(),
        scan_interval_s(),
    )
    while True:
        try:
            reconcile_stall_revocations()
            reconcile_succession_confirmations(snapshot_reader=read_cdp_lane_snapshot)
            outcomes = await scan_and_fire(queue=get_queue())
            due = [
                o
                for o in outcomes
                if o.get("ok") or o.get("reason") == "capacity_blocked"
            ]
            if due:
                logger.info("hop_cadence scan outcomes=%s", due)
            elif outcomes:
                logger.debug(
                    "hop_cadence scan n=%s sample=%s",
                    len(outcomes),
                    outcomes[:3],
                )
        except Exception as exc:  # noqa: BLE001 — never kill the worker loop
            logger.exception("hop_cadence loop iteration failed: %s", exc)
        await asyncio.sleep(scan_interval_s())
