"""Durable per-root tick health episodes and unhealthy predicate (BIND 6249)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from libs.charter_runner_store.db import charter_runner_data_dir

from .ledger_age import TICK_STALL_MAX_AGE_S, age_s, clear, observation_count, observe

EpisodeState = Literal["healthy", "suspect", "unhealthy"]

_STATE_FILENAME = "tick-health-episodes.json"
_RECURRING_REFUSE_CONFIRM = 2


class FireAttemptOutcome(StrEnum):
    """Write-time fire-attempt classification threaded from dispatch branches."""

    FIRED = "fired"
    FIRED_BOOKKEEPING_FAILED = "fired_bookkeeping_failed"
    REFUSED_PRE_FIRE = "refused_pre_fire"
    ERRORED_PRE_FIRE = "errored_pre_fire"
    DEFERRED_LEGAL = "deferred_legal"
    WAITING_ON_WORKER = "waiting_on_worker"
    NO_ATTEMPT_QUIET = "no_attempt_quiet"
    INTEGRITY = "integrity"


@dataclass(frozen=True, slots=True)
class AdmitResult:
    """Structured admit path result — replaces bare bool from dispatch."""

    admitted: bool
    fire_attempt_outcome: FireAttemptOutcome
    fire_attempt_reason: str = ""
    dispatch_id: str | None = None
    thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class HealthObservationResult:
    """One observe_root_health pass."""

    unhealthy: bool
    episode_opened: bool
    episode_refired: bool
    fire_attempt_outcome: FireAttemptOutcome | None
    fire_attempt_reason: str | None


def _episode_ttl_s() -> float:
    raw = os.environ.get("CHARTER_TICK_SOS_TTL_S", "3600")
    try:
        return float(raw)
    except ValueError:
        return 3600.0


def _state_path(*, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else charter_runner_data_dir()
    return base / _STATE_FILENAME


def _load_store(*, data_dir: Path | None = None) -> dict[str, Any]:
    path = _state_path(data_dir=data_dir)
    if not path.is_file():
        return {"episodes": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"episodes": {}}
    if not isinstance(raw, dict):
        return {"episodes": {}}
    episodes = raw.get("episodes")
    if not isinstance(episodes, dict):
        raw["episodes"] = {}
    return raw


def _save_store(state: dict[str, Any], *, data_dir: Path | None = None) -> None:
    path = _state_path(data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def is_declared_wait(
    outcome: FireAttemptOutcome | None,
    *,
    consult_pending: bool = False,
    skipped_reason: str | None = None,
) -> bool:
    """Legal standing waits — never fast-leg mark."""
    if outcome in {
        FireAttemptOutcome.DEFERRED_LEGAL,
        FireAttemptOutcome.WAITING_ON_WORKER,
    }:
        return True
    if outcome == FireAttemptOutcome.NO_ATTEMPT_QUIET:
        return True
    reason = (skipped_reason or "").strip()
    if reason in {"dormant", "no_gated_pickup"}:
        return True
    if reason == "empty_hopper" and not consult_pending:
        return True
    return False


def root_has_demand(
    outcome: FireAttemptOutcome | None,
    *,
    consult_pending: bool = False,
    skipped_reason: str | None = None,
) -> bool:
    """Demand gate for fast legs; unknown ⇒ true (slow-fuse backstop)."""
    if outcome is None:
        return True
    if is_declared_wait(
        outcome, consult_pending=consult_pending, skipped_reason=skipped_reason
    ):
        return False
    if outcome == FireAttemptOutcome.NO_ATTEMPT_QUIET:
        return consult_pending
    return True


def _is_stuck_state(
    outcome: FireAttemptOutcome,
    *,
    stopped_reason: str | None,
) -> bool:
    if outcome == FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED:
        return True
    return outcome == FireAttemptOutcome.REFUSED_PRE_FIRE and bool(stopped_reason)


def _is_recurring_refuse(
    outcome: FireAttemptOutcome,
    *,
    stopped_reason: str | None,
    root_id: str,
    data_dir: Path | None,
) -> bool:
    if stopped_reason:
        return False
    if outcome not in {
        FireAttemptOutcome.REFUSED_PRE_FIRE,
        FireAttemptOutcome.ERRORED_PRE_FIRE,
    }:
        return False
    return observation_count("tick_stall", f"{root_id}:refuse", data_dir=data_dir) >= _RECURRING_REFUSE_CONFIRM


def _slow_fuse_tripped(
    root_id: str,
    outcome: FireAttemptOutcome | None,
    *,
    consult_pending: bool,
    skipped_reason: str | None,
    data_dir: Path | None,
) -> bool:
    if outcome in {
        FireAttemptOutcome.FIRED,
        FireAttemptOutcome.WAITING_ON_WORKER,
    }:
        return False
    if is_declared_wait(
        outcome, consult_pending=consult_pending, skipped_reason=skipped_reason
    ):
        return False
    if not root_has_demand(
        outcome, consult_pending=consult_pending, skipped_reason=skipped_reason
    ):
        return False
    return age_s("tick_stall", root_id, data_dir=data_dir) >= TICK_STALL_MAX_AGE_S


def compute_unhealthy(
    root_id: str,
    outcome: FireAttemptOutcome | None,
    *,
    consult_pending: bool = False,
    skipped_reason: str | None = None,
    stopped_reason: str | None = None,
    data_dir: Path | None = None,
) -> bool:
    """BIND §3.1 class-dependent unhealthy predicate."""
    if outcome is None:
        return _slow_fuse_tripped(
            root_id,
            outcome,
            consult_pending=consult_pending,
            skipped_reason=skipped_reason,
            data_dir=data_dir,
        )
    if outcome == FireAttemptOutcome.INTEGRITY:
        return True
    demand = root_has_demand(
        outcome, consult_pending=consult_pending, skipped_reason=skipped_reason
    )
    if _is_stuck_state(outcome, stopped_reason=stopped_reason) and demand:
        return True
    if _is_recurring_refuse(
        outcome, stopped_reason=stopped_reason, root_id=root_id, data_dir=data_dir
    ) and demand:
        return True
    return _slow_fuse_tripped(
        root_id,
        outcome,
        consult_pending=consult_pending,
        skipped_reason=skipped_reason,
        data_dir=data_dir,
    )


def map_dispatch_failure(reason: str) -> FireAttemptOutcome:
    """Map dispatch branch reason to FireAttemptOutcome (S1)."""
    if reason == "pointer_post_failed":
        return FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED
    if reason in {"admission_rejected", "admission_parity", "gate_defer_escalated"} or reason.startswith(
        "gate_defer_escalated:"
    ):
        return FireAttemptOutcome.REFUSED_PRE_FIRE
    if reason in {"admission_transport_error", "admission_exception"}:
        return FireAttemptOutcome.ERRORED_PRE_FIRE
    if reason in {"gate_defer", "lease_held", "giw_draining"}:
        return FireAttemptOutcome.DEFERRED_LEGAL
    return FireAttemptOutcome.ERRORED_PRE_FIRE


def _record_refuse_observation(
    root_id: str,
    outcome: FireAttemptOutcome | None,
    *,
    stopped_reason: str | None,
    data_dir: Path | None,
) -> None:
    key = f"{root_id}:refuse"
    if stopped_reason or outcome not in {
        FireAttemptOutcome.REFUSED_PRE_FIRE,
        FireAttemptOutcome.ERRORED_PRE_FIRE,
    }:
        clear("tick_stall", key, data_dir=data_dir)
        return
    observe("tick_stall", key, present=True, data_dir=data_dir)


def _episode_record(
    store: dict[str, Any], root_id: str
) -> dict[str, Any] | None:
    episodes = store.get("episodes")
    if not isinstance(episodes, dict):
        return None
    rec = episodes.get(root_id)
    return rec if isinstance(rec, dict) else None


def episode_is_open(root_id: str, *, data_dir: Path | None = None) -> bool:
    rec = _episode_record(_load_store(data_dir=data_dir), root_id)
    return rec is not None and rec.get("state") == "unhealthy"


def clear_episode(root_id: str, *, data_dir: Path | None = None) -> None:
    store = _load_store(data_dir=data_dir)
    episodes = store.setdefault("episodes", {})
    if root_id in episodes:
        del episodes[root_id]
        _save_store(store, data_dir=data_dir)
    clear("tick_stall", root_id, data_dir=data_dir)
    clear("tick_stall", f"{root_id}:refuse", data_dir=data_dir)


def _open_episode(
    store: dict[str, Any],
    root_id: str,
    *,
    outcome: FireAttemptOutcome | None,
    reason: str | None,
    worker_thread: str | None = None,
    now: float,
) -> None:
    episodes = store.setdefault("episodes", {})
    episodes[root_id] = {
        "state": "unhealthy",
        "fire_attempt_outcome": outcome.value if outcome else None,
        "fire_attempt_reason": reason,
        "opened_at": now,
        "last_fired_at": now,
        "worker_thread": worker_thread,
    }


def _episode_reason(
    fire_attempt_reason: str | None,
    *,
    old_decision_label: str | None = None,
) -> str:
    reason = (fire_attempt_reason or "").strip()
    if reason:
        return reason
    label = (old_decision_label or "").strip()
    if label:
        return label
    return "skip_age_exceeded"


async def observe_root_health(
    root_id: str,
    *,
    fire_attempt_outcome: FireAttemptOutcome | None,
    fire_attempt_reason: str | None = None,
    skipped_reason: str | None = None,
    consult_pending: bool = False,
    stopped_reason: str | None = None,
    admitted: bool = False,
    worker_thread: str | None = None,
    data_dir: Path | None = None,
    old_decision_label: str | None = None,
) -> HealthObservationResult:
    """Single tick observation site — predicate + episode open/re-fire."""
    if admitted or fire_attempt_outcome == FireAttemptOutcome.FIRED:
        clear_episode(root_id, data_dir=data_dir)
        from .tick_sos import clear_tick_sos

        clear_tick_sos(root_id)
        return HealthObservationResult(
            unhealthy=False,
            episode_opened=False,
            episode_refired=False,
            fire_attempt_outcome=FireAttemptOutcome.FIRED,
            fire_attempt_reason=fire_attempt_reason,
        )

    observe("tick_stall", root_id, present=True, data_dir=data_dir)
    _record_refuse_observation(
        root_id,
        fire_attempt_outcome,
        stopped_reason=stopped_reason,
        data_dir=data_dir,
    )

    unhealthy = compute_unhealthy(
        root_id,
        fire_attempt_outcome,
        consult_pending=consult_pending,
        skipped_reason=skipped_reason,
        stopped_reason=stopped_reason,
        data_dir=data_dir,
    )
    if not unhealthy:
        return HealthObservationResult(
            unhealthy=False,
            episode_opened=False,
            episode_refired=False,
            fire_attempt_outcome=fire_attempt_outcome,
            fire_attempt_reason=fire_attempt_reason,
        )

    effective_reason = _episode_reason(
        fire_attempt_reason, old_decision_label=old_decision_label
    )
    now = time.time()
    store = _load_store(data_dir=data_dir)
    rec = _episode_record(store, root_id)
    episode_opened = rec is None or rec.get("state") != "unhealthy"
    episode_refired = False
    if rec is not None and rec.get("state") == "unhealthy":
        last = float(rec.get("last_fired_at") or rec.get("opened_at") or 0.0)
        episode_refired = (now - last) >= _episode_ttl_s()
        if not episode_refired:
            return HealthObservationResult(
                unhealthy=True,
                episode_opened=False,
                episode_refired=False,
                fire_attempt_outcome=fire_attempt_outcome,
                fire_attempt_reason=fire_attempt_reason,
            )

    _open_episode(
        store,
        root_id,
        outcome=fire_attempt_outcome,
        reason=effective_reason,
        worker_thread=worker_thread,
        now=now,
    )
    _save_store(store, data_dir=data_dir)

    from .telemetry import emit_tick_escalation
    from .tick_sos import fire_episode_actuator

    await emit_tick_escalation(
        root=root_id,
        fire_attempt_outcome=(
            fire_attempt_outcome.value if fire_attempt_outcome else None
        ),
        fire_attempt_reason=effective_reason,
        worker_thread=worker_thread,
        refired=not episode_opened,
    )
    # First open only — TTL refires emit escalation telemetry but must not
    # re-page / re-dispatch CDP heal (overnight waste on unresolved stalls).
    if episode_opened:
        await fire_episode_actuator(
            root_id,
            fire_attempt_outcome=fire_attempt_outcome,
            fire_attempt_reason=effective_reason,
            worker_thread=worker_thread,
            refired=False,
        )

    return HealthObservationResult(
        unhealthy=True,
        episode_opened=episode_opened,
        episode_refired=episode_refired,
        fire_attempt_outcome=fire_attempt_outcome,
        fire_attempt_reason=effective_reason,
    )


__all__ = [
    "AdmitResult",
    "FireAttemptOutcome",
    "HealthObservationResult",
    "clear_episode",
    "compute_unhealthy",
    "episode_is_open",
    "is_declared_wait",
    "map_dispatch_failure",
    "observe_root_health",
    "root_has_demand",
]
