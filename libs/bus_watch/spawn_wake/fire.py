"""Spawn fire leg and gear-3 tick orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from stargate_dispatch.client import submit_team_dispatch

from bus_watch.events import emit_pending_spawn_released
from bus_watch.fable_lock import (
    current_night_id,
    read_lock,
    release_fable_lock,
)
from bus_watch.liaison_pager import maybe_forfeit_expired_lease, page_liaison
from bus_watch.spawn_pending import (
    dead_sdk_holder,
    digest_pending_is_terminal,
    record_remint_cap,
    record_spawn_service,
)
from bus_watch.spawn_wake.packet import (
    _wire_submit_body,
    build_dispatch_body,
    successor_context_from_digest,
)
from bus_watch.spawn_wake.play_classify import (
    LEFTOVER_HOLD,
    LEFTOVER_PLAY,
    PLAY_HOLD,
    build_play_dispatch_body,
    classify_leftover,
)
from bus_watch.spawn_wake.predicate import evaluate_spawn_predicate
from bus_watch.spawn_wake.row_bind import body_for_sit_friction
from bus_watch.spawn_wake.row_class import (
    ROW_CLASS_FIRED,
    ROW_CLASS_HOLD,
    mark_row_class_fired,
    sit_forcing_friction,
)

_WORK_KEY_IN_FLIGHT = "CURSOR_SOURCE_REF_IN_FLIGHT"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def body_for_leftover(
    root_id: str,
    policy: dict[str, Any],
    leftover: dict[str, Any],
    *,
    successor_context: dict[str, Any] | None = None,
    digest: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Hold mints nothing. Play rematerializes the todo. Sit on a forcing friction
    score row binds CDP ROW_CLASS; otherwise sit is today's house generate."""
    if leftover.get("leftover") == LEFTOVER_HOLD:
        return None
    todo = leftover.get("todo")
    if leftover.get("leftover") == LEFTOVER_PLAY and todo:
        return build_play_dispatch_body(root_id, policy, todo_slug=str(todo))
    if digest is not None and state is not None:
        friction = sit_forcing_friction(digest, leftover)
        if friction:
            return body_for_sit_friction(
                root_id, policy, friction, state, digest
            )
    return build_dispatch_body(root_id, policy, successor_context=successor_context)


def fire_spawn(
    root_id: str,
    policy: dict[str, Any],
    state: dict[str, Any],
    *,
    dry_run: bool = False,
    submit: Callable[..., tuple[dict[str, Any], int]] | None = None,
    successor_context: dict[str, Any] | None = None,
    digest: dict[str, Any] | None = None,
    leftover: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST one successor generate and record pending_spawn plus tonight's counters."""
    snap = digest or {
        "policy": policy,
        "attention": [],
        "budget": {},
        "root": {"id": root_id},
        "lanes": [],
    }
    verdict = leftover or classify_leftover(snap, state)
    body = body_for_leftover(
        root_id,
        policy,
        verdict,
        successor_context=successor_context,
        digest=snap,
        state=state,
    )
    if verdict.get("leftover") == LEFTOVER_HOLD:
        return {
            "status_code": 0,
            "refused": PLAY_HOLD,
            "leftover": verdict,
            "body": None,
        }
    if body and body.get("_row_class_hold"):
        return {
            "status_code": 0,
            "refused": ROW_CLASS_HOLD,
            "leftover": verdict,
            "body": None,
        }
    if body and body.get("_row_class_fired"):
        return {
            "status_code": 0,
            "refused": ROW_CLASS_FIRED,
            "leftover": verdict,
            "body": None,
        }
    if (
        body is not None
        and not body.get("model")
        and verdict.get("leftover") != LEFTOVER_PLAY
        and not body.get("_row_bind")
        and body.get("contract") != "implement"
    ):
        # Sit/house generate: never let the wire pick a model.
        return {
            "status_code": 0,
            "refused": "successor_model_unset",
            "body": body,
            "leftover": verdict,
        }
    evaluation = evaluate_spawn_predicate(
        {"policy": policy, "attention": [], "budget": {}, "root": {}, "lanes": []},
        state,
    )
    if dry_run:
        return {
            "dry_run": True,
            "body": body,
            "evaluation": evaluation,
            "leftover": verdict,
        }
    poster = submit or submit_team_dispatch
    payload, status = poster(_wire_submit_body(body or {}))
    night_id = current_night_id()
    result: dict[str, Any] = {
        "status_code": status,
        "payload": payload,
        "body": body,
        "leftover": verdict,
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
            reason = getattr(checker, "last_reason", None) or "terminal"
            emit_pending_spawn_released(
                root_id=root_id,
                execution_id=finished_execution_id,
                thread_id=str(pending.get("thread_id") or ""),
                reason=str(reason),
                spawned_at=str(pending.get("spawned_at") or "") or None,
            )
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
    successor_context = successor_context_from_digest(
        digest,
        spawn_signal_sources=evaluation.get("spawn_signal_sources"),
    )
    leftover = evaluation.get("leftover") or classify_leftover(
        digest, state, lock=lock
    )
    body = body_for_leftover(
        root_id,
        policy,
        leftover,
        successor_context=successor_context,
        digest=digest,
        state=state,
    )
    if leftover.get("leftover") == LEFTOVER_HOLD:
        return {
            "action": "hold",
            "evaluation": evaluation,
            "refused": PLAY_HOLD,
            "body": None,
        }
    if body and body.get("_row_class_hold"):
        return {
            "action": "hold",
            "evaluation": evaluation,
            "refused": ROW_CLASS_HOLD,
            "body": None,
        }
    if body and body.get("_row_class_fired"):
        return {
            "action": "hold",
            "evaluation": evaluation,
            "refused": ROW_CLASS_FIRED,
            "body": None,
        }
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
        digest=digest,
        leftover=leftover,
    )
    status = int(fired.get("status_code") or 0)
    if 0 < status < 400:
        if digest.get("checkpoint_due"):
            state["checkpoint_due_spawned_tick"] = int(state.get("last_cp_tick") or 0)
        record_spawn_service(state, digest.get("attention"), root_id=root_id)
        fired_fid = str((body or {}).get("_friction_id") or "")
        if fired_fid and (body or {}).get("_row_class"):
            mark_row_class_fired(state, fired_fid)
    return {"action": "spawned", "evaluation": evaluation, "fire": fired}
