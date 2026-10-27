"""Mechanical-land event reactor (spec §3.2, stage 4)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from implement_admission.closeout_models import ImplementCloseout
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client
from universal_logging import get_logger

from mechanical_land.fold import fold
from mechanical_land.transition import Transition
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_dispatch_read import _structured_closeout
from services.git_integration_worker.mechanical_land.blocked import emit_blocked
from services.git_integration_worker.mechanical_land.rulings import (
    ingest_rulings_for_run,
)
from services.git_integration_worker.mechanical_land.store import (
    MechanicalLandStore,
    _journal_entries_from_rows,
    build_implement_admit_body,
    post_stargate_team_dispatch,
)

logger = get_logger(__name__)

_PIPELINE_MODEL = "mechanical-land"
_STARGATE_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=15.0, pool=5.0)
_DEFERRED_COMMANDS = frozenset(
    {"gate", "land", "restart", "probe", "close", "review", "harvest"}
)


@dataclass
class ReactorDeps:
    store: MechanicalLandStore
    fetch_plan_fn: Callable[..., dict[str, Any] | None]
    post_admit_fn: Callable[..., tuple[int, dict[str, Any]]]
    post_steer_cancel_fn: Callable[..., tuple[bool, dict[str, Any]]] | None = None
    bus_post_fn: Callable[..., int | None] | None = None


def _default_fetch_plan(
    *,
    run_id: str,
    kind: str,
    policy: dict[str, Any],
    policy_sha256: str,
    journal: list[dict[str, Any]],
    stargate_url: str | None = None,
) -> dict[str, Any] | None:
    payload = {
        "model": _PIPELINE_MODEL,
        "messages": [{"role": "user", "content": "transition"}],
        "pipeline_options": {
            "op": "transition",
            "kind": kind,
            "policy": policy,
            "policy_sha256": policy_sha256,
            "journal": journal,
            "run_id": run_id,
        },
    }
    base = (stargate_url or DEFAULT_STARGATE_URL).rstrip("/")
    try:
        with make_sync_client(base, timeout=_STARGATE_TIMEOUT) as client:
            resp = client.post("/v1/chat/completions", json=payload)
        if resp.status_code >= 500:
            return None
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "mechanical-land transition fetch failed run=%s: %s", run_id, exc
        )
        return None
    choices = data.get("choices") or []
    if not choices:
        return None
    content = str((choices[0].get("message") or {}).get("content") or "")
    try:
        plan = json.loads(content)
    except json.JSONDecodeError:
        return None
    return plan if isinstance(plan, dict) else None


def _default_steer_cancel(
    *,
    dispatch_id: str,
    reason: str,
    stargate_url: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    body = {
        "op": "steer",
        "steer": "cancel_discard",
        "dispatch_id": dispatch_id,
        "reason": reason,
        "actor": "mechanical-land-reactor",
    }
    base = (stargate_url or DEFAULT_STARGATE_URL).rstrip("/")
    try:
        with make_sync_client(base, timeout=_STARGATE_TIMEOUT) as client:
            resp = client.post("/api/v1/team/dispatch", json=body)
        payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict):
            payload = {}
        return resp.status_code < 400, payload
    except httpx.HTTPError as exc:
        return False, {"error": str(exc)}


def _record_json(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.get("record_json") or "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def mechanical_land_stamp(row: dict[str, Any]) -> dict[str, Any] | None:
    rec = _record_json(row)
    gen = rec.get("generation_options") or {}
    if not isinstance(gen, dict):
        return None
    ml = gen.get("mechanical_land")
    if not isinstance(ml, dict):
        return None
    run_id = str(ml.get("run_id") or "").strip()
    if not run_id:
        return None
    return ml


def _folded_state(store: MechanicalLandStore, run_id: str) -> dict[str, Any] | None:
    meta = _load_run_meta(store, run_id)
    if meta is None:
        return None
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM mechanical_land_journal WHERE run_id=? ORDER BY seq ASC",
            (run_id,),
        ).fetchall()
    entries = _journal_entries_from_rows(run_id, list(rows))
    folded = fold(entries)
    folded.run_id = run_id
    state = folded.result_json()
    state["status"] = str(meta.get("status") or folded.status or "")
    state["lane_thread_id"] = meta.get("lane_thread_id")
    state["step"] = folded.step
    state["attempt"] = folded.attempt
    state["pending_hold"] = folded.pending_hold
    state["policy"] = folded.policy
    if folded.last_closeout is not None:
        lc = folded.last_closeout
        state["last_closeout"] = {
            "dispatch_id": lc.dispatch_id,
            "closeout_status": lc.closeout_status,
            "work_outcome": lc.work_outcome,
            "head_sha": lc.head_sha,
            "branch": lc.branch,
            "branch_point": lc.branch_point,
            "sidecar_ref": lc.sidecar_ref,
        }
    legs_by_step: dict[str, list[dict[str, Any]]] = {}
    for step_id, legs in folded.legs.items():
        legs_by_step[step_id] = [
            {
                "step_id": leg.step_id,
                "attempt": leg.attempt,
                "dispatch_id": leg.dispatch_id,
                "closeout_status": leg.closeout_status,
            }
            for leg in legs
        ]
    state["legs_by_step"] = legs_by_step
    state["rulings"] = [
        {
            "verb": getattr(r, "verb", None),
            "step": getattr(r, "step", None),
            "reason": getattr(r, "reason", None),
        }
        for r in folded.rulings
    ]
    return state


def _load_run_meta(store: MechanicalLandStore, run_id: str) -> dict[str, Any] | None:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM mechanical_land_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _journal_for_pipeline(
    store: MechanicalLandStore, run_id: str
) -> list[dict[str, Any]]:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM mechanical_land_journal WHERE run_id=? ORDER BY seq ASC",
            (run_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        out.append(
            {
                "run_id": run_id,
                "seq": int(row["seq"]),
                "prev_seq": int(row["prev_seq"]),
                "step_id": str(row["step_id"]),
                "attempt": int(row["attempt"]),
                "flow": str(row["flow"]),
                "event": str(row["event"]),
                "payload": payload if isinstance(payload, dict) else {},
                "source": str(row["source"]),
            }
        )
    return out


def _verification_summary(closeout: ImplementCloseout) -> str:
    parts: list[str] = []
    for row in closeout.verification[:5]:
        code = row.exit_code
        parts.append(f"{row.command}:{code}")
    return "; ".join(parts)


def _closeout_to_leg_closed(
    closeout: ImplementCloseout,
    *,
    dispatch_id: str,
) -> dict[str, Any]:
    return {
        "dispatch_id": dispatch_id,
        "closeout_status": str(closeout.status),
        "work_outcome": str(closeout.work_outcome or ""),
        "head_sha": str(closeout.head_sha or ""),
        "branch": str(closeout.branch or ""),
        "branch_point": str(closeout.branch_point or ""),
        "files_modified": list(closeout.files_modified),
        "files_created": list(closeout.files_created),
        "files_deleted": list(closeout.files_deleted),
        "verification_summary": _verification_summary(closeout),
        "sidecar_ref": f"tmp/reviews/closeouts/{dispatch_id}.md",
        "forbidden_git_ops": [],
    }


def _leg_closed_exists(
    store: MechanicalLandStore, run_id: str, dispatch_id: str
) -> bool:
    snap = store.get_run(run_id, event_limit=500)
    if snap is None:
        return False
    for ev in snap.get("events", []):
        if ev.get("event") != "leg_closed":
            continue
        payload = ev.get("payload") or {}
        if str(payload.get("dispatch_id") or "") == dispatch_id:
            return True
    with store._connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM mechanical_land_journal "
            "WHERE run_id=? AND event='leg_closed' "
            "AND json_extract(payload_json, '$.dispatch_id')=? LIMIT 1",
            (run_id, dispatch_id),
        ).fetchone()
    return row is not None


def ingest_terminal_closeout(
    deps: ReactorDeps,
    *,
    run_id: str,
    dispatch_id: str,
    stamp: dict[str, Any],
) -> bool:
    """Append ``leg_closed`` from structured ImplementCloseout when stamped."""
    if _leg_closed_exists(deps.store, run_id, dispatch_id):
        return False
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return False
    row_dict = {k: row[k] for k in row.keys()}
    structured = _structured_closeout(row_dict)
    if structured is None:
        return False
    try:
        closeout = ImplementCloseout.model_validate(structured)
    except Exception:  # noqa: BLE001
        return False
    state = _folded_state(deps.store, run_id)
    if state is None:
        return False
    tip = int(state.get("tip_seq") or 0)
    step_id = str(stamp.get("step_id") or state.get("step") or "implement")
    attempt = int(stamp.get("attempt") or state.get("attempt") or 1)
    deps.store.append_event(
        run_id=run_id,
        prev_seq=tip,
        step_id=step_id,
        attempt=attempt,
        flow="main",
        event="leg_closed",
        payload=_closeout_to_leg_closed(closeout, dispatch_id=dispatch_id),
        source="giw_closeout",
    )
    return True


def _transition_from_plan(plan: dict[str, Any]) -> Transition:
    tr = plan.get("transition") or plan
    return Transition(
        command=str(tr.get("command") or "noop"),
        step_id=tr.get("step_id"),
        attempt=tr.get("attempt"),
        to_step=tr.get("to_step"),
        reason=tr.get("reason"),
        needs_packet=bool(tr.get("needs_packet")),
        blocked=bool(tr.get("blocked")),
        reason_code=tr.get("reason_code"),
        detail=tr.get("detail"),
        review_route=tr.get("review_route"),
    )


def _admit_retry_count(store: MechanicalLandStore, run_id: str) -> int:
    snap = store.get_run(run_id, event_limit=200)
    if snap is None:
        return 0
    return sum(1 for e in snap["events"] if e.get("event") == "leg_admit_failed")


def _policy_retry_max(policy: dict[str, Any]) -> int:
    retry = policy.get("retry") or {}
    if isinstance(retry, dict):
        raw = retry.get("admit_retry_max", 2)
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return 2


def _append_transition_decided(
    store: MechanicalLandStore,
    *,
    run_id: str,
    state: dict[str, Any],
    tr: Transition,
    pipeline_execution_id: str | None = None,
) -> None:
    tip = int(state.get("tip_seq") or 0)
    from_step = str(state.get("step") or "run")
    store.append_event(
        run_id=run_id,
        prev_seq=tip,
        step_id=from_step,
        attempt=int(state.get("attempt") or 1),
        flow="main",
        event="transition_decided",
        payload={
            "from_step": from_step,
            "to_step": tr.to_step or tr.step_id or from_step,
            "attempt": tr.attempt or int(state.get("attempt") or 1),
            "reason": tr.reason or tr.command,
            "pipeline_execution_id": pipeline_execution_id,
        },
        source="pipeline",
    )


def _live_leg_dispatch_id(state: dict[str, Any]) -> str | None:
    step = str(state.get("step") or "")
    legs = state.get("legs_by_step") or {}
    rows = legs.get(step) if isinstance(legs, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    last = rows[-1]
    if isinstance(last, dict):
        if last.get("closeout_status"):
            return None
        disp = str(last.get("dispatch_id") or "").strip()
        return disp or None
    return None


def _execute_admit(
    deps: ReactorDeps,
    *,
    run_id: str,
    meta: dict[str, Any],
    state: dict[str, Any],
    tr: Transition,
    plan: dict[str, Any],
) -> bool:
    """Returns True when the loop should stop (async leg admitted)."""
    step_id = str(tr.step_id or "implement")
    attempt = int(tr.attempt or 1)
    if _leg_already_admitted(state, step_id, attempt):
        return True
    lane_thread_id = str(meta.get("lane_thread_id") or "")
    policy = json.loads(meta.get("policy_json") or "{}")
    if not isinstance(policy, dict):
        policy = {}
    policy_sha256 = str(meta.get("policy_sha256") or "")
    source_ref = str(meta.get("source_ref") or "")
    caller_agent = str(meta.get("caller_agent") or "mechanical-land")
    body = build_implement_admit_body(
        run_id=run_id,
        lane_thread_id=lane_thread_id,
        source_ref=source_ref,
        policy=policy,
        policy_sha256=policy_sha256,
        caller_agent=caller_agent,
    )
    gen_opts = dict(body.get("generation_options") or {})
    gen_ml = dict(gen_opts.get("mechanical_land") or {})
    gen_ml.update(
        {
            "run_id": run_id,
            "step_id": step_id,
            "attempt": attempt,
            "policy_sha256": policy_sha256,
        }
    )
    gen_opts["mechanical_land"] = gen_ml
    body["generation_options"] = gen_opts
    admit_body = plan.get("admit_body")
    if isinstance(admit_body, dict) and admit_body.get("prompt"):
        body["contract"] = "freeform"
        body["prompt"] = str(admit_body.get("prompt"))
    state = _folded_state(deps.store, run_id)
    if state is None:
        return True
    tip = int(state.get("tip_seq") or 0)
    try:
        status_code, payload = deps.post_admit_fn(body)
    except httpx.HTTPError as exc:
        recovered, _, _ = deps.store.recover_or_fail_admit_transport_error(
            run_id=run_id,
            lane_thread_id=lane_thread_id,
            step_id=step_id,
            attempt=attempt,
            prev_seq=tip,
            admit_body=body,
            exc=exc,
            packet_sha256=str(plan.get("packet_sha256") or ""),
        )
        if recovered:
            return True
        retries = _admit_retry_count(deps.store, run_id)
        if retries >= _policy_retry_max(policy):
            emit_blocked(
                deps.store,
                run_id=run_id,
                step_id=step_id,
                attempt=attempt,
                reason_code="admit_failed",
                detail=f"admit transport error after {retries} attempts",
                bus_post_fn=deps.bus_post_fn,
            )
        else:
            deps.store._set_run_status(run_id, "admit_deferred")
        return True
    state = _folded_state(deps.store, run_id)
    if state is None:
        return True
    tip = int(state.get("tip_seq") or 0)
    if status_code >= 400:
        deps.store.append_event(
            run_id=run_id,
            prev_seq=tip,
            step_id=step_id,
            attempt=attempt,
            flow="main",
            event="leg_admit_failed",
            payload={
                "status_code": status_code,
                "error": json.dumps(payload)[:2000],
            },
            source="reactor",
        )
        retries = _admit_retry_count(deps.store, run_id)
        if retries >= _policy_retry_max(policy):
            emit_blocked(
                deps.store,
                run_id=run_id,
                step_id=step_id,
                attempt=attempt,
                reason_code="admit_failed",
                detail=f"admit failed after {retries} attempts",
                bus_post_fn=deps.bus_post_fn,
            )
        else:
            deps.store._set_run_status(run_id, "admit_deferred")
        return True
    dispatch_id = (
        str(payload.get("dispatch_id") or payload.get("execution_id") or "").strip()
        or None
    )
    execution_id = str(payload.get("execution_id") or "").strip() or None
    state = _folded_state(deps.store, run_id)
    if state is None:
        return True
    tip = int(state.get("tip_seq") or 0)
    deps.store.append_event(
        run_id=run_id,
        prev_seq=tip,
        step_id=step_id,
        attempt=attempt,
        flow="main",
        event="leg_admitted",
        payload={
            "step_id": step_id,
            "attempt": attempt,
            "dispatch_id": dispatch_id or "",
            "execution_id": execution_id or "",
            "model": str(body.get("model") or ""),
            "contract": str(body.get("contract") or ""),
            "lane": str(body.get("lane") or "B"),
            "sdk_mode": body.get("sdk_mode"),
            "packet_sha256": str(plan.get("packet_sha256") or ""),
        },
        source="reactor",
    )
    deps.store._set_run_status(run_id, "awaiting_leg")
    return True


def _leg_already_admitted(state: dict[str, Any], step_id: str, attempt: int) -> bool:
    legs = state.get("legs_by_step") or {}
    if not isinstance(legs, dict):
        return False
    for leg in legs.get(step_id, []):
        if not isinstance(leg, dict):
            continue
        if int(leg.get("attempt") or 0) == attempt and leg.get("dispatch_id"):
            return True
    return False


def _execute_block(
    deps: ReactorDeps,
    *,
    run_id: str,
    tr: Transition,
    state: dict[str, Any],
) -> bool:
    emit_blocked(
        deps.store,
        run_id=run_id,
        step_id=str(tr.step_id or state.get("step") or "run"),
        attempt=int(tr.attempt or state.get("attempt") or 1),
        reason_code=str(tr.reason_code or "blocked"),
        detail=str(tr.detail or tr.reason or ""),
        bus_post_fn=deps.bus_post_fn,
    )
    return True


def _execute_ruling_block(
    deps: ReactorDeps, *, run_id: str, state: dict[str, Any]
) -> None:
    dispatch_id = _live_leg_dispatch_id(state)
    if dispatch_id and deps.post_steer_cancel_fn is not None:
        deps.post_steer_cancel_fn(
            dispatch_id=dispatch_id,
            reason="mechanical_land_ruling_block",
        )


def run_reactor_loop(
    deps: ReactorDeps,
    *,
    run_id: str,
    max_sync_steps: int = 8,
) -> None:
    """Ingest → fold → transition → execute until async or cap."""
    meta = _load_run_meta(deps.store, run_id)
    if meta is None:
        return
    policy = json.loads(meta.get("policy_json") or "{}")
    if not isinstance(policy, dict):
        policy = {}
    ruling_authors = list(policy.get("ruling_authors") or ["web-anthropic"])
    lane_thread_id = str(meta.get("lane_thread_id") or "")
    ingest_rulings_for_run(
        deps.store,
        run_id=run_id,
        lane_thread_id=lane_thread_id,
        ruling_authors=ruling_authors,
    )
    state = _folded_state(deps.store, run_id)
    if state is not None:
        for ruling in state.get("rulings") or []:
            if isinstance(ruling, dict) and ruling.get("verb") == "BLOCK":
                _execute_ruling_block(deps, run_id=run_id, state=state)
                return
    for _ in range(max_sync_steps):
        state = _folded_state(deps.store, run_id)
        if state is None:
            return
        status = str(state.get("status") or "")
        if status in ("closed", "blocked"):
            return
        if state.get("pending_hold") or status == "blocked_hold":
            return
        journal = _journal_for_pipeline(deps.store, run_id)
        plan = deps.fetch_plan_fn(
            run_id=run_id,
            kind=str(meta.get("kind") or "implement"),
            policy=policy,
            policy_sha256=str(meta.get("policy_sha256") or ""),
            journal=journal,
        )
        if plan is None:
            deps.store._set_run_status(run_id, "admit_deferred")
            return
        tr = _transition_from_plan(plan)
        if tr.command == "wait":
            return
        if tr.command == "noop":
            return
        state = _folded_state(deps.store, run_id)
        if state is None:
            return
        _append_transition_decided(deps.store, run_id=run_id, state=state, tr=tr)
        state = _folded_state(deps.store, run_id)
        if state is None:
            return
        if tr.command == "block" or tr.blocked:
            _execute_block(deps, run_id=run_id, tr=tr, state=state)
            return
        if tr.command == "admit":
            if _execute_admit(
                deps,
                run_id=run_id,
                meta=meta,
                state=state,
                tr=tr,
                plan=plan,
            ):
                return
            continue
        if tr.command in _DEFERRED_COMMANDS:
            return
        return


async def maybe_fire_mechanical_land_reactor(*, dispatch_id: str) -> None:
    """Terminal-hook entry: resolve stamped run and drive one reactor pass."""
    try:
        ledger = CursorDispatchLedger.instance()
        with ledger._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        if row is None:
            return
        row_dict = {k: row[k] for k in row.keys()}
        stamp = mechanical_land_stamp(row_dict)
        if stamp is None:
            return
        run_id = str(stamp.get("run_id") or "")
        if not run_id:
            return
        deps = ReactorDeps(
            store=MechanicalLandStore(),
            fetch_plan_fn=_default_fetch_plan,
            post_admit_fn=post_stargate_team_dispatch,
            post_steer_cancel_fn=_default_steer_cancel,
        )
        ingest_terminal_closeout(
            deps, run_id=run_id, dispatch_id=dispatch_id, stamp=stamp
        )
        run_reactor_loop(deps, run_id=run_id)
    except Exception:  # noqa: BLE001 — advisory on terminal hot path
        logger.exception(
            "mechanical_land reactor failed dispatch_id=%s",
            dispatch_id,
        )


def run_reactor_for_run_id(run_id: str, *, deps: ReactorDeps | None = None) -> None:
    """Synchronous sweep/reconcile driver."""
    use_deps = deps or ReactorDeps(
        store=MechanicalLandStore(),
        fetch_plan_fn=_default_fetch_plan,
        post_admit_fn=post_stargate_team_dispatch,
        post_steer_cancel_fn=_default_steer_cancel,
    )
    run_reactor_loop(use_deps, run_id=run_id)


def repost_blocked_if_needed(store: MechanicalLandStore, run_id: str) -> None:
    """Sweep: re-post BLOCKED bus turns once when ``posted_turn`` was null."""
    meta = _load_run_meta(store, run_id)
    if meta is None or str(meta.get("status") or "") != "blocked":
        return
    with store._connect() as conn:
        row = conn.execute(
            "SELECT seq, payload_json FROM mechanical_land_journal "
            "WHERE run_id=? AND event='blocked' ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    if row is None:
        return
    blocked_seq = int(row["seq"])
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    if payload.get("posted_turn") is not None:
        return
    repost_attempts = int(payload.get("repost_attempts") or 0)
    if repost_attempts > 0 or payload.get("_repost_in_flight"):
        return
    payload["_repost_in_flight"] = True
    with store._connect() as conn_preflight:
        conn_preflight.execute(
            "UPDATE mechanical_land_journal SET payload_json=? WHERE run_id=? AND seq=?",
            (json.dumps(payload, sort_keys=True), run_id, blocked_seq),
        )
        conn_preflight.commit()
    posted_turn = _post_blocked_bus_only(
        store,
        run_id=run_id,
        payload=payload,
        lane_thread_id=str(meta.get("lane_thread_id") or ""),
    )
    payload.pop("_repost_in_flight", None)
    if posted_turn is None:
        with store._connect() as conn_fail:
            conn_fail.execute(
                "UPDATE mechanical_land_journal SET payload_json=? WHERE run_id=? AND seq=?",
                (json.dumps(payload, sort_keys=True), run_id, blocked_seq),
            )
            conn_fail.commit()
        return
    payload["repost_attempts"] = repost_attempts + 1
    payload["posted_turn"] = posted_turn
    with store._connect() as conn2:
        conn2.execute(
            "UPDATE mechanical_land_journal SET payload_json=? WHERE run_id=? AND seq=?",
            (json.dumps(payload, sort_keys=True), run_id, blocked_seq),
        )
        conn2.commit()


def _post_blocked_bus_only(
    store: MechanicalLandStore,
    *,
    run_id: str,
    payload: dict[str, Any],
    lane_thread_id: str,
) -> int | None:
    from services.git_integration_worker.mechanical_land import blocked as blocked_mod

    subject = blocked_mod.build_blocked_subject(
        run_id=run_id,
        step_id=str(payload.get("step_id") or "run"),
        attempt=int(payload.get("attempt") or 1),
        reason_code=str(payload.get("reason_code") or "blocked"),
    )
    body = blocked_mod.build_blocked_body(
        run_id=run_id,
        kind="implement",
        source_ref="",
        work_key="",
        step_id=str(payload.get("step_id") or "run"),
        attempt=int(payload.get("attempt") or 1),
        reason_code=str(payload.get("reason_code") or "blocked"),
        detail=str(payload.get("detail") or ""),
        lane_thread_id=lane_thread_id,
        last_dispatch=None,
        closeout_ref=None,
        head_sha=None,
        branch=None,
        master_sha=None,
        journal_uri=f"cortex://notes/system/runs/mechanical-land/{run_id}.md",
        tip_seq=0,
    )
    return blocked_mod._post_one_turn(
        thread_id=blocked_mod._BLOCKED_THREAD,
        to_agent=blocked_mod._BLOCKED_AGENT,
        subject=subject,
        body=body,
        bus_post_fn=None,
    )
