"""Poll-only CDP generate reconcile loop + shared finalize (restart recovery)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from cdp_ask.client import CdpAskClient, CdpAskClientError
from cdp_ask.unverifiable import WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED
from claude_bundles.cdp_model_endpoint import (
    CdpGenerateResult,
    picker_from_model_id,
    result_from_snapshot,
)
from universal_logging import get_logger

from .cdp_events import (
    CdpGenerateProof,
    CdpGenerateReconciled,
    CdpGenerateStalled,
    publish_cdp_kwargs,
    publish_horizon_unverifiable_once,
    reset_horizon_unverifiable_emits_for_tests,
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
from .cdp_horizon_probe import (
    HorizonObservation,
    classify_horizon_probe,
    fetch_recent_thread_turns,
    leg_scoped_horizon_hit,
    seated_authorship_on_thread,
)

logger = get_logger(__name__)

FinalizeVia = Literal["worker", "reconcile", "attest"]
RECONCILE_INTERVAL_S = 20.0
HARVEST_LAG_S = 600.0
MIN_OPEN_LEG_S = 3600.0
STALL_RECONCILE_ABANDONED_CONFIRMED = "reconcile_abandoned_confirmed_dead"
STALL_RECONCILE_ABANDONED_UNVERIFIABLE = "reconcile_abandoned_unverifiable"
STALL_HORIZON_UNVERIFIABLE_RETAINED = "horizon_unverifiable_retained"
STALL_HORIZON_SEATED_AUTHORSHIP = "horizon_seated_authorship"

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
    "HorizonObservation",
    "classify_horizon_probe",
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


def _leg_registration_id(
    leg: InflightLeg, snapshot: dict[str, Any] | None
) -> str | None:
    owner = (leg.owner or "").strip()
    if owner:
        return owner
    if not snapshot:
        return None
    raw = snapshot.get("cse_registration_id") or snapshot.get("registration_id")
    text = str(raw or "").strip()
    return text or None


def _successor_birth_id(snapshot: dict[str, Any] | None) -> str | None:
    if not snapshot:
        return None
    text = str(snapshot.get("successor_birth_id") or "").strip()
    return text or None


async def _failed_horizon_scoped(
    leg: InflightLeg, snapshot: dict[str, Any] | None
) -> bool:
    """Leg-scoped retain evidence for a failed-at-horizon poll."""
    turns = await fetch_recent_thread_turns(leg.thread_id)
    return leg_scoped_horizon_hit(
        turns,
        admitted_at=leg.admitted_at,
        registration_id=_leg_registration_id(leg, snapshot),
        successor_birth_id=_successor_birth_id(snapshot),
    )


async def _retain_unverifiable_horizon(
    leg: InflightLeg, *, detail: str, scoped: bool = False
) -> None:
    """Retain the open inflight row; unverifiable is not death (G1 AC1)."""
    seated = scoped or await seated_authorship_on_thread(leg.thread_id)
    stall_stage = (
        STALL_HORIZON_SEATED_AUTHORSHIP
        if seated
        else STALL_HORIZON_UNVERIFIABLE_RETAINED
    )
    logger.info(
        "cdp reconcile horizon: %s execution_id=%s sat=%s detail=%s",
        "seated authorship, extending" if seated else "unverifiable retained",
        leg.execution_id,
        leg.satellite_execution_id,
        detail,
    )
    publish_horizon_unverifiable_once(
        request_id=leg.request_id,
        execution_id=leg.execution_id,
        satellite_execution_id=leg.satellite_execution_id,
        thread_id=leg.thread_id,
        stall_stage=stall_stage,
        error=detail,
    )


async def _reconcile_horizon_leg(leg: InflightLeg, *, horizon: float) -> None:
    """Horizon triggers a liveness probe — abandon only when confirmed dead."""
    if not leg.satellite_execution_id:
        await _retain_unverifiable_horizon(
            leg,
            detail=(
                "horizon crossed without satellite_execution_id; liveness unverifiable"
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

    status = str((snapshot or {}).get("status") or "")
    if status == "failed":
        detail = str((snapshot or {}).get("error") or "failed at horizon")
        if await _failed_horizon_scoped(leg, snapshot):
            await _retain_unverifiable_horizon(leg, detail=detail, scoped=True)
            return
        await _emit_reconcile_abandon(
            leg,
            horizon=horizon,
            stall_stage=STALL_RECONCILE_ABANDONED_UNVERIFIABLE,
            error=detail or "failed at horizon without leg-scoped evidence",
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
            purpose=leg.purpose,
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
    detail = str(probe_err or "poll returned None")
    await _retain_unverifiable_horizon(leg, detail=detail)


async def finalize_cdp_generate(
    *,
    result: CdpGenerateResult,
    request_id: str,
    thread_id: str,
    to_agent: str,
    pointer_turn: int,
    via: FinalizeVia = "worker",
    attested_by: str | None = None,
) -> None:
    """Publish proof or stalled once, then attempt on-behalf delivery.

    An unconfirmed wall-expiry stall (``wall_clock_exceeded_abort_unconfirmed``)
    returns without setting ``proof_emitted``, so reconcile horizon decides death.
    """
    from .cdp_dispatch_envelope import (
        get_cdp_dispatch_envelope,
        record_cdp_dispatch_link_terminal,
    )
    from .cdp_generate_worker import (
        _emit_upstream_overload_friction,
        _upstream_overloaded,
        deliver_cdp_result_turn,
    )
    from .handoff import terminate_handoff_dispatch

    envelope = get_cdp_dispatch_envelope(result.execution_id)
    dispatch_link_terminal: bool | None = None

    async def _terminate_dispatch_link(
        *,
        terminal_status: str,
        archive_uri: str | None = None,
    ) -> bool | None:
        nonlocal dispatch_link_terminal
        if envelope is None or envelope.admit_reason != "ok":
            dispatch_link_terminal = None
            return None
        bus_lifecycle = (
            "persistent"
            if terminal_status == "completed" and envelope.caller_supplied_thread
            else None
        )
        ok = await terminate_handoff_dispatch(
            request_id=request_id,
            thread_id=thread_id,
            execution_id=result.execution_id,
            terminal_status=terminal_status,
            bus_lifecycle=bus_lifecycle,
            archive_uri=archive_uri,
        )
        dispatch_link_terminal = ok
        record_cdp_dispatch_link_terminal(
            execution_id=result.execution_id, terminated=ok
        )
        return ok

    def _enrich_result(base: CdpGenerateResult) -> CdpGenerateResult:
        if envelope is None:
            return base
        merged = dict(base.extras or {})
        merged.setdefault("thread_id", envelope.thread_id)
        merged.setdefault("pointer_turn", envelope.pointer_turn)
        if envelope.admit_reason == "ok":
            if dispatch_link_terminal is True:
                term = "failed" if not base.ok else "completed"
                merged.setdefault("dispatch_link", f"terminated:{term}")
            elif dispatch_link_terminal is False:
                merged.setdefault("dispatch_link", "absent:terminate_failed")
        else:
            merged.setdefault("dispatch_link", f"absent:{envelope.admit_reason}")
        return CdpGenerateResult(
            ok=base.ok,
            body=base.body,
            execution_id=base.execution_id,
            satellite_execution_id=base.satellite_execution_id,
            prompt_uri=base.prompt_uri,
            picker_model=base.picker_model,
            archive_uri=base.archive_uri,
            content_proof_uri=base.content_proof_uri,
            content_proof_sha256=base.content_proof_sha256,
            stall_stage=base.stall_stage,
            error=base.error,
            substrate=base.substrate,
            cost_source=base.cost_source,
            poll_snapshots=base.poll_snapshots,
            extras=merged,
        )

    leg = read_inflight_leg(result.execution_id)
    if leg is not None and leg.proof_emitted:
        return

    if terminal_event_exists(result.execution_id):
        if result.stall_stage == WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED:
            return
        # Terminate before proof. proof_emitted=1 drops the leg from the open
        # selector, so a failed terminate must leave the row retryable.
        link_owed = envelope is not None and envelope.admit_reason == "ok"
        terminated = await _terminate_dispatch_link(
            terminal_status="completed" if result.ok else "failed",
        )
        if link_owed and terminated is not True:
            return
        mark_proof_emitted(result.execution_id)
        if not try_claim_delivery(execution_id=result.execution_id):
            return
        enriched = _enrich_result(result)
        posted = await deliver_cdp_result_turn(
            result=enriched,
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

    if not result.ok:
        await _terminate_dispatch_link(terminal_status="failed")

    sat_id = result.satellite_execution_id
    event_extras = {
        "thread_id": thread_id,
        "pointer_turn": pointer_turn,
        "dispatch_link_terminal": dispatch_link_terminal,
    }
    if result.extras:
        if result.extras.get("registration_id"):
            event_extras["registration_id"] = result.extras["registration_id"]
        if result.extras.get("chat_url"):
            event_extras["chat_url"] = result.extras["chat_url"]
    if result.ok:
        publish_cdp_kwargs(
            CdpGenerateProof,
            request_id=request_id,
            execution_id=result.execution_id,
            satellite_execution_id=sat_id,
            archive_uri=result.archive_uri,
            content_proof_uri=result.content_proof_uri,
            via=via,
            attested_by=attested_by,
            **event_extras,
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
            since_last_progress_s=(result.extras or {}).get("since_last_progress_s"),
            archive_uri=result.archive_uri,
            deliverable_present=bool(
                result.archive_uri
                or result.content_proof_uri
                or (result.extras or {}).get("deliverable_present_unproven")
            ),
            **event_extras,
        )
        if _upstream_overloaded(result):
            await _emit_upstream_overload_friction(
                execution_id=result.execution_id,
                thread_id=thread_id,
                result=result,
            )

    if result.stall_stage == WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED:
        return

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
    if result.ok:
        enriched = _enrich_result(result)
        posted = await deliver_cdp_result_turn(
            result=enriched,
            thread_id=thread_id,
            to_agent=to_agent,
            request_id=request_id,
            pointer_turn=pointer_turn,
        )
        if posted:
            await _terminate_dispatch_link(terminal_status="completed")
            enriched = _enrich_result(result)
        else:
            await _terminate_dispatch_link(
                terminal_status="failed",
                archive_uri=result.archive_uri,
            )
            enriched = _enrich_result(result)
    else:
        enriched = _enrich_result(result)
        posted = await deliver_cdp_result_turn(
            result=enriched,
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
    # Pipeline legs are consumed by the step poll, not a thread delivery.
    # The only reconcile action is abandon once the horizon has passed.
    if leg.owner == "pipeline":
        if open_s >= horizon:
            mark_abandoned(leg.execution_id)
        return
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
        purpose=leg.purpose,
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
    """Start the reconcile loop without running a pass before returning.

    An awaited boot pass held the caller — lifespan yield, and therefore the
    :9999 bind — on that pass. The loop's first iteration is the boot pass.
    """
    global _reconcile_task
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
    reset_horizon_unverifiable_emits_for_tests()
