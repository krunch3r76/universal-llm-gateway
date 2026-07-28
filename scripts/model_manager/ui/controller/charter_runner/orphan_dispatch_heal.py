"""Recover in-flight windows whose GIW dispatch never ran or left without closeout."""

from __future__ import annotations

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import bus_client, window_log
from .attendance import admission_mode_for_root
from .caps import CapStore
from .checkpoint_body import resolve_checkpoint_body
from .checkpoint_parse import parse_checkpoint
from .eligibility import Decision
from .giw_live_hold import dispatch_ids_from_active_work, fetch_giw_active_work_payload
from .self_heal import CHECKPOINT_MISSING_HEAL_CAP
from .self_heal_checkpoint import build_self_heal_checkpoint, pickup_survives_round_trip

logger = get_logger(__name__)

DISPATCH_ORPHAN = "dispatch_orphan"
ORPHAN_GRACE_S = 120.0


def decision_needs_fleet_slot(decision: Decision) -> bool:
    """True when admission would consume the GIW cursor-sdk fleet slot."""
    if admission_mode_for_root(decision.root_id) == "handoff":
        return False
    if decision.window_kind == "consult" and decision.parsed is not None:
        role = (decision.parsed.consult_role or "").strip().lower()
        if role and role != "r_admit":
            return False
    return True


async def try_recover_orphan_dispatch(
    decision: Decision,
    *,
    caps: CapStore,
    age_s: float,
    grace_s: float = ORPHAN_GRACE_S,
) -> bool:
    """Post machine CHECKPOINT when a window is in-flight but GIW lost the dispatch."""
    if admission_mode_for_root(decision.root_id) == "handoff":
        return False
    if age_s < grace_s:
        return False
    adm = decision.admission_turn or {}
    meta = window_log.parse_admission_meta(str(adm.get("body") or ""))
    worker_thread = str(meta.get("worker_thread") or "")
    if not worker_thread:
        return False
    try:
        worker_turns = await bus_client.fetch_turns(worker_thread)
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner orphan probe failed fetching worker %s", worker_thread
        )
        return False
    if bus_client.closeout_status_from_turns(worker_turns) is not None:
        return False
    if await bus_client.worker_failure_reason(worker_thread) is not None:
        return False
    dispatch_id = window_log.dispatch_id_from_transcript(worker_thread)
    payload = await fetch_giw_active_work_payload()
    active_ids = dispatch_ids_from_active_work(payload or {})
    if dispatch_id and dispatch_id in active_ids:
        return False
    try:
        window_index = int(meta.get("window") or 0)
    except (TypeError, ValueError):
        window_index = 0
    prior_body = str((decision.checkpoint or {}).get("body") or "")
    prior = parse_checkpoint(
        resolve_checkpoint_body(
            prior_body,
            sidecar_uri=(
                (decision.checkpoint or {}).get("sidecar_uri")
                if isinstance((decision.checkpoint or {}).get("sidecar_uri"), str)
                else None
            ),
        )
    )
    if not prior.next_pickup_gated and not prior.next_pickup:
        return False
    subject, body = build_self_heal_checkpoint(
        prior=prior,
        window_index=window_index or 0,
        worker_thread=worker_thread,
        reason=DISPATCH_ORPHAN,
        root_id=decision.root_id,
    )
    ok, want, got = pickup_survives_round_trip(prior, body)
    if not ok:
        logger.error(
            "charter-runner orphan heal ABORT root=%s — pickup round trip failed "
            "(want=%r got=%r)",
            decision.root_id,
            want,
            got,
        )
        return False
    heals = caps.increment_heal(decision.root_id)
    if heals > CHECKPOINT_MISSING_HEAL_CAP:
        caps.mark_failed(decision.root_id, f"no_progress:{DISPATCH_ORPHAN}")
        await events.emit_manage_charter_tick_window_failed(
            root=decision.root_id, reason=f"no_progress:{DISPATCH_ORPHAN}"
        )
        return False
    try:
        await bus_client.post_root_checkpoint(
            decision.root_id, subject=subject, body=body
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner orphan heal CHECKPOINT post failed root=%s",
            decision.root_id,
        )
        return False
    caps.reset(decision.root_id)
    try:
        from .residue_store import clear_residue_record

        clear_residue_record(decision.root_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner orphan heal residue clear failed root=%s",
            decision.root_id,
        )
    if window_index > 0:
        caps.clear_admit_intent(decision.root_id, window_index)
    emit = getattr(events, "emit_manage_charter_tick_self_healed", None)
    if emit is not None:
        await emit(
            root=decision.root_id,
            reason=DISPATCH_ORPHAN,
            window_index=window_index,
            worker_thread=worker_thread,
            heal_count=heals,
            harvested=False,
        )
    return True
