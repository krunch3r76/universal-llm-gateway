"""Spawn predicate evaluation and idempotency fingerprint."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from bus_watch.digest_budget import GEAR3_SPAWNABLE_PRESET_SUCCESSORS
from bus_watch.fable_lock import current_night_id, read_lock, seat_lock_free
from bus_watch.spawn_pending import (
    actionable_attention,
    checkpoint_due_wake,
    digest_pending_is_terminal,
    handoff_wake,
    idle_ide_forfeit,
    pending_spawn_terminal,
    remint_cap_wall,
)
from bus_watch.spawn_wake.play_classify import (
    LEFTOVER_HOLD,
    LEFTOVER_PLAY,
    classify_leftover,
    holder_lost_finished_hire,
)
from bus_watch.spawn_wake.review_apply import (
    owing_review_apply,
    ready_review_apply_attention,
)


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _holder_dispatch_id(holder: str | None) -> str | None:
    text = str(holder or "")
    if text.startswith("sdk:"):
        return text.split(":", 1)[1] or None
    return None


def spawn_fingerprint(root: dict[str, Any], lanes: list[dict[str, Any]]) -> str:
    """Spawn idempotency fingerprint — excludes ``root.turn_count`` (A5)."""
    key = [(root.get("id"), root.get("status"))]
    key += [
        (lane["id"], lane["turns"], lane["status"], lane["lifecycle"]) for lane in lanes
    ]
    return hashlib.sha256(
        json.dumps(key, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _context_budget_spawn_allowed(
    digest: dict[str, Any],
    *,
    lock: dict[str, Any],
    policy: dict[str, Any],
    now: float,
) -> tuple[bool, str | None]:
    budget = digest.get("budget") or {}
    if budget.get("stop_class") != "CONTEXT_BUDGET":
        return False, None
    max_age = float(policy.get("budget_max_age_s") or 300)
    as_of = _parse_iso_ts(str(budget.get("as_of") or ""))
    if as_of is None or (now - as_of) > max_age:
        return False, "budget_stale"
    live_epoch = _holder_dispatch_id(str(lock.get("holder") or ""))
    if live_epoch is None or str(budget.get("epoch") or "") != live_epoch:
        return False, "epoch_mismatch"
    return True, None


def _successor_model_bound(policy: dict[str, Any]) -> bool:
    """True when a successor model may spawn on gear-3 tickers.

    Operator ``--set successor_model`` always wins. Preset/default layers spawn
    only for allowlisted models (Grok 4.7 high / non-fast); premium presets
    stay blocked (10534).
    """
    model = str(policy.get("successor_model") or "")
    if not model:
        return False
    source = str(policy.get("successor_model_source") or "override")
    if source == "override":
        return True
    if model in GEAR3_SPAWNABLE_PRESET_SUCCESSORS and source in (
        "gear_preset",
        "default",
    ):
        return True
    return False


def _under_dispatch_cap(dispatches: int, policy: dict[str, Any]) -> bool:
    """True while the night's dispatch count is below an opt-in ceiling.

    The ceiling is opt-in: a non-positive or absent ``max_dispatches_per_night``
    means the house spawns without a nightly dispatch limit.
    """
    cap = int(policy.get("max_dispatches_per_night") or 0)
    return cap <= 0 or dispatches < cap


def _under_hop_cap(hops: int, policy: dict[str, Any]) -> bool:
    """True while tonight's hop count is below the ceiling, or the ceiling is off.

    Non-positive ``max_hops_per_night`` means no hop cap (operator 2026-09-20:
    work does not stop at 8). Absent/None still uses the historical default 8.
    """
    raw = policy.get("max_hops_per_night")
    if raw is None:
        return hops < 8
    cap = int(raw)
    return cap <= 0 or hops < cap


def _hops_tonight(lock: dict[str, Any], night_id: str) -> int:
    """Count hops for *this* night only.

    Falling back to ``lock.hops`` re-applied yesterday's 8 after UTC rolled
    (11667 2026-09-21T01:17Z: hops_by_night had only 2026-09-20).
    """
    by_night = lock.get("hops_by_night") or {}
    if night_id in by_night:
        return int(by_night.get(night_id) or 0)
    return 0


def compute_spawn_signal_sources(
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    lock: dict[str, Any] | None = None,
    now: float | None = None,
) -> list[str]:
    """Name each wake clause that fired for provenance on the successor paste."""
    policy = digest.get("policy") or {}
    if lock is None:
        root_id = str((digest.get("root") or {}).get("id") or "").strip()
        lock = read_lock(root_id) if root_id else {}
    ts = now if now is not None else time.time()
    attention = digest.get("attention") or []
    checkpoint_due = bool(digest.get("checkpoint_due"))
    budget_spawn, _reason = _context_budget_spawn_allowed(
        digest, lock=lock, policy=policy, now=ts
    )
    sources: list[str] = []
    if actionable_attention(attention, state=state):
        sources.append("actionable_attention")
    if ready_review_apply_attention(digest, state):
        sources.append("review_apply")
    if checkpoint_due_wake(state, checkpoint_due):
        sources.append("checkpoint_due")
    if handoff_wake(state):
        sources.append("handoff")
    if budget_spawn:
        sources.append("context_budget")
    return sources


def evaluate_spawn_predicate(
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    lock: dict[str, Any] | None = None,
    now: float | None = None,
    is_terminal: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Return spawn decision with per-clause reasons."""
    policy = digest.get("policy") or {}
    max_hop_minutes = float(policy.get("max_hop_minutes") or 60)
    if lock is None:
        root_id = str((digest.get("root") or {}).get("id") or "").strip()
        lock = read_lock(root_id) if root_id else {}
    ts = now if now is not None else time.time()
    night_id = current_night_id()
    budget = digest.get("budget") or {}
    budget_spawn, budget_reason = _context_budget_spawn_allowed(
        digest, lock=lock, policy=policy, now=ts
    )
    spawn_signal_sources = compute_spawn_signal_sources(
        digest, state, lock=lock, now=ts
    )
    spawn_signal = bool(spawn_signal_sources)
    register = str(digest.get("register") or state.get("register") or "")
    idle_forfeit = idle_ide_forfeit(lock, register=register, policy=policy)
    grace = float(policy.get("spawn_grace_seconds") or 900)
    last_spawn_at = float(state.get("last_spawn_at") or 0.0)
    last_fp = state.get("last_spawn_fingerprint")
    fp = spawn_fingerprint(digest.get("root") or {}, digest.get("lanes") or [])
    hops = _hops_tonight(lock, night_id)
    dispatches = int(
        (state.get("dispatches_tonight_by_night") or {}).get(night_id)
        or state.get("dispatches_tonight")
        or 0
    )
    pending = state.get("pending_spawn")
    checker = is_terminal or digest_pending_is_terminal(digest, now=ts)
    budget_replaces_holder = budget_spawn and _holder_dispatch_id(
        str(lock.get("holder") or "")
    ) == str(budget.get("epoch") or "")
    clauses = {
        "spawn_signal": spawn_signal,
        "seat_lock_free": seat_lock_free(
            lock,
            max_hop_minutes=max_hop_minutes,
            root_id=str((digest.get("root") or {}).get("id") or "").strip(),
        )
        or budget_replaces_holder
        or idle_forfeit is not None,
        "pending_spawn_terminal": pending_spawn_terminal(pending, is_terminal=checker),
        "hops_under_cap": _under_hop_cap(hops, policy),
        "dispatches_under_cap": _under_dispatch_cap(dispatches, policy),
        "policy_ready": bool(policy.get("ready")),
        # A preset default is not a choice: only an operator-bound successor
        # model spawns (10534 2026-09-12 — four unasked Opus liaisons).
        "successor_model_bound": _successor_model_bound(policy),
        # Attention is spawn_signal. It must not also force this clause true:
        # a forcing friction with a frozen lane fingerprint re-fired the 10479
        # mill every poll (a:35207 kept the chain moving by defeating the latch).
        "fingerprint_changed": fp != last_fp,
        "grace_elapsed": (ts - last_spawn_at) > grace,
        "remint_cap_clear": not remint_cap_wall(state, night_id),
    }
    leftover = classify_leftover(digest, state, lock=lock)
    # Play admits the same successor_model as later wakes. An unbound model
    # must not start the house as Composer omit.
    clauses["leftover_not_hold"] = leftover["leftover"] != LEFTOVER_HOLD
    # A dead hop does not change lane id/turns/status, so the idempotency
    # fingerprint stays equal to the last admit and the row never plays again.
    # A live successor flips leftover to hold and stops the re-admit.
    if leftover.get("leftover") == LEFTOVER_PLAY and holder_lost_finished_hire(
        digest, str(leftover.get("todo") or "")
    ):
        clauses["fingerprint_changed"] = True
    if owing_review_apply(digest, state):
        # Apply-all under: frozen ready, ide: check-in, or leftover HOLD must
        # not park suggestions for the operator (11960).
        clauses["policy_ready"] = True
        clauses["successor_model_bound"] = True
        clauses["leftover_not_hold"] = True
        clauses["seat_lock_free"] = True
        if "review_apply" not in spawn_signal_sources:
            spawn_signal_sources.append("review_apply")
        clauses["spawn_signal"] = True
        clauses["fingerprint_changed"] = True
    spawn = all(clauses.values())
    result: dict[str, Any] = {
        "spawn": spawn,
        "clauses": clauses,
        "spawn_fingerprint": fp,
        "spawn_signal_sources": spawn_signal_sources,
        "night_id": night_id,
        "hops": hops,
        "dispatches_tonight": dispatches,
        "leftover": leftover,
    }
    if budget.get("stop_class") == "CONTEXT_BUDGET":
        result["context_budget"] = {
            "fresh": budget_spawn,
            "reason": budget_reason,
        }
    if idle_forfeit:
        result["idle_ide_forfeit"] = idle_forfeit
    if handoff_wake(state):
        result["handoff"] = state.get("handoff")
    return result
