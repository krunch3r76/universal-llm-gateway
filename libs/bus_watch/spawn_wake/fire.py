"""Spawn fire leg and gear-3 tick orchestration."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from stargate_dispatch.client import submit_team_dispatch

from bus_watch.events import emit_pending_spawn_released
from bus_watch.fable_lock import (
    current_night_id,
    read_lock,
    release_fable_lock,
    seat_lock_free,
)
from bus_watch.liaison_pager import maybe_forfeit_expired_lease, page_liaison
from bus_watch.now_row import resolve_now_row
from bus_watch.spawn_pending import (
    dead_sdk_holder,
    digest_pending_is_terminal,
    record_remint_cap,
    record_spawn_service,
    row_is_terminal,
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
    extract_todo_slug,
)
from bus_watch.spawn_wake.predicate import evaluate_spawn_predicate
from bus_watch.spawn_wake.review_apply import (
    mark_review_apply_fired,
    maybe_review_apply_body,
)
from bus_watch.spawn_wake.row_bind import body_for_sit_friction
from bus_watch.spawn_wake.row_class import (
    ROW_CLASS_FIRED,
    ROW_CLASS_HOLD,
    mark_row_class_fired,
    sit_forcing_friction,
)

_WORK_KEY_IN_FLIGHT = "CURSOR_SOURCE_REF_IN_FLIGHT"
_WORK_KEY_UNPARSEABLE = "work_key_unparseable"
LIVE_DISPATCH_HOLD = "live_dispatch_hold"
_FRICTION_NOW_RE = re.compile(r"Friction\s+a:(\d+)", re.I)


def friction_gate_id_from_now_row(raw: str) -> str | None:
    """Return ``a:<digits>`` when ``raw`` is a friction NOW gate line, else None."""
    match = _FRICTION_NOW_RE.search(str(raw or ""))
    if not match:
        return None
    return f"a:{match.group(1)}"


def _lane_todo_slugs(lane: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for blob in (
        lane.get("work_key"),
        lane.get("source_ref"),
        lane.get("last_subject"),
        lane.get("slug"),
    ):
        slug = extract_todo_slug(blob)
        if slug:
            found.add(slug)
        elif blob and str(blob).lower().startswith("todo:"):
            found.add(str(blob).split(":", 1)[1].lower())
    return found


def maybe_steer_friction_gate(
    digest: dict[str, Any],
    state: dict[str, Any],
    leftover: dict[str, Any],
    *,
    submit: Callable[..., tuple[dict[str, Any], int]] | None = None,
) -> dict[str, Any] | None:
    """Inject one CHECKPOINT steer when HOLD is gated by friction NOW on the same todo.

    Latches ``state['gate_steers_sent'][a:<id>]`` so the next tick does not re-inject.
    Returns steer metadata when a POST was attempted; None when not applicable.
    """
    if leftover.get("leftover") != LEFTOVER_HOLD:
        return None
    todo = leftover.get("todo")
    if not todo:
        return None
    owner = leftover.get("owner")
    if not isinstance(owner, dict):
        return None
    lane = owner.get("lane")
    if not isinstance(lane, dict):
        return None
    if str(todo).lower() not in _lane_todo_slugs(lane):
        return None
    raw_now, _source = resolve_now_row(digest)
    friction_id = friction_gate_id_from_now_row(raw_now)
    roster = digest.get("roster") or []
    if roster:
        todo_slug = str(todo or "").lower()
        gated = [
            row
            for row in roster
            if str(row.get("gate") or "") == friction_id
            and str(row.get("work_key") or "").lower() == f"todo:{todo_slug}"
        ]
        if friction_id and not gated:
            return None
    if not friction_id:
        return None
    sent = state.get("gate_steers_sent")
    if not isinstance(sent, dict):
        sent = {}
    if sent.get(friction_id):
        return {"steered": False, "friction_id": friction_id, "reason": "latched"}
    dispatch_id = str(lane.get("dispatch_id") or "").strip()
    if not dispatch_id:
        return {
            "steered": False,
            "friction_id": friction_id,
            "reason": "no_dispatch_id",
        }
    body = {
        "op": "steer",
        "seat": "cursor-sdk",
        "steer": "inject",
        "dispatch_id": dispatch_id,
        "reason": "friction-gate",
        "directive": f"CHECKPOINT. Stop this row. Gate is {friction_id}.",
        "caller_agent": "liaison-ticker",
    }
    poster = submit or submit_team_dispatch
    _payload, status = poster(body)
    sent = dict(sent)
    sent[friction_id] = _utcnow()
    state["gate_steers_sent"] = sent
    return {
        "steered": True,
        "friction_id": friction_id,
        "dispatch_id": dispatch_id,
        "status_code": status,
    }


def _holder_ident(lock: dict[str, Any]) -> str | None:
    holder = str(lock.get("holder") or "")
    if not holder.startswith("sdk:"):
        return None
    parts = holder.split(":", 1)
    return parts[1] if len(parts) > 1 else None


def _lane_for_live_holder(digest: dict[str, Any], ident: str) -> dict[str, Any] | None:
    for lane in digest.get("lanes") or []:
        if not isinstance(lane, dict) or row_is_terminal(lane):
            continue
        dispatch_id = str(lane.get("dispatch_id") or "")
        if dispatch_id.startswith(ident) or ident in str(
            lane.get("last_subject") or ""
        ):
            return lane
    return None


def maybe_steer_live_dispatch(
    digest: dict[str, Any],
    state: dict[str, Any],
    lock: dict[str, Any],
    evaluation: dict[str, Any],
    body: dict[str, Any] | None,
    policy: dict[str, Any],
    *,
    submit: Callable[..., tuple[dict[str, Any], int]] | None = None,
) -> dict[str, Any] | None:
    """Hold spawn and steer a live sdk holder on context-budget hop.

    Returns steer metadata when the fire predicate holds (including latch and
    no-match holds); None when spawn should proceed normally.
    """
    ident = _holder_ident(lock)
    if not ident:
        return None
    max_hop_minutes = float(policy.get("max_hop_minutes") or 60)
    root = str((digest.get("root") or {}).get("id") or "").strip()
    if seat_lock_free(lock, max_hop_minutes=max_hop_minutes, root_id=root):
        return None
    if evaluation.get("reaped_sdk_holder"):
        return None
    if (body or {}).get("_review_apply"):
        return None

    lane = _lane_for_live_holder(digest, ident)
    dispatch_id = str((lane or {}).get("dispatch_id") or "").strip()
    if not lane or not dispatch_id:
        return {"steered": False, "reason": "no_dispatch_id"}

    sent = state.get("live_dispatch_steers_sent")
    if not isinstance(sent, dict):
        sent = {}
    if sent.get(dispatch_id):
        return {
            "steered": False,
            "dispatch_id": dispatch_id,
            "reason": "latched",
        }

    steer_body = {
        "op": "steer",
        "seat": "cursor-sdk",
        "steer": "inject",
        "dispatch_id": dispatch_id,
        "reason": "context-budget-hop",
        "directive": (
            f"CHECKPOINT. Context budget reached for dispatch {dispatch_id}. "
            "Hop under the context policy. Do not start a parallel liaison."
        ),
        "caller_agent": "liaison-ticker",
    }
    poster = submit or submit_team_dispatch
    _payload, status = poster(steer_body)
    sent = dict(sent)
    sent[dispatch_id] = _utcnow()
    state["live_dispatch_steers_sent"] = dict(list(sent.items())[-32:])
    return {
        "steered": True,
        "dispatch_id": dispatch_id,
        "status_code": status,
    }


def _release_stop_claim(stop_id: str, body: dict[str, Any] | None) -> None:
    """Return a stop's one admit slot after the claimed admit was refused."""
    admit_id = str((body or {}).get("dispatch_id") or "").strip()
    if not stop_id or not admit_id:
        return
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    CursorDispatchLedger.instance().release_stop_service(stop_id, admit_id)


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
    """Hold mints nothing unless a review owes apply. Play rematerializes the
    todo. An owing review outranks sit (including ROW_CLASS bind). Else sit on a
    forcing friction binds CDP ROW_CLASS; remaining sit is house generate."""
    review_body = maybe_review_apply_body(root_id, policy, digest, state)
    if leftover.get("leftover") == LEFTOVER_HOLD:
        return review_body
    todo = leftover.get("todo")
    if leftover.get("leftover") == LEFTOVER_PLAY and todo:
        roster_row_id = leftover.get("roster_row_id")
        if digest is not None and not roster_row_id:
            from bus_watch.roster import roster_play_rows
            from bus_watch.spawn_wake.play_classify import extract_todo_slug

            slug = str(todo).lower()
            for row, _verdict in roster_play_rows(digest):
                if extract_todo_slug(row.get("work_key")) == slug:
                    roster_row_id = str(row.get("row_id") or "")
                    break
        reuse = None
        hop_from = None
        service_stop_id = None
        if digest is not None:
            from bus_watch.spawn_wake.play_classify import (
                holder_lost_finished_hire,
                parked_resume,
            )

            found = parked_resume(digest)
            if found:
                reuse, hop_from = found
            if roster_row_id and not hop_from:
                for row in digest.get("roster") or []:
                    if isinstance(row, dict) and str(row.get("row_id") or "") == str(
                        roster_row_id
                    ):
                        # holder_lost already finished that hire. Claiming its
                        # dispatch id again returns stop_not_claimed and the
                        # row never admits.
                        if holder_lost_finished_hire(digest, str(todo)):
                            break
                        service_stop_id = (
                            str(row.get("last_hire_dispatch_id") or "").strip() or None
                        )
                        break
        return build_play_dispatch_body(
            root_id,
            policy,
            todo_slug=str(todo),
            roster_row_id=roster_row_id or None,
            reuse_thread=reuse,
            hop_from=hop_from,
            stop_id=service_stop_id,
        )
    if review_body:
        return review_body
    if digest is not None and state is not None:
        friction = sit_forcing_friction(digest, leftover)
        if friction:
            return body_for_sit_friction(root_id, policy, friction, state, digest)
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
    if verdict.get("leftover") == LEFTOVER_HOLD and not (body or {}).get(
        "_review_apply"
    ):
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
    if body and body.get("_refused") == "model_paused":
        return {
            "status_code": 0,
            "refused": "model_paused",
            "quiet_refusal": True,
            "body": body,
            "leftover": verdict,
        }
    if (
        body is not None
        and not body.get("model")
        and verdict.get("leftover") != LEFTOVER_PLAY
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
    work_key = str((body or {}).get("work_key") or "")
    if work_key and work_key in set(state.get("refused_work_keys") or []):
        # A 422 of this shape still mints a generate lane. Retrying it every
        # poll changes the digest and wakes the Cowork session again.
        return {
            "status_code": 0,
            "refused": _WORK_KEY_UNPARSEABLE,
            "quiet_refusal": True,
            "body": body,
            "leftover": verdict,
        }
    stop_id = ""
    if body and verdict.get("leftover") == LEFTOVER_PLAY:
        stop_id = str(body.get("hop_from") or body.get("_stop_id") or "").strip()
        if stop_id:
            from services.git_integration_worker.cursor_dispatch_ledger import (
                CursorDispatchLedger,
            )

            admit_id = str(body.get("dispatch_id") or "").strip() or str(uuid.uuid4())
            body["dispatch_id"] = admit_id
            if not CursorDispatchLedger.instance().claim_stop_service(
                stop_id, admit_id
            ):
                return {
                    "status_code": 0,
                    "refused": "stop_not_claimed",
                    "quiet_refusal": True,
                    "body": body,
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
    if status <= 0 or status >= 400:
        _release_stop_claim(stop_id, body)
    if status >= 400:
        err = payload.get("error") or {}
        code = err.get("code") if isinstance(err, dict) else None
        if code == _WORK_KEY_IN_FLIGHT or status == 409:
            result["quiet_refusal"] = True
        if code == _WORK_KEY_UNPARSEABLE and work_key:
            seen = [str(k) for k in (state.get("refused_work_keys") or []) if k]
            if work_key not in seen:
                seen.append(work_key)
            state["refused_work_keys"] = seen[-32:]
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
    roster_row_id = str((body or {}).get("_roster_row_id") or "").strip()
    dispatch_id = str(
        payload.get("dispatch_id") or execution_id or thread_id or ""
    ).strip()
    if roster_row_id and dispatch_id:
        from bus_watch.roster import record_row_hire

        record_row_hire(root_id, roster_row_id, dispatch_id)
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
    if night_id not in paged_nights and not (body or {}).get("_review_apply"):
        page_liaison(
            root_id,
            f"liaison {root_id} — first spawn {night_id}",
            f"Spawned successor execution_id={execution_id} thread={thread_id}.",
        )
        paged_nights.add(night_id)
        state["spawn_paged_nights"] = sorted(paged_nights)
    return result


def _remember_unparseable_work_key(state: dict[str, Any], work_key: str) -> None:
    if not work_key:
        return
    seen = [str(key) for key in (state.get("refused_work_keys") or []) if key]
    if work_key not in seen:
        seen.append(work_key)
    state["refused_work_keys"] = seen[-32:]


def _page_roster_remint(
    root_id: str, state: dict[str, Any], payload: dict[str, Any]
) -> bool:
    """Latch and page a REMINT_CAP refusal once per night. False for any other code."""
    wall = record_remint_cap(state, payload, night_id=current_night_id(), at=_utcnow())
    if not wall:
        return False
    night_id = str(wall.get("night_id") or "")
    page_liaison(
        root_id,
        f"liaison {root_id} — REMINT_CAP wall {night_id}",
        f"GIW refused the night's work_key: {wall['message']} "
        "Ticker holds until the night rolls.",
    )
    return True


def _emit_roster_classify_refuses(
    root_id: str, digest: dict[str, Any], state: dict[str, Any]
) -> None:
    """Emit readmit_cap / max_conductors once per row and reason."""
    del root_id
    roster = digest.get("roster")
    if not isinstance(roster, list) or not roster:
        return
    from bus_watch.events import emit_roster_hire_refused
    from bus_watch.roster import classify_roster_rows

    sent = state.get("roster_classify_refused")
    if not isinstance(sent, dict):
        sent = {}
    else:
        sent = dict(sent)
    changed = False
    for row, verdict in zip(roster, classify_roster_rows(digest), strict=False):
        reason = str(verdict.get("reason") or "")
        if reason not in ("readmit_cap", "max_conductors"):
            continue
        row_id = str(row.get("row_id") or "")
        stamp = f"{row_id}:{reason}"
        if sent.get(stamp):
            continue
        emit_roster_hire_refused(
            row_id=row_id,
            work_key=str(row.get("work_key") or ""),
            reason=reason,
        )
        sent[stamp] = _utcnow()
        changed = True
    if changed:
        state["roster_classify_refused"] = dict(list(sent.items())[-200:])


def _roster_hire_active(digest: dict[str, Any]) -> bool:
    roster = digest.get("roster")
    if not isinstance(roster, list) or not roster:
        return False
    from bus_watch.roster import roster_play_rows

    return bool(roster_play_rows(digest))


def _post_roster_hires(
    digest: dict[str, Any],
    state: dict[str, Any],
    root_id: str,
    evaluation: dict[str, Any],
    *,
    submit: Callable[..., tuple[dict[str, Any], int]] | None,
) -> dict[str, Any]:
    """Post play rows up to the conductor cap. Does not write ``pending_spawn``."""
    from bus_watch.events import (
        emit_roster_hire_latched,
        emit_roster_hire_refused,
        emit_roster_hire_released,
    )
    from bus_watch.roster import record_row_hire, roster_play_rows
    from bus_watch.spawn_wake.play_classify import (
        closed_unharvested_lane_ids,
        extract_todo_slug,
        holder_lost_finished_hire,
    )

    policy = digest.get("policy") or {}
    roster = digest.get("roster") or []
    live = sum(1 for row in roster if isinstance(row, dict) and row.get("live") is True)
    cap = max(0, int(policy.get("max_conductors") or 2) - live)
    poster = submit or submit_team_dispatch
    posts: list[dict[str, Any]] = []
    dropped: list[str] = []
    refused_keys = {str(key) for key in (state.get("refused_work_keys") or []) if key}
    if cap == 0 and roster_play_rows(digest):
        dropped.append("conductor_cap")
    for row, verdict in roster_play_rows(digest)[:cap]:
        work_key = str(row.get("work_key") or "")
        if work_key and work_key in refused_keys:
            dropped.append("refused_work_key")
            continue
        slug = extract_todo_slug(row.get("work_key")) or ""
        release_reason = str(verdict.get("release_reason") or "")
        release_lane = str(verdict.get("release_lane_id") or "")
        latched = str(row.get("last_hire_dispatch_id") or "").strip()
        if release_reason:
            emit_roster_hire_released(
                row_id=str(row.get("row_id") or ""),
                dispatch_id=latched,
                reason=release_reason,
            )
        stop_id = None
        if (
            latched
            and release_reason
            and release_reason != "holder_lost"
            and not holder_lost_finished_hire(digest, slug)
        ):
            stop_id = latched
        body = build_play_dispatch_body(
            root_id,
            policy,
            todo_slug=slug,
            roster_row_id=str(row.get("row_id") or "") or None,
            stop_id=stop_id,
        )
        if stop_id:
            from services.git_integration_worker.cursor_dispatch_ledger import (
                CursorDispatchLedger,
            )

            # Stargate reserves this id for the admitted row, so the slot's
            # serviced_admit and the successor dispatch_id stay the same value.
            admit_id = str(body.get("dispatch_id") or "").strip() or str(uuid.uuid4())
            body["dispatch_id"] = admit_id
            if not CursorDispatchLedger.instance().claim_stop_service(
                stop_id, admit_id
            ):
                from bus_watch.spawn_wake.consumed_stop import release_consumed_stop

                # The slot was taken by an admit that never became a dispatch.
                # Claiming it again is what made every later play a silent hold.
                if release_consumed_stop(
                    body, stop_id, digest.get("unstarted_claims") or []
                ):
                    emit_roster_hire_released(
                        row_id=str(row.get("row_id") or ""),
                        dispatch_id=stop_id,
                        reason="stop_unstarted",
                    )
                else:
                    emit_roster_hire_refused(
                        row_id=str(row.get("row_id") or ""),
                        work_key=str(row.get("work_key") or ""),
                        reason="stop_not_claimed",
                    )
                    dropped.append("stop_not_claimed")
                    continue
        from bus_watch.spawn_wake.park_release import arm_hop_park_release

        # The hop reactor's mission-cap park is not the liaison's hire. A
        # roster play releases it; the body still names no next step.
        arm_hop_park_release(body, work_key)
        payload, status = poster(_wire_submit_body(body))
        err = payload.get("error") or {}
        code = err.get("code") if isinstance(err, dict) else None
        posted_key = str(body.get("work_key") or work_key)
        row_id = str(row.get("row_id") or "")
        if status <= 0 or status >= 400:
            _release_stop_claim(stop_id, body)
        if code == _WORK_KEY_IN_FLIGHT or status == 409:
            # REMINT_CAP is itself a 409. Page that wall; other 409s stay in-flight.
            if _page_roster_remint(root_id, state, payload):
                reason = str(code or "CURSOR_WORK_KEY_REMINT_CAP")
                emit_roster_hire_refused(
                    row_id=row_id,
                    work_key=posted_key,
                    reason=reason,
                )
            else:
                reason = "work_key_in_flight"
                emit_roster_hire_refused(
                    row_id=row_id,
                    work_key=posted_key,
                    reason=reason,
                )
            dropped.append(reason)
            continue
        if status >= 400:
            if code == _WORK_KEY_UNPARSEABLE:
                _remember_unparseable_work_key(state, posted_key)
                refused_keys.add(posted_key)
            _page_roster_remint(root_id, state, payload)
            reason = str(code or status)
            emit_roster_hire_refused(
                row_id=row_id,
                work_key=posted_key,
                reason=reason,
            )
            dropped.append(reason)
            continue
        if status <= 0:
            dropped.append("status_0")
            continue
        dispatch_id = str(
            payload.get("dispatch_id")
            or payload.get("execution_id")
            or payload.get("thread_id")
            or payload.get("thread")
            or ""
        ).strip()
        if not dispatch_id:
            emit_roster_hire_refused(
                row_id=row_id,
                work_key=posted_key,
                reason="posted_unlatched",
            )
            dropped.append("posted_unlatched")
            continue
        readmit_from = release_lane if release_reason == "closed_park" else None
        record_row_hire(
            root_id,
            str(row.get("row_id") or ""),
            dispatch_id,
            readmit_from=readmit_from,
            closed_lane_ids=closed_unharvested_lane_ids(digest, slug),
        )
        merged_count = int(row.get("readmit_count") or 0)
        if readmit_from:
            merged_count += 1
        emit_roster_hire_latched(
            row_id=str(row.get("row_id") or ""),
            work_key=str(row.get("work_key") or ""),
            dispatch_id=dispatch_id,
            readmit_count=merged_count,
            readmit_from=readmit_from or row.get("readmit_from"),
        )
        posts.append({"status_code": status, "payload": payload, "body": body})
    if not posts:
        held: dict[str, Any] = {
            "action": "hold",
            "evaluation": evaluation,
            "body": None,
        }
        if dropped:
            held["refused"] = dropped[0]
        return held
    night_id = current_night_id()
    by_night = dict(state.get("dispatches_tonight_by_night") or {})
    count = int(by_night.get(night_id) or state.get("dispatches_tonight") or 0)
    count += len(posts)
    by_night[night_id] = count
    state["dispatches_tonight_by_night"] = by_night
    state["dispatches_tonight"] = count
    state["last_spawn_at"] = time.time()
    return {
        "action": "spawned",
        "evaluation": evaluation,
        "fires": posts,
        "body": posts[0]["body"],
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
    if not dry_run:
        from bus_watch.quiet_reason import close_unharvested_quiet_lanes_on_bus

        closed_quiet = close_unharvested_quiet_lanes_on_bus(digest.get("lanes") or [])
        if closed_quiet:
            digest["closed_unharvested_lanes"] = closed_quiet
    import sys
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[3])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    unstarted = CursorDispatchLedger.instance().unstarted_claims()
    if unstarted:
        digest["unstarted_claims"] = unstarted
    if not dry_run:
        from bus_watch.quiet_reason import (
            fetch_held_execution_ids,
            live_holder_projections,
            reconcile_holder_lost,
        )

        held = fetch_held_execution_ids()
        if held is not None:
            projections = live_holder_projections() or []
            digest["giw_live_projections"] = projections
            holder_lost = reconcile_holder_lost(
                digest.get("lanes") or [],
                held,
                projections=projections,
            )
            if holder_lost:
                digest["holder_lost"] = holder_lost
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
    leftover = evaluation.get("leftover") or classify_leftover(digest, state, lock=lock)
    body = body_for_leftover(
        root_id,
        policy,
        leftover,
        successor_context=successor_context,
        digest=digest,
        state=state,
    )
    if _roster_hire_active(digest):
        from bus_watch.spawn_wake.predicate import roster_hire_night_caps

        if not roster_hire_night_caps(evaluation.get("clauses") or {}):
            if not dry_run:
                _emit_roster_classify_refuses(root_id, digest, state)
            return {
                "action": "hold",
                "evaluation": evaluation,
                "refused": "roster_night_cap",
                "body": None,
            }
        if dry_run:
            return {
                "action": "would_spawn",
                "evaluation": evaluation,
                "body": body,
            }
        _emit_roster_classify_refuses(root_id, digest, state)
        return _post_roster_hires(
            digest,
            state,
            root_id,
            evaluation,
            submit=submit,
        )
    if not dry_run:
        _emit_roster_classify_refuses(root_id, digest, state)
    if leftover.get("leftover") == LEFTOVER_HOLD and not (body or {}).get(
        "_review_apply"
    ):
        steer_meta = maybe_steer_friction_gate(digest, state, leftover, submit=submit)
        out: dict[str, Any] = {
            "action": "hold",
            "evaluation": evaluation,
            "refused": PLAY_HOLD,
            "body": None,
        }
        if steer_meta is not None:
            out["friction_gate_steer"] = steer_meta
        return out
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
    work_key = str((body or {}).get("work_key") or "")
    refused_keys = {str(key) for key in (state.get("refused_work_keys") or []) if key}
    if work_key and work_key in refused_keys:
        return {
            "action": "hold",
            "evaluation": evaluation,
            "refused": _WORK_KEY_UNPARSEABLE,
            "body": body,
        }
    if body and body.get("_refused") == "model_paused":
        return {
            "action": "hold",
            "evaluation": evaluation,
            "refused": "model_paused",
            "body": body,
        }
    if dry_run:
        return {
            "action": "would_spawn" if evaluation["spawn"] else "hold",
            "evaluation": evaluation,
            "body": body,
        }
    if not evaluation["spawn"]:
        return {"action": "hold", "evaluation": evaluation, "body": body}
    live_steer = maybe_steer_live_dispatch(
        digest,
        state,
        lock,
        evaluation,
        body,
        policy,
        submit=submit,
    )
    if live_steer is not None:
        return {
            "action": "hold",
            "evaluation": evaluation,
            "refused": LIVE_DISPATCH_HOLD,
            "body": None,
            "live_dispatch_steer": live_steer,
        }
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
        review_key = str((body or {}).get("_review_key") or "")
        if (body or {}).get("_review_apply") and review_key:
            mark_review_apply_fired(state, review_key)
    if fired.get("quiet_refusal") or fired.get("refused"):
        return {
            "action": "hold",
            "evaluation": evaluation,
            "fire": fired,
            "refused": fired.get("refused"),
        }
    return {"action": "spawned", "evaluation": evaluation, "fire": fired}
