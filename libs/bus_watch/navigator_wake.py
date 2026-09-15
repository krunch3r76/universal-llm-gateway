"""Navigator CDP transport — clause evaluation, single-flight lease, and fire leg.

Clause 1 — navigator_single_flight (binder text, verbatim):

1a  Before emitting a navigator generate, the ticker MUST acquire an
    exclusive lease keyed ``navigator:<root>`` with TTL = wake_timeout +
    grace. If held, the ticker MUST NOT emit, and MUST record
    skip_reason=navigator_in_flight. Released on terminal dispatch state
    (done|failed|timeout) or TTL expiry, whichever is first.

1b  The navigator generate MUST carry
    work_key=navigator:<root>:<night>. A second emit under the same
    work_key is a defect, not a retry.

1c  The navigator generate MUST carry parent_thread=<root>. seat_cap
    enforces per-lane admission for bound callers; the global ceiling
    (evaluate_new_admission) remains advisory until implemented.
    1a/1b remain as defence-in-depth and are not retired.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text, path_flock
from stargate_dispatch.client import submit_team_dispatch

from bus_watch.doorbell import render_doorbell
from bus_watch.doorbell_skills import dispatch_skills_for_surface
from bus_watch.fable_lock import WATCH_DIR, current_night_id

_NAVIGATOR_LEASE_PREFIX = "navigator-"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_root_id(root_id: str) -> str:
    rid = str(root_id or "").strip()
    if not rid or "/" in rid or "\\" in rid or rid.startswith("."):
        raise ValueError(f"invalid root_id: {root_id!r}")
    return rid


def navigator_lock_path(root_id: str) -> Path:
    return WATCH_DIR / f"{_NAVIGATOR_LEASE_PREFIX}{_validate_root_id(root_id)}.lock"


def read_navigator_lock(root_id: str) -> dict[str, Any]:
    path = navigator_lock_path(root_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["age_s"] = round(time.time() - path.stat().st_mtime, 1)
    except (OSError, ValueError):
        return {}
    return data


def _lease_expired(lock: dict[str, Any]) -> bool:
    raw = lock.get("expires_at_ts")
    if raw is None:
        return True
    try:
        return time.time() >= float(raw)
    except (TypeError, ValueError):
        return True


def navigator_single_flight_held(root_id: str) -> bool:
    """True when a live navigator lease blocks another emit."""
    lock = read_navigator_lock(root_id)
    if not lock.get("holder"):
        return False
    return not _lease_expired(lock)


def acquire_navigator_lease(
    root_id: str,
    *,
    ttl_seconds: float,
    holder: str = "liaison-ticker",
    work_key: str | None = None,
    execution_id: str | None = None,
) -> dict[str, Any]:
    """Atomically claim ``navigator:<root>`` for ``ttl_seconds``.

    Cross-process serialisation uses ``fcntl.flock`` on a sibling lockfile
    (``durable_io.atomic.path_flock``) so concurrent tickers cannot both pass
    the read-then-write window (a:33951).
    """
    rid = _validate_root_id(root_id)
    path = navigator_lock_path(rid)
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    with path_flock(path):
        current = read_navigator_lock(rid)
        if current.get("holder") and not _lease_expired(current):
            return {"ok": False, "reason": "navigator_in_flight", "lock": current}
        expires_at_ts = time.time() + ttl_seconds
        payload = {
            "holder": holder,
            "holder_pid": os.getpid(),
            "claimed_at": _utcnow(),
            "expires_at_ts": expires_at_ts,
            "root": rid,
            "work_key": work_key,
            "execution_id": execution_id,
        }
        durable_write_text(path, json.dumps(payload, indent=2), already_locked=True)
    return {"ok": True, "lock": payload}


def release_navigator_lease(
    root_id: str, *, holder: str | None = None
) -> dict[str, Any]:
    """Drop the navigator lease when dispatch reaches terminal state."""
    rid = _validate_root_id(root_id)
    path = navigator_lock_path(rid)
    with path_flock(path):
        current = read_navigator_lock(rid)
        if holder and current.get("holder") not in (holder, None):
            return {"ok": False, "reason": "not_holder", "lock": current}
        if current:
            durable_write_text(
                path,
                json.dumps({"holder": None, "released_at": _utcnow(), "root": rid}),
                already_locked=True,
            )
    return {"ok": True, "lock": read_navigator_lock(rid)}


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _navigator_model_bound(policy: dict[str, Any]) -> bool:
    """True when a navigator model is set and bound by operator override."""
    if not policy.get("navigator_model"):
        return False
    return policy.get("navigator_model_source", "override") == "override"


def _commissions_tonight(state: dict[str, Any], night_id: str) -> int:
    by_night = state.get("navigator_commissions_by_night") or {}
    return int(
        by_night.get(night_id) or state.get("navigator_commissions_tonight") or 0
    )


def _under_commission_cap(count: int, policy: dict[str, Any]) -> bool:
    """Fail-closed nightly commission ceiling — 0 means ZERO, not unlimited.

    Deliberate inverse of ``spawn_wake.predicate._under_dispatch_cap`` where
    ``cap <= 0`` means no limit; here ``cap <= 0`` refuses all commissions.
    """
    cap = int(policy.get("max_navigator_commissions_per_night") or 0)
    return count < cap


_ATTENTION_EXCLUDED_KINDS = ("budget_estimate", "friction")

# The producing expression for the doorbell's digest echo, in the digest's own
# dotted-path convention (cf. `state.dispatches_tonight_by_night`,
# `policy.max_hops_per_night`). `source` must name WHERE the value came from;
# the fingerprint belongs in `epoch`. Passing the fingerprint for both made the
# quintuple carry four independent fields while presenting as five.
DIGEST_SOURCE_EXPR = "digest.attention[].id excl:" + ",".join(_ATTENTION_EXCLUDED_KINDS)


def _attention_row_ids(digest: dict[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for row in digest.get("attention") or []:
        if row.get("kind") in _ATTENTION_EXCLUDED_KINDS:
            continue
        rid = row.get("id")
        if rid is not None:
            ids.append(str(rid))
    return tuple(ids)


def _scope_lanes(digest: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(lane.get("id")) for lane in (digest.get("lanes") or []) if lane.get("id")
    )


def _wake_timeout_seconds(policy: dict[str, Any]) -> float:
    max_hop = float(policy.get("max_hop_minutes") or 60)
    return float(policy.get("navigator_wake_timeout_seconds") or (max_hop * 60 + 1800))


def render_navigator_doorbell(
    digest: dict[str, Any],
    *,
    root_id: str,
    include_commission: bool,
) -> str:
    """Render the navigator doorbell with the status quintuple when digest is in hand."""
    root = digest.get("root") or {}
    policy = digest.get("policy") or {}
    slug = str(
        root.get("slug") or policy.get("navigator_slug") or "claude-ai-navigator-seat"
    )
    ring = policy.get("wake_ring")
    extras = tuple(policy.get("navigator_extra_addresses") or ())
    fired_by = policy.get("navigator_fired_by") or "cdp generate via liaison-ticker"
    fp = str(digest.get("fingerprint") or "")
    return render_doorbell(
        root_id,
        slug,
        ring=str(ring) if ring else None,
        surface="cdp",
        extra_addresses=extras,
        fired_by=str(fired_by),
        include_commission=include_commission,
        attention_row_ids=_attention_row_ids(digest),
        as_of=str(digest.get("ts") or ""),
        digest_source=DIGEST_SOURCE_EXPR,
        scope_lanes=_scope_lanes(digest),
        fingerprint=fp,
    )


def evaluate_navigator_wake(
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    register: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Return navigator decision with per-clause reasons."""
    policy = digest.get("policy") or {}
    root = digest.get("root") or {}
    root_id = str(root.get("id") or "").strip()
    ts = now if now is not None else time.time()
    night_id = str(digest.get("night_id") or current_night_id())
    grace = float(policy.get("navigator_grace_seconds") or 900)
    last_at = float(state.get("last_induction_at") or 0.0)
    commissions = _commissions_tonight(state, night_id)
    include_commission = _under_commission_cap(commissions, policy)
    clauses = {
        "navigator_single_flight": not navigator_single_flight_held(root_id),
        # ``already_fired`` (same digest fingerprint) is NOT an anti-loop clause —
        # digest_fingerprint folds lane.turns and fold_fingerprint folds friction
        # rows, so the navigator's own activity moves the fingerprint and re-arms
        # ``fingerprint_changed`` on the next tick.
        "navigator_grace_elapsed": (ts - last_at) > grace,
        # Fail-closed on the commission line, not on the wake: at cap 0 the doorbell
        # omits ``commission:`` entirely while this clause stays True.
        "navigator_commission_cap": True,
        "navigator_model_bound": _navigator_model_bound(policy),
        "navigator_lane_bound": bool(root_id),
        "register_not_attended": register != "attended",
    }
    fire = all(clauses.values())
    skip_reason: str | None = None
    if not clauses["navigator_single_flight"]:
        skip_reason = "navigator_in_flight"
    elif not clauses["navigator_grace_elapsed"]:
        skip_reason = "grace_not_elapsed"
    elif not clauses["navigator_model_bound"]:
        skip_reason = "navigator_model_unbound"
    elif not clauses["navigator_lane_bound"]:
        skip_reason = "navigator_lane_unbound"
    elif not clauses["register_not_attended"]:
        skip_reason = "register_attended"
    doorbell = render_navigator_doorbell(
        digest, root_id=root_id, include_commission=include_commission
    )
    return {
        "fire": fire,
        "clauses": clauses,
        "skip_reason": skip_reason,
        "night_id": night_id,
        "work_key": f"navigator:{root_id}:{night_id}",
        "include_commission": include_commission,
        "commissions_tonight": commissions,
        "doorbell": doorbell,
        "doorbell_bytes": len(doorbell.encode("utf-8")),
    }


def fire_navigator_wake(
    root_id: str,
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    register: str,
    dry_run: bool = False,
    submit: Callable[..., tuple[dict[str, Any], int]] | None = None,
) -> dict[str, Any]:
    """Evaluate clauses, acquire lease, and POST one navigator generate."""
    evaluation = evaluate_navigator_wake(digest, state, register=register)
    policy = digest.get("policy") or {}
    night_id = evaluation["night_id"]
    work_key = evaluation["work_key"]
    doorbell = evaluation["doorbell"]
    wake_timeout = _wake_timeout_seconds(policy)
    grace = float(policy.get("navigator_grace_seconds") or 900)
    ttl = wake_timeout + grace
    body = {
        "op": "generate",
        "seat": "cdp",
        "contract": "none",
        "model": policy.get("navigator_model"),
        "prompt": doorbell,
        "dispatch_thread_id": root_id,
        "parent_thread": root_id,
        "work_key": work_key,
        "timeout_seconds": int(wake_timeout),
        "caller_agent": "liaison-ticker",
    }
    staged_skills = dispatch_skills_for_surface("cdp")
    if staged_skills:
        body["skills"] = staged_skills
    evaluation["clauses"]["navigator_lane_bound"] = bool(body.get("parent_thread"))
    if not evaluation["clauses"]["navigator_lane_bound"]:
        return {
            "ok": False,
            "refused": "navigator_lane_unbound",
            "evaluation": evaluation,
        }
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "would_fire": evaluation["fire"],
            "transport": "navigator",
            "evaluation": evaluation,
            "body": body,
            "doorbell": doorbell,
            "refused": evaluation.get("skip_reason"),
        }
    if not evaluation["fire"]:
        return {
            "ok": False,
            "refused": evaluation["skip_reason"],
            "evaluation": evaluation,
        }
    lease = acquire_navigator_lease(root_id, ttl_seconds=ttl, work_key=work_key)
    if not lease.get("ok"):
        return {
            "ok": False,
            "refused": lease.get("reason"),
            "evaluation": evaluation,
        }
    poster = submit or submit_team_dispatch
    payload, status = poster(body)
    result: dict[str, Any] = {
        "ok": 0 < status < 400,
        "status_code": status,
        "payload": payload,
        "transport": "navigator",
        "evaluation": evaluation,
        "body": body,
    }
    if result["ok"]:
        execution_id = str(payload.get("execution_id") or "").strip()
        lock_path = navigator_lock_path(root_id)
        with path_flock(lock_path):
            lock = read_navigator_lock(root_id)
            lock["execution_id"] = execution_id
            durable_write_text(
                lock_path, json.dumps(lock, indent=2), already_locked=True
            )
        if evaluation["include_commission"]:
            by_night = dict(state.get("navigator_commissions_by_night") or {})
            count = int(by_night.get(night_id) or 0) + 1
            by_night[night_id] = count
            state["navigator_commissions_by_night"] = by_night
            state["navigator_commissions_tonight"] = count
        state["last_induction_at"] = time.time()
    else:
        release_navigator_lease(root_id)
    return result


__all__ = [
    "DIGEST_SOURCE_EXPR",
    "acquire_navigator_lease",
    "evaluate_navigator_wake",
    "fire_navigator_wake",
    "navigator_lock_path",
    "navigator_single_flight_held",
    "read_navigator_lock",
    "release_navigator_lease",
    "render_navigator_doorbell",
]
