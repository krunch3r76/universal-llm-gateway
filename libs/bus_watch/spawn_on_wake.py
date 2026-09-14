"""Gear-3 spawn-on-wake predicate and fire leg."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from stargate_dispatch.client import submit_team_dispatch

from bus_watch.doorbell import render_successor_wake
from bus_watch.fable_lock import (
    current_night_id,
    read_lock,
    release_fable_lock,
    seat_lock_free,
)
from bus_watch.friction_rows import now_row as friction_now_row
from bus_watch.liaison_pager import maybe_forfeit_expired_lease, page_liaison
from bus_watch.spawn_pending import (
    actionable_attention,
    checkpoint_due_wake,
    dead_sdk_holder,
    digest_pending_is_terminal,
    handoff_wake,
    idle_ide_forfeit,
    pending_spawn_terminal,
    record_remint_cap,
    record_spawn_service,
    remint_cap_wall,
)

_WORK_KEY_IN_FLIGHT = "CURSOR_SOURCE_REF_IN_FLIGHT"
SUCCESSOR_MESSAGE_CAP = 2048
_CONTEXT_BUDGET_RATIO = 0.80


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def build_successor_message(
    root_id: str,
    *,
    gear: str,
    row: str,
    tip_cp_ordinal: int | None = None,
    ring: str | None = None,
    extra_addresses: tuple[str, ...] = (),
    cap: int = SUCCESSOR_MESSAGE_CAP,
) -> str:
    """Inline resume-fence pull recipe for a headless liaison successor."""
    return render_successor_wake(
        root_id,
        gear=gear,
        row=row,
        tip_cp_ordinal=tip_cp_ordinal,
        ring=ring,
        extra_addresses=tuple(extra_addresses),
        cap=cap,
    )


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
    """True when a successor model is set and, if its layer is stamped, bound by
    an operator override rather than inherited from a gear preset."""
    if not policy.get("successor_model"):
        return False
    return policy.get("successor_model_source", "override") == "override"


def _under_dispatch_cap(dispatches: int, policy: dict[str, Any]) -> bool:
    """True while the night's dispatch count is below an opt-in ceiling.

    The ceiling is opt-in: a non-positive or absent ``max_dispatches_per_night``
    means the house spawns without a nightly dispatch limit.
    """
    cap = int(policy.get("max_dispatches_per_night") or 0)
    return cap <= 0 or dispatches < cap


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
    attention = digest.get("attention") or []
    checkpoint_due = bool(digest.get("checkpoint_due"))
    budget = digest.get("budget") or {}
    budget_spawn, budget_reason = _context_budget_spawn_allowed(
        digest, lock=lock, policy=policy, now=ts
    )
    spawn_signal = (
        bool(actionable_attention(attention, state=state))
        or checkpoint_due_wake(state, checkpoint_due)
        or handoff_wake(state)
        or budget_spawn
    )
    register = str(digest.get("register") or state.get("register") or "")
    idle_forfeit = idle_ide_forfeit(lock, register=register, policy=policy)
    grace = float(policy.get("spawn_grace_seconds") or 900)
    last_spawn_at = float(state.get("last_spawn_at") or 0.0)
    last_fp = state.get("last_spawn_fingerprint")
    fp = spawn_fingerprint(digest.get("root") or {}, digest.get("lanes") or [])
    hops = int((lock.get("hops_by_night") or {}).get(night_id) or lock.get("hops") or 0)
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
        "hops_under_cap": hops < int(policy.get("max_hops_per_night") or 8),
        "dispatches_under_cap": _under_dispatch_cap(dispatches, policy),
        "policy_ready": bool(policy.get("ready")),
        # A preset default is not a choice: only an operator-bound successor
        # model spawns (10534 2026-09-12 — four unasked Opus liaisons).
        "successor_model_bound": _successor_model_bound(policy),
        "fingerprint_changed": fp != last_fp,
        "grace_elapsed": (ts - last_spawn_at) > grace,
        "remint_cap_clear": not remint_cap_wall(state, night_id),
    }
    spawn = all(clauses.values())
    result: dict[str, Any] = {
        "spawn": spawn,
        "clauses": clauses,
        "spawn_fingerprint": fp,
        "night_id": night_id,
        "hops": hops,
        "dispatches_tonight": dispatches,
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


def build_dispatch_body(
    root_id: str,
    policy: dict[str, Any],
    *,
    work_key: str | None = None,
    successor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the generate payload: successor model, night work_key, resume message."""
    max_hop = int(policy.get("max_hop_minutes") or 60)
    ctx = dict(successor_context or {})
    extras = ctx.get("extra_addresses") or policy.get("successor_extra_addresses") or ()
    message = build_successor_message(
        root_id,
        gear=str(ctx.get("gear") or policy.get("gear") or "1-fable-mvp"),
        row=str(ctx.get("row") or ""),
        tip_cp_ordinal=ctx.get("tip_cp_ordinal"),
        ring=ctx.get("ring") or policy.get("wake_ring"),
        extra_addresses=tuple(extras),
    )
    body: dict[str, Any] = {
        "op": "generate",
        "seat": policy.get("successor_seat") or "cursor-sdk",
        "contract": "none",
        "lane": "A",
        "model": policy.get("successor_model"),
        "message": message,
        "dispatch_thread_id": root_id,
        # Per-night work identity: GIW's remint cap counts admits per work_key, so a
        # root-wide key runs out after one night (a:33139 — hop 9 refused at seq 9 >
        # cap 8). Keying by night_id resets the sequence with the night, not by hand.
        "work_key": work_key or f"agent-bus:{root_id}:night-{current_night_id()}",
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        **(
            {"cost_intent": policy["successor_cost_intent"]}
            if policy.get("successor_cost_intent")
            else {}
        ),
    }
    return body


def _wire_submit_body(body: dict[str, Any]) -> dict[str, Any]:
    """Map local ``message`` to Stargate ``prompt``; drop generate-forbidden tags."""
    wired = dict(body)
    if wired.get("message") and not wired.get("prompt"):
        wired["prompt"] = wired.pop("message")
    wired.pop("tags", None)
    return wired


def fire_spawn(
    root_id: str,
    policy: dict[str, Any],
    state: dict[str, Any],
    *,
    dry_run: bool = False,
    submit: Callable[..., tuple[dict[str, Any], int]] | None = None,
    successor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST one successor generate and record pending_spawn plus tonight's counters."""
    body = build_dispatch_body(root_id, policy, successor_context=successor_context)
    if not body.get("model"):
        # Never let the wire pick a model: an unset successor is a hold, not a default.
        return {"status_code": 0, "refused": "successor_model_unset", "body": body}
    evaluation = evaluate_spawn_predicate(
        {"policy": policy, "attention": [], "budget": {}, "root": {}, "lanes": []},
        state,
    )
    if dry_run:
        return {"dry_run": True, "body": body, "evaluation": evaluation}
    poster = submit or submit_team_dispatch
    payload, status = poster(_wire_submit_body(body))
    night_id = current_night_id()
    result: dict[str, Any] = {
        "status_code": status,
        "payload": payload,
        "body": body,
    }
    if status >= 400:
        err = payload.get("error") or {}
        code = err.get("code") if isinstance(err, dict) else None
        if code == _WORK_KEY_IN_FLIGHT or status == 409:
            result["quiet_refusal"] = True
        if wall := record_remint_cap(state, payload, night_id=night_id, at=_utcnow()):
            # A designed stop, not a transient: hold for the night and page once.
            result["remint_cap_wall"] = wall
            page_liaison(
                root_id,
                f"liaison {root_id} — REMINT_CAP wall {night_id}",
                f"GIW refused the night's work_key: {wall['message']} "
                "Ticker holds until the night rolls.",
            )
        return result
    execution_id = str(payload.get("execution_id") or "").strip()
    thread_id = str(payload.get("thread_id") or payload.get("thread") or "").strip()
    state["pending_spawn"] = {
        "execution_id": execution_id,
        "thread_id": thread_id,
        "spawned_at": _utcnow(),
    }
    by_night = dict(state.get("dispatches_tonight_by_night") or {})
    count = int(by_night.get(night_id) or state.get("dispatches_tonight") or 0) + 1
    by_night[night_id] = count
    state["dispatches_tonight_by_night"] = by_night
    state["dispatches_tonight"] = count
    state["last_spawn_at"] = time.time()
    paged_nights = set(state.get("spawn_paged_nights") or [])
    if night_id not in paged_nights:
        page_liaison(
            root_id,
            f"liaison {root_id} — first spawn {night_id}",
            f"Spawned successor execution_id={execution_id} thread={thread_id}.",
        )
        paged_nights.add(night_id)
        state["spawn_paged_nights"] = sorted(paged_nights)
    return result


def successor_context_from_digest(digest: dict[str, Any]) -> dict[str, Any]:
    """Extract message bind fields from a digest snapshot; with no seat-bound
    row, a forcing friction is the row the successor is spawned for."""
    policy = digest.get("policy") or {}
    root = digest.get("root") or {}
    return {
        "gear": policy.get("gear"),
        # Same precedence as the induction NOW: seat bind, then policy bind,
        # then the newest undispositioned friction.
        "row": digest.get("summary_row")
        or str(policy.get("now_row") or "").strip()
        or friction_now_row(digest)
        or root.get("last_subject")
        or "",
        "tip_cp_ordinal": root.get("turns"),
        "ring": policy.get("wake_ring"),
    }


def tick_spawn_on_wake(
    digest: dict[str, Any],
    state: dict[str, Any],
    root_id: str,
    *,
    dry_run: bool = False,
    submit: Callable[..., tuple[dict[str, Any], int]] | None = None,
    is_terminal: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """One gear-3 poll: drop a finished pending mutex, then spawn or hold.

    Default ``is_terminal`` reads digest lanes so ``liaison-tick --loop`` cannot
    forget the callback (10534 held four hours after 10579 completed). Mutates
    ``state`` (pops pending, records checkpoint-due latch and spawn counters).
    """
    policy = digest.get("policy") or {}
    if not policy.get("wake_on_attention_only"):
        return {"action": "disabled"}
    lock = read_lock(root_id)
    maybe_forfeit_expired_lease(
        root_id,
        lock=lock,
        last_holder_turn=(digest.get("root") or {}).get("turns"),
    )
    checker = is_terminal or digest_pending_is_terminal(digest)
    finished_execution_id = None
    if pending := state.get("pending_spawn"):
        if checker(pending):
            finished_execution_id = str(pending.get("execution_id") or "")
            state.pop("pending_spawn", None)
    reaped = dead_sdk_holder(
        lock,
        finished_execution_id=finished_execution_id,
        rows=[*(digest.get("lanes") or []), *(digest.get("attention") or [])],
    )
    if reaped:
        release_fable_lock(reaped, pid=None, root_id=root_id)
        lock = read_lock(root_id)
    evaluation = evaluate_spawn_predicate(digest, state, lock=lock, is_terminal=checker)
    if reaped:
        evaluation["reaped_sdk_holder"] = reaped
    successor_context = successor_context_from_digest(digest)
    body = build_dispatch_body(root_id, policy, successor_context=successor_context)
    if dry_run:
        return {
            "action": "would_spawn" if evaluation["spawn"] else "hold",
            "evaluation": evaluation,
            "body": body,
        }
    if not evaluation["spawn"]:
        return {"action": "hold", "evaluation": evaluation, "body": body}
    if forfeit := evaluation.get("idle_ide_forfeit"):
        # The stopped tab's lease goes before the successor is minted, so the
        # successor's ``--claim`` is not refused by a seat nobody is sitting in.
        release_fable_lock(forfeit["holder"], pid=None, root_id=root_id)
    state["last_spawn_fingerprint"] = evaluation["spawn_fingerprint"]
    fired = fire_spawn(
        root_id,
        policy,
        state,
        dry_run=False,
        submit=submit,
        successor_context=successor_context,
    )
    status = int(fired.get("status_code") or 0)
    if 0 < status < 400:
        if digest.get("checkpoint_due"):
            state["checkpoint_due_spawned_tick"] = int(state.get("last_cp_tick") or 0)
        record_spawn_service(state, digest.get("attention"))
    return {"action": "spawned", "evaluation": evaluation, "fire": fired}
