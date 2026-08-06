"""Poll-only CDP generate reconcile loop + shared finalize (restart recovery)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from cdp_ask.client import CdpAskClient, CdpAskClientError
from claude_bundles.cdp_model_endpoint import (
    CdpGenerateResult,
    picker_from_model_id,
    result_from_snapshot,
    terminal_failure,
)
from universal_logging import get_logger

from .cdp_events import (
    CdpGenerateProof,
    CdpGenerateReconciled,
    CdpGenerateStalled,
    publish_cdp_kwargs,
)
from .cdp_generate_inflight_ledger import (
    InflightLeg,
    attach_satellite_execution_id,
    clear_delivery_claim,
    clear_inflight_ledger,
    list_open_inflight_legs,
    mark_abandoned,
    mark_proof_emitted,
    read_inflight_leg,
    terminal_event_exists,
    try_claim_delivery,
    try_claim_proof_publish,
    upsert_inflight_leg,
)

logger = get_logger(__name__)

FinalizeVia = Literal["worker", "reconcile"]
HorizonObservation = Literal["alive", "confirmed_dead", "unverifiable"]
RECONCILE_INTERVAL_S = 20.0
HARVEST_LAG_S = 600.0
MIN_OPEN_LEG_S = 3600.0
STALL_RECONCILE_ABANDONED_CONFIRMED = "reconcile_abandoned_confirmed_dead"
STALL_RECONCILE_ABANDONED_UNVERIFIABLE = "reconcile_abandoned_unverifiable"

_reconcile_task: asyncio.Task[None] | None = None
_reconcile_in_flight = False

# Re-export ledger API so existing callers/tests keep importing this module.
__all__ = [
    "InflightLeg",
    "attach_satellite_execution_id",
    "finalize_cdp_generate",
    "list_open_inflight_legs",
    "max_open_leg_s",
    "poll_satellite_snapshot",
    "read_inflight_leg",
    "reconcile_cdp_inflight_legs",
    "reset_cdp_generate_reconcile_for_tests",
    "start_cdp_generate_reconcile",
    "upsert_inflight_leg",
]


def max_open_leg_s(max_wall_s: float) -> float:
    """Abandonment horizon floor ≥3600s (AC7)."""
    return max(float(max_wall_s) + HARVEST_LAG_S, MIN_OPEN_LEG_S)


def _leg_open_seconds(leg: InflightLeg) -> float:
    try:
        admitted = datetime.fromisoformat(leg.admitted_at).timestamp()
    except ValueError:
        return 0.0
    return max(0.0, datetime.now(UTC).timestamp() - admitted)


def _poll_snapshot(satellite_execution_id: str) -> dict[str, Any] | None:
    try:
        return CdpAskClient().poll(satellite_execution_id)
    except CdpAskClientError as exc:
        return {"error": str(exc), "status_code": exc.status_code}


async def poll_satellite_snapshot(satellite_execution_id: str) -> dict[str, Any] | None:
    """Poll-only satellite read for reconcile (no submit/abort)."""
    return await asyncio.to_thread(_poll_snapshot, satellite_execution_id)


def classify_horizon_probe(snapshot: dict[str, Any] | None) -> HorizonObservation:
    """Classify one horizon probe — alive legs extend; dead/unverifiable may abandon."""
    if snapshot is None:
        return "unverifiable"
    if snapshot.get("error") and "status" not in snapshot:
        return "unverifiable"
    status = str(snapshot.get("status") or "")
    if status in {"running", "pending"}:
        return "alive"
    if status in {"failed", "aborted"} or terminal_failure(snapshot):
        return "confirmed_dead"
    return "unverifiable"


async def _emit_reconcile_abandon(
    leg: InflightLeg,
    *,
    horizon: float,
    stall_stage: str,
    error: str,
) -> None:
    abandoned = CdpGenerateResult(
        ok=False,
        body="",
        execution_id=leg.execution_id,
        satellite_execution_id=leg.satellite_execution_id,
        prompt_uri=leg.prompt_uri,
        picker_model=picker_from_model_id(leg.model_id),
        stall_stage=stall_stage,
        error=error,
    )
    await finalize_cdp_generate(
        result=abandoned,
        request_id=leg.request_id,
        thread_id=leg.thread_id,
        to_agent=leg.caller_agent or "dispatch",
        pointer_turn=leg.pointer_turn,
        via="reconcile",
    )
    mark_abandoned(leg.execution_id)


async def _reconcile_horizon_leg(leg: InflightLeg, *, horizon: float) -> None:
    """Horizon triggers a liveness probe — abandon only when dead or unverifiable."""
    if not leg.satellite_execution_id:
        await _emit_reconcile_abandon(
            leg,
            horizon=horizon,
            stall_stage=STALL_RECONCILE_ABANDONED_UNVERIFIABLE,
            error=(
                "horizon crossed without satellite_execution_id; "
                "liveness unverifiable"
            ),
        )
        return

    snapshot = await poll_satellite_snapshot(leg.satellite_execution_id)
    observation = classify_horizon_probe(snapshot)
    if observation == "alive":
        logger.info(
            "cdp reconcile horizon: observed alive, extending execution_id=%s sat=%s",
            leg.execution_id,
            leg.satellite_execution_id,
        )
        return

    if observation == "confirmed_dead":
        status = str((snapshot or {}).get("status") or "")
        await _emit_reconcile_abandon(
            leg,
            horizon=horizon,
            stall_stage=STALL_RECONCILE_ABANDONED_CONFIRMED,
            error=f"satellite confirmed dead at horizon (status={status!r})",
        )
        return

    if snapshot is not None and not (
        snapshot.get("error") and "status" not in snapshot
    ):
        result = result_from_snapshot(
            snapshot=snapshot,
            execution_id=leg.execution_id,
            satellite_execution_id=leg.satellite_execution_id,
            prompt_uri=leg.prompt_uri,
            picker_model=picker_from_model_id(leg.model_id),
        )
        if result is not None:
            await finalize_cdp_generate(
                result=result,
                request_id=leg.request_id,
                thread_id=leg.thread_id,
                to_agent=leg.caller_agent or "dispatch",
                pointer_turn=leg.pointer_turn,
                via="reconcile",
            )
            return

    probe_err = (snapshot or {}).get("error") if snapshot else None
    detail = probe_err or "poll returned None"
    await _emit_reconcile_abandon(
        leg,
        horizon=horizon,
        stall_stage=STALL_RECONCILE_ABANDONED_UNVERIFIABLE,
        error=f"horizon crossed; liveness unverifiable: {detail}",
    )


async def finalize_cdp_generate(
    *,
    result: CdpGenerateResult,
    request_id: str,
    thread_id: str,
    to_agent: str,
    pointer_turn: int,
    via: FinalizeVia = "worker",
) -> None:
    """Publish proof/stalled once, then attempt on-behalf delivery (AC8/AC9)."""
    from .cdp_generate_worker import (
        _emit_upstream_overload_friction,
        _upstream_overloaded,
        deliver_cdp_result_turn,
    )

    leg = read_inflight_leg(result.execution_id)
    if leg is not None and leg.proof_emitted:
        return

    if terminal_event_exists(result.execution_id):
        mark_proof_emitted(result.execution_id)
        if not try_claim_delivery(execution_id=result.execution_id):
            return
        posted = await deliver_cdp_result_turn(
            result=result,
            thread_id=thread_id,
            to_agent=to_agent,
            request_id=request_id,
            pointer_turn=pointer_turn,
        )
        if not posted:
            clear_delivery_claim(result.execution_id)
        return

    holder = f"{via}:{uuid.uuid4().hex[:8]}"
    if not try_claim_proof_publish(execution_id=result.execution_id, holder=holder):
        return

    sat_id = result.satellite_execution_id
    if result.ok:
        publish_cdp_kwargs(
            CdpGenerateProof,
            request_id=request_id,
            execution_id=result.execution_id,
            satellite_execution_id=sat_id,
            archive_uri=result.archive_uri,
            content_proof_uri=result.content_proof_uri,
        )
    else:
        publish_cdp_kwargs(
            CdpGenerateStalled,
            request_id=request_id,
            execution_id=result.execution_id,
            satellite_execution_id=sat_id,
            stall_stage=result.stall_stage,
            error=result.error,
            progress_trace=(result.extras or {}).get("progress_trace"),
        )
        if _upstream_overloaded(result):
            await _emit_upstream_overload_friction(
                execution_id=result.execution_id,
                thread_id=thread_id,
                result=result,
            )

    mark_proof_emitted(result.execution_id)
    if via == "reconcile":
        publish_cdp_kwargs(
            CdpGenerateReconciled,
            request_id=request_id,
            execution_id=result.execution_id,
            satellite_execution_id=sat_id,
            via="reconcile",
        )

    if not try_claim_delivery(execution_id=result.execution_id):
        return
    posted = await deliver_cdp_result_turn(
        result=result,
        thread_id=thread_id,
        to_agent=to_agent,
        request_id=request_id,
        pointer_turn=pointer_turn,
    )
    if not posted:
        clear_delivery_claim(result.execution_id)


async def _reconcile_leg(leg: InflightLeg) -> None:
    open_s = _leg_open_seconds(leg)
    horizon = max_open_leg_s(leg.max_wall_s)
    if open_s >= horizon:
        await _reconcile_horizon_leg(leg, horizon=horizon)
        return

    if not leg.satellite_execution_id:
        return

    snapshot = await poll_satellite_snapshot(leg.satellite_execution_id)
    if snapshot is None:
        return
    if snapshot.get("error") and "status" not in snapshot:
        logger.warning(
            "cdp reconcile poll transport error: execution_id=%s sat=%s err=%s",
            leg.execution_id,
            leg.satellite_execution_id,
            snapshot.get("error"),
        )
        return

    result = result_from_snapshot(
        snapshot=snapshot,
        execution_id=leg.execution_id,
        satellite_execution_id=leg.satellite_execution_id,
        prompt_uri=leg.prompt_uri,
        picker_model=picker_from_model_id(leg.model_id),
    )
    if result is None:
        return

    await finalize_cdp_generate(
        result=result,
        request_id=leg.request_id,
        thread_id=leg.thread_id,
        to_agent=leg.caller_agent or "dispatch",
        pointer_turn=leg.pointer_turn,
        via="reconcile",
    )


async def reconcile_cdp_inflight_legs() -> None:
    """Single-flight reconcile tick over open legs lacking proof (AC1/AC2)."""
    global _reconcile_in_flight
    if _reconcile_in_flight:
        return
    _reconcile_in_flight = True
    try:
        for leg in list_open_inflight_legs():
            try:
                await _reconcile_leg(leg)
            except Exception as exc:  # noqa: BLE001 — per-leg isolation
                logger.warning(
                    "cdp reconcile leg error: execution_id=%s err=%s",
                    leg.execution_id,
                    exc,
                )
    finally:
        _reconcile_in_flight = False


async def _reconcile_loop() -> None:
    while True:
        try:
            await reconcile_cdp_inflight_legs()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("cdp generate reconcile loop error: %s", exc)
        await asyncio.sleep(RECONCILE_INTERVAL_S)


async def start_cdp_generate_reconcile() -> None:
    """Boot one reconcile pass, then start the periodic poll-only catch-up loop."""
    global _reconcile_task
    await reconcile_cdp_inflight_legs()
    if _reconcile_task is None or _reconcile_task.done():
        _reconcile_task = asyncio.create_task(
            _reconcile_loop(), name="cdp-generate-reconcile"
        )


def reset_cdp_generate_reconcile_for_tests() -> None:
    """Clear in-flight ledger and cancel reconcile task (test isolation hook)."""
    global _reconcile_task, _reconcile_in_flight
    _reconcile_in_flight = False
    if _reconcile_task is not None:
        _reconcile_task.cancel()
        _reconcile_task = None
    clear_inflight_ledger()
