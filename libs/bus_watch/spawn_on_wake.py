"""Gear-3 spawn-on-wake predicate and fire leg."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from stargate_dispatch.client import submit_team_dispatch

from bus_watch.events import emit_lease_forfeited
from bus_watch.fable_lock import (
    current_night_id,
    read_lock,
    seat_lock_free,
)

_EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)
_WORK_KEY_IN_FLIGHT = "CURSOR_SOURCE_REF_IN_FLIGHT"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def spawn_fingerprint(root: dict[str, Any], lanes: list[dict[str, Any]]) -> str:
    """Spawn idempotency fingerprint — excludes ``root.turn_count`` (A5)."""
    key = [(root.get("id"), root.get("status"))]
    key += [
        (lane["id"], lane["turns"], lane["status"], lane["lifecycle"]) for lane in lanes
    ]
    return hashlib.sha256(
        json.dumps(key, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def pending_spawn_terminal(
    pending: dict[str, Any] | None,
    *,
    is_terminal: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    if not pending:
        return True
    if is_terminal is None:
        return False
    return is_terminal(pending)


def evaluate_spawn_predicate(
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    lock: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Return spawn decision with per-clause reasons."""
    policy = digest.get("policy") or {}
    max_hop_minutes = float(policy.get("max_hop_minutes") or 60)
    lock = lock if lock is not None else read_lock()
    ts = now if now is not None else time.time()
    night_id = current_night_id()
    attention = digest.get("attention") or []
    checkpoint_due = bool((digest.get("budget") or {}).get("checkpoint_due"))
    spawn_signal = bool(attention) or checkpoint_due
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
    clauses = {
        "spawn_signal": spawn_signal,
        "seat_lock_free": seat_lock_free(lock, max_hop_minutes=max_hop_minutes),
        "pending_spawn_terminal": pending_spawn_terminal(pending),
        "hops_under_cap": hops < int(policy.get("max_hops_per_night") or 8),
        "dispatches_under_cap": dispatches
        < int(policy.get("max_dispatches_per_night") or 12),
        "policy_ready": bool(policy.get("ready")),
        "fingerprint_changed": fp != last_fp,
        "grace_elapsed": (ts - last_spawn_at) > grace,
    }
    spawn = all(clauses.values())
    return {
        "spawn": spawn,
        "clauses": clauses,
        "spawn_fingerprint": fp,
        "night_id": night_id,
        "hops": hops,
        "dispatches_tonight": dispatches,
    }


def build_dispatch_body(
    root_id: str,
    policy: dict[str, Any],
    *,
    work_key: str | None = None,
) -> dict[str, Any]:
    max_hop = int(policy.get("max_hop_minutes") or 60)
    return {
        "op": "generate",
        "seat": policy.get("successor_seat") or "cursor-sdk",
        "contract": "none",
        "lane": "A",
        "model": policy.get("successor_model"),
        "packet_path": policy.get("successor_packet"),
        "dispatch_thread_id": root_id,
        "work_key": work_key or f"agent-bus:{root_id}",
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        "tags": ["liaison-successor"],
        **(
            {"cost_intent": policy["successor_cost_intent"]}
            if policy.get("successor_cost_intent")
            else {}
        ),
    }


def page_liaison(root_id: str, subject: str, body: str) -> dict[str, Any]:
    if not os.path.exists(_EMAIL_BRIDGE_SOCK):
        return {"ok": False, "reason": "pager_unavailable"}
    payload = json.dumps(
        {"subject": subject, "body": body, "tag": "liaison"},
        ensure_ascii=False,
    )
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "--unix-socket",
            _EMAIL_BRIDGE_SOCK,
            "-H",
            "Content-Type: application/json",
            "-d",
            payload,
            "http://localhost/pager/notify",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return {"ok": proc.returncode == 0, "stdout": proc.stdout[:200]}


def maybe_forfeit_expired_lease(
    root_id: str,
    *,
    lock: dict[str, Any] | None = None,
    last_holder_turn: int | None = None,
) -> bool:
    lock = lock if lock is not None else read_lock()
    if seat_lock_free(lock):
        return False
    holder = str(lock.get("holder") or "")
    if not holder:
        return False
    emit_lease_forfeited(
        root_id=root_id,
        holder=holder,
        expires_at=str(lock.get("expires_at") or ""),
        last_holder_turn=last_holder_turn,
    )
    page_liaison(
        root_id,
        f"liaison {root_id} — lease forfeit",
        f"Holder {holder} lease expired at {lock.get('expires_at')}; "
        f"last holder turn={last_holder_turn}.",
    )
    return True


def fire_spawn(
    root_id: str,
    policy: dict[str, Any],
    state: dict[str, Any],
    *,
    dry_run: bool = False,
    submit: Callable[..., tuple[dict[str, Any], int]] | None = None,
) -> dict[str, Any]:
    body = build_dispatch_body(root_id, policy)
    evaluation = evaluate_spawn_predicate(
        {"policy": policy, "attention": [], "budget": {}, "root": {}, "lanes": []},
        state,
    )
    if dry_run:
        return {"dry_run": True, "body": body, "evaluation": evaluation}
    poster = submit or submit_team_dispatch
    payload, status = poster(body)
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


def tick_spawn_on_wake(
    digest: dict[str, Any],
    state: dict[str, Any],
    root_id: str,
    *,
    dry_run: bool = False,
    submit: Callable[..., tuple[dict[str, Any], int]] | None = None,
    is_terminal: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    policy = digest.get("policy") or {}
    if not policy.get("wake_on_attention_only"):
        return {"action": "disabled"}
    lock = read_lock()
    maybe_forfeit_expired_lease(
        root_id,
        lock=lock,
        last_holder_turn=(digest.get("root") or {}).get("turns"),
    )
    evaluation = evaluate_spawn_predicate(digest, state, lock=lock)
    if pending := state.get("pending_spawn"):
        if is_terminal and is_terminal(pending):
            state.pop("pending_spawn", None)
            evaluation = evaluate_spawn_predicate(digest, state, lock=lock)
    body = build_dispatch_body(root_id, policy)
    if dry_run:
        return {
            "action": "would_spawn" if evaluation["spawn"] else "hold",
            "evaluation": evaluation,
            "body": body,
        }
    if not evaluation["spawn"]:
        return {"action": "hold", "evaluation": evaluation, "body": body}
    state["last_spawn_fingerprint"] = evaluation["spawn_fingerprint"]
    fired = fire_spawn(root_id, policy, state, dry_run=False, submit=submit)
    return {"action": "spawned", "evaluation": evaluation, "fire": fired}
