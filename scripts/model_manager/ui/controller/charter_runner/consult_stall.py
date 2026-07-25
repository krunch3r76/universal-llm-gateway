"""Recover a quiet, stale consult window after fencing its worker.

An admitted R-ADMIT only unblocks the root; a later window must still read and
disposition the verdict. Missing or rejecting R-ADMIT turns instead requeue the
prior gated pickup with explicit abandonment lineage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import bus_client, window_log
from .caps import CapStore
from .checkpoint_parse import parse_checkpoint
from .consult_stall_build import (
    R_ADMIT_SUBJECT_PREFIX,
    build_consult_stall_requeue_checkpoint,
    build_r_admit_advance_checkpoint,
    discover_child_refs,
    find_r_admit_after,
)
from .eligibility import Decision
from .harvest import harvest_completed_windows
from .self_heal import turn_number, window_terminal_after

logger = get_logger(__name__)

# Shorter than DEFAULT_AUTONOMOUS_STALE_S (3600) — consult CDP should resolve
# or fail well under this; hung consult seats must not pin the root for an hour.
DEFAULT_CONSULT_STALE_S = 900.0
QUIESCENCE_S = 600.0
CONSULT_STALL_HEAL_CAP = 2


def _admission_mode(meta: dict[str, Any], fallback: str) -> str:
    raw = str(meta.get("admission_mode") or "").strip().lower()
    if raw in {"autonomous", "handoff", "generate", "consult"}:
        return raw
    return fallback


def _turn_created_at(turn: dict[str, Any]) -> datetime | None:
    raw = turn.get("created_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _last_activity_age_s(
    *,
    root_turns: list[dict[str, Any]],
    worker_turns: list[dict[str, Any]],
    admission_turn_number: int,
    admission_age_s: float,
    now: datetime,
) -> float:
    """Return zero when activity timestamps are present but not auditable."""
    candidates = [
        turn for turn in root_turns if turn_number(turn) > admission_turn_number
    ]
    candidates.extend(worker_turns)
    if not candidates:
        return admission_age_s
    timestamps = [_turn_created_at(turn) for turn in candidates]
    if any(timestamp is None for timestamp in timestamps):
        return 0.0
    latest = max(timestamp for timestamp in timestamps if timestamp is not None)
    return max(0.0, (now - latest).total_seconds())


async def try_recover_consult_stall(
    decision: Decision,
    *,
    root_turns: list[dict[str, Any]],
    caps: CapStore,
    age_s: float,
    admission_mode: str,
    stale_s: float = DEFAULT_CONSULT_STALE_S,
    quiescence_s: float = QUIESCENCE_S,
    heal_cap: int = CONSULT_STALL_HEAL_CAP,
) -> bool:
    """Fence and recover a consult window only after stale quiescence."""
    if age_s < stale_s:
        return False
    adm = decision.admission_turn or {}
    meta = window_log.parse_admission_meta(str(adm.get("body") or ""))
    mode = _admission_mode(meta, admission_mode)
    if mode != "consult":
        return False
    adm_n = turn_number(adm)
    if adm_n <= 0:
        return False
    if window_terminal_after(root_turns, adm_n) is not None:
        return False

    worker_thread = str(meta.get("worker_thread") or "")
    if not worker_thread:
        logger.warning(
            "charter-runner consult-stall skipped root=%s — no worker to fence",
            decision.root_id,
        )
        return False
    try:
        window_index = int(meta.get("window") or 0)
    except (TypeError, ValueError):
        window_index = 0

    try:
        worker_turns = await bus_client.fetch_turns(worker_thread)
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner consult-stall activity probe failed for %s",
            worker_thread,
        )
        return False
    activity_age_s = _last_activity_age_s(
        root_turns=root_turns,
        worker_turns=worker_turns,
        admission_turn_number=adm_n,
        admission_age_s=age_s,
        now=datetime.now(UTC),
    )
    if activity_age_s < quiescence_s:
        return False

    prior_body = str((decision.checkpoint or {}).get("body") or "")
    prior = parse_checkpoint(prior_body)
    r_admit = find_r_admit_after(root_turns, adm_n)

    if r_admit is None and not prior.next_pickup_gated and not prior.next_pickup:
        logger.warning(
            "charter-runner consult-stall skipped root=%s — no prior pickup",
            decision.root_id,
        )
        emit_abort = getattr(events, "emit_manage_charter_tick_self_heal_aborted", None)
        if emit_abort is not None:
            await emit_abort(
                root=decision.root_id,
                reason="no_prior_pickup",
                window_index=window_index,
            )
        return False

    heals = caps.increment_consult_stall_heal(decision.root_id)
    if heals > heal_cap:
        caps.mark_failed(decision.root_id, "no_progress:consult_stall")
        await events.emit_manage_charter_tick_window_failed(
            root=decision.root_id, reason="no_progress:consult_stall"
        )
        return False

    if r_admit is not None:
        reason = "r_admit_on_root"
        subject, body = build_r_admit_advance_checkpoint(
            prior=prior,
            window_index=window_index or 0,
            worker_thread=worker_thread,
            r_admit_turn=r_admit,
            generation=heals,
        )
    else:
        reason = "consult_stall_requeue"
        subject, body = build_consult_stall_requeue_checkpoint(
            prior=prior,
            window_index=window_index or 0,
            worker_thread=worker_thread,
            child_refs=discover_child_refs(root_turns, adm_n),
            generation=heals,
        )

    try:
        await bus_client.close_worker_thread(
            worker_thread,
            summary=(
                f"charter-runner consult-stall {reason} — "
                f"root {decision.root_id} · heal:consult_stall gen={heals}"
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner consult-stall worker fence failed %s",
            worker_thread,
        )
        return False

    try:
        await bus_client.post_root_checkpoint(
            decision.root_id, subject=subject, body=body
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner consult-stall CHECKPOINT post failed root=%s",
            decision.root_id,
        )
        return False

    harvested = False
    try:
        fresh = await bus_client.fetch_turns(decision.root_id)
        await harvest_completed_windows(decision.root_id, fresh)
        harvested = True
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner consult-stall harvest failed root=%s",
            decision.root_id,
        )

    caps.reset(decision.root_id)
    emit = getattr(events, "emit_manage_charter_tick_consult_stall_recovered", None)
    if emit is None:
        # Fall back to self_healed so older manage processes still emit something.
        emit = getattr(events, "emit_manage_charter_tick_self_healed", None)
    if emit is not None:
        await emit(
            root=decision.root_id,
            reason=reason,
            window_index=window_index,
            worker_thread=worker_thread,
            heal_count=heals,
            harvested=harvested,
        )
    else:
        logger.warning(
            "consult_stall emitter missing; recovery committed root=%s reason=%s",
            decision.root_id,
            reason,
        )
    return True


def admission_age_s(admission_turn: dict[str, Any]) -> float | None:
    """Seconds since admission ``posted_at``, or None if unparseable."""
    try:
        meta = window_log.parse_admission_meta(str(admission_turn.get("body") or ""))
        raw = meta.get("posted_at")
        if not raw:
            return None
        parsed = datetime.fromisoformat(str(raw))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return (datetime.now(UTC) - parsed).total_seconds()
    except (ValueError, TypeError):
        return None


__all__ = [
    "DEFAULT_CONSULT_STALE_S",
    "R_ADMIT_SUBJECT_PREFIX",
    "admission_age_s",
    "build_r_admit_advance_checkpoint",
    "find_r_admit_after",
    "try_recover_consult_stall",
]
