"""Autonomous self-heal for incomplete charter windows.

Contract: a worker ``status∈{complete,partial}`` without *any* bound window
terminal (CHECKPOINT / CONSULT_PENDING / BLOCKED / PACKAGING_DEFICIT) after the
admission WIP pointer is a **checkpoint_missing** breach — not a successful
window. Autonomous mode posts a machine CHECKPOINT that re-queues the prior
gated Next-pickup, harvests the window, resets CapStore stop, and lets the next
tick re-admit.

Attested by R-admit (agent-bus:5743 / cortex archive deeffdcd): A1–A7.
Attended handoff mode does not auto-heal (operator may still be open in IDE).
Schema-class skips are healed via ``schema_skip_heal.try_self_heal_schema_skip``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cortex_store.dispatch_ops._friction_enqueue import (
    file_charter_protocol_friction,
)
from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import bus_client, window_log
from .caps import CapStore
from .checkpoint_parse import parse_checkpoint
from .window_terminal_contract import terminal_verb
from .eligibility import Decision
from .harvest import harvest_completed_windows
from .self_heal_checkpoint import (
    build_self_heal_checkpoint,
    pickup_survives_round_trip,
)

logger = get_logger(__name__)

CHECKPOINT_MISSING = "checkpoint_missing"
# Grace after worker closeout so a late root CHECKPOINT can land before heal.
CHECKPOINT_MISSING_GRACE_S = 120.0
CHECKPOINT_MISSING_HEAL_CAP = 2


def turn_number(turn: dict[str, Any]) -> int:
    """Parse a turn's ``turn_number`` as int; corrupt/missing values become 0."""
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def window_terminal_after(turns: list[dict[str, Any]], after_n: int) -> str | None:
    """Subject-prefix match on bound stop vocabulary; None = no terminal posted."""
    for turn in turns:
        if turn_number(turn) <= after_n:
            continue
        verb = terminal_verb(
            str(turn.get("subject") or ""), body=str(turn.get("body") or "")
        )
        if verb is not None:
            return verb
    return None


def incomplete_window_reason(
    *,
    root_turns: list[dict[str, Any]],
    admission_turn: dict[str, Any],
    worker_status: str | None,
) -> tuple[str | None, str | None]:
    """Return ``(heal_reason, terminal_verb)``.

    ``checkpoint_missing`` when worker succeeded without a window terminal.
    When a bound non-CHECKPOINT terminal already landed, return
    ``(None, verb)`` so the caller can emit ``terminal_not_checkpoint:<verb>``
    (R-admit A1 observability) without healing over consult/blocked state.
    """
    if worker_status not in {"complete", "partial"}:
        return None, None
    adm_n = turn_number(admission_turn)
    if adm_n <= 0:
        logger.warning(
            "charter-runner self-heal skipped — admission turn_number unusable"
        )
        return None, None
    terminal = window_terminal_after(root_turns, adm_n)
    if terminal is not None:
        return None, terminal
    return CHECKPOINT_MISSING, None


def _resolve_admission_mode(meta: dict[str, Any], fallback: str) -> str:
    raw = str(meta.get("admission_mode") or "").strip().lower()
    if raw in {"autonomous", "handoff", "generate", "consult"}:
        return raw
    return fallback


async def closeout_within_grace(
    decision: Decision,
    *,
    grace_s: float = CHECKPOINT_MISSING_GRACE_S,
) -> bool:
    """True when worker closeout exists and post-close grace has not elapsed.

    Used by the stale path so admission-age stale does not fire while the
    closeout-anchored heal grace still holds (R-admit A3/A5 interaction).
    """
    adm = decision.admission_turn or {}
    meta = window_log.parse_admission_meta(str(adm.get("body") or ""))
    worker_thread = str(meta.get("worker_thread") or "")
    if not worker_thread:
        return False
    try:
        worker_turns = await bus_client.fetch_turns(worker_thread)
    except Exception:  # noqa: BLE001
        return False
    closed_at = bus_client.closeout_posted_at_from_turns(worker_turns)
    if closed_at is None:
        return False
    return (datetime.now(UTC) - closed_at).total_seconds() < grace_s


async def try_self_heal_incomplete_window(
    decision: Decision,
    *,
    root_turns: list[dict[str, Any]],
    caps: CapStore,
    age_s: float,
    admission_mode: str,
    grace_s: float = CHECKPOINT_MISSING_GRACE_S,
    heal_cap: int = CHECKPOINT_MISSING_HEAL_CAP,
) -> bool:
    """If incomplete window past closeout grace, post CHECKPOINT + harvest + reset.

    Returns True when heal ran (caller should skip waiting_open / stale stop).
    Autonomous-only; attended modes leave the soft remind / stale path alone.
    """
    adm = decision.admission_turn or {}
    meta = window_log.parse_admission_meta(str(adm.get("body") or ""))
    mode = _resolve_admission_mode(meta, admission_mode)
    if mode != "autonomous":
        return False
    worker_thread = str(meta.get("worker_thread") or "")
    try:
        window_index = int(meta.get("window") or 0)
    except (TypeError, ValueError):
        window_index = 0
    worker_status: str | None = None
    worker_turns: list[dict[str, Any]] = []
    if worker_thread:
        try:
            worker_turns = await bus_client.fetch_turns(worker_thread)
            worker_status = bus_client.closeout_status_from_turns(worker_turns)
        except Exception:  # noqa: BLE001 — leave for waiting_open / stale
            logger.exception(
                "charter-runner self-heal worker probe failed for %s", worker_thread
            )
            return False
    closed_at = bus_client.closeout_posted_at_from_turns(worker_turns)
    if closed_at is not None:
        if (datetime.now(UTC) - closed_at).total_seconds() < grace_s:
            return False
    elif age_s < grace_s:
        return False
    reason, terminal = incomplete_window_reason(
        root_turns=root_turns,
        admission_turn=adm,
        worker_status=worker_status,
    )
    if reason is None:
        if terminal and terminal != "CHECKPOINT":
            cp_turn = (decision.checkpoint or {}).get("turn_number")
            emit_skip = getattr(events, "emit_manage_charter_tick_root_skipped", None)
            if emit_skip is not None:
                await emit_skip(
                    root=decision.root_id,
                    reason=f"terminal_not_checkpoint:{terminal}",
                    checkpoint_turn=int(cp_turn) if cp_turn is not None else None,
                )
        return False
    prior_body = str((decision.checkpoint or {}).get("body") or "")
    from .checkpoint_body import resolve_checkpoint_body

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
        logger.warning(
            "charter-runner self-heal skipped root=%s — prior CHECKPOINT has no pickup",
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
    subject, body = build_self_heal_checkpoint(
        prior=prior,
        window_index=window_index or 0,
        worker_thread=worker_thread,
        reason=reason,
        root_id=decision.root_id,
        friction_id=file_charter_protocol_friction(
            root_id=decision.root_id,
            window_index=window_index or 0,
            note=(
                "worker reported success-shaped closeout without posting a bound "
                "window terminal on this root"
            ),
            scoreboard_uri=prior.scoreboard_uri,
            actionable=False,
            actionable_false_reason="machine self-heal recovery checkpoint",
        ),
    )
    ok, want, got = pickup_survives_round_trip(prior, body)
    if not ok:
        logger.error(
            "charter-runner self-heal ABORT root=%s — pickup did not survive "
            "round trip (want=%r got=%r)",
            decision.root_id,
            want,
            got,
        )
        emit_abort = getattr(events, "emit_manage_charter_tick_self_heal_aborted", None)
        if emit_abort is not None:
            await emit_abort(
                root=decision.root_id,
                reason="pickup_round_trip",
                window_index=window_index,
            )
        return False
    heals = caps.increment_heal(decision.root_id)
    if heals > heal_cap:
        caps.mark_failed(decision.root_id, "no_progress:checkpoint_missing")
        await events.emit_manage_charter_tick_window_failed(
            root=decision.root_id, reason="no_progress:checkpoint_missing"
        )
        return False
    try:
        await bus_client.post_root_checkpoint(
            decision.root_id, subject=subject, body=body
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner self-heal CHECKPOINT post failed root=%s", decision.root_id
        )
        return False
    harvested = False
    try:
        fresh = await bus_client.fetch_turns(decision.root_id)
        await harvest_completed_windows(decision.root_id, fresh)
        harvested = True
    except Exception:  # noqa: BLE001 — CHECKPOINT already clears in-flight
        logger.exception(
            "charter-runner self-heal harvest failed root=%s", decision.root_id
        )
    caps.reset(decision.root_id)
    emit = getattr(events, "emit_manage_charter_tick_self_healed", None)
    if emit is None:
        logger.warning(
            "self_healed emitter missing (stale manage); heal committed root=%s",
            decision.root_id,
        )
    else:
        await emit(
            root=decision.root_id,
            reason=reason,
            window_index=window_index,
            worker_thread=worker_thread,
            heal_count=heals,
            harvested=harvested,
        )
    return True
