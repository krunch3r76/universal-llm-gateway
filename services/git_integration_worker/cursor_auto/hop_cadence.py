"""Cursor-auto hop-cadence actuator — fire continuity hops without CDP self-drive.

The operator CSE cannot perceive its own depth and cannot ``team_dispatch`` from
the life seat. This module is the external actuator: age enrolled watches and
self-enqueue structural ``TYPE: CONTINUITY_HANDOFF`` when the threshold elapses.

Watch ledger / evaluate live in ``hop_cadence_watch``. This file owns capacity
gating, hop body authorship, enqueue+commission, and the background loop.

Invariants:
- Hop ≠ close: only the existing continuity-hop path; never ``MISSION_CLOSEOUT``.
- Silent/degraded seats still hop once a watch is enrolled.
- Wake-count is rejected as primary signal (Cowork-internal, falsified at #6).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_bundles.hop_seat_cutover import refuse_cadence_hop_for_live_seat
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
) -> str:
    """Author the structural CONTINUITY_HANDOFF body Auto self-enqueues on fire."""
    handoff = decision.handoff or assess_standing_handoff(decision.thread_id)
    lines = [
        "TYPE: CONTINUITY_HANDOFF",
        "contract: light-bounded",
        "source: cursor-auto-hop-cadence",
        f"trigger: {decision.signal or 'watch_seated_at'}",
        f"thread_id: {decision.thread_id}",
        f"cse_age_s: {decision.age_s:.1f}" if decision.age_s is not None else "cse_age_s: unknown",
        f"threshold_s: {decision.threshold_s:.1f}"
        if decision.threshold_s is not None
        else f"threshold_s: {age_threshold_s():.1f}",
        f"standing_handoff: {handoff.uri}",
        f"standing_handoff_freshness: {handoff.status}",
    ]
    if handoff.age_s is not None:
        lines.append(f"standing_handoff_age_s: {handoff.age_s:.1f}")
    if registration_id:
        lines.append(f"superseded_registration_id: {registration_id}")
    lines.extend(
        [
            "",
            "Resume as operator-proxy on this private lane.",
            "Read the standing handoff URI above before trusting any wake prose.",
            "This is a CONTINUITY HOP (seat refresh) — do NOT emit MISSION_CLOSEOUT.",
            "Arc continues; predecessor wakes must be torn down only after this",
            "successor launch is confirmed.",
            "",
            "KEEP-ALIVE / wake cycle (BINDING — 6661 sole-wake · suspended pattern):",
            "Do NOT arm Monitor loops. Do NOT re-arm send_later for durable wake.",
            "Wake authority is the mission PRIMARY orchestrator only (monitor 6661 ↔",
            "mission root) — hop successors are subordinates, not peer wake servers.",
            "If you inherit a predecessor Monitor, TaskStop it after successor admit;",
            "delete only trigger_ids this seat recorded (never class-delete).",
            "CDP one-off work from the mission runner remains fine; keep-alive is not",
            "ready for fleet hops under the current pattern.",
            "(cursor-auto cannot reach Cowork-internal timers — seat duty.)",
        ]
    )
    return "\n".join(lines) + "\n"


def capacity_blocks_hop(
    *,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
) -> tuple[bool, str | None]:
    """Return (blocked, label) when CDP capacity cannot admit a successor window.

    Hop cadence is a seat *replacement* within hard=3: admit when ``free_slots >= 1``
    so a successor window can occupy the last hard slot while the predecessor
    streams out. The generic unattended soft gate (``escalation_lane_refusal`` with
    ``at_soft_limit``) requires ``free_slots >= 2`` and must not apply here.
    """
    reader = snapshot_reader or read_cdp_lane_snapshot
    try:
        snap = reader()
    except Exception as exc:  # noqa: BLE001 — cadence must not crash the worker
        logger.warning("hop_cadence capacity probe failed: %s", exc)
        return False, None
    if not isinstance(snap, dict):
        return False, None
    if snap.get("at_hard_limit"):
        return True, "hard"
    free_slots = int(snap.get("free_slots") or 0)
    if free_slots < 1:
        return True, "hard"
    return False, None


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
    blocked, label = capacity_blocks_hop(snapshot_reader=reader)
    if blocked:
        logger.warning(
            "hop_cadence defer thread=%s reason=capacity label=%s",
            decision.thread_id,
            label,
        )
        return {
            "ok": False,
            "reason": "capacity_blocked",
            "capacity_label": label,
            "thread_id": decision.thread_id,
        }
    liveness_probe: dict[str, Any] = {"fail_open": False}
    try:
        snap = reader()
    except Exception as exc:  # noqa: BLE001 — cadence must not crash the worker
        logger.warning("hop_cadence liveness probe failed: %s", exc)
        snap = {}
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
    # Same lookup as HTTP hop (routes/cursor_auto.py): name the live claimed
    # commission so CONTINUITY_HARVEST_RESIDUAL is truthful. Hop still skips
    # supersede — reporting only.
    incumbent = queue.claimed_for_thread(decision.thread_id)
    if incumbent is not None and incumbent.job_id == job.job_id:
        incumbent = None
    result = await run_continuity_hop_concurrent(
        job, queue=queue, incumbent=incumbent
    )
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
        hop_ok = True
        mark_hop_fired(decision.thread_id, execution_id=execution_id, path=path)
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
        decision = evaluate_watch(row, now=ts)
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
            due = [o for o in outcomes if o.get("ok") or o.get("reason") == "capacity_blocked"]
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
