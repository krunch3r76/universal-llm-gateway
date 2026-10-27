"""BLOCKED poster (spec §5.10)."""

from __future__ import annotations

from collections.abc import Callable

from agent_bus_store.db.turns import create_turn
from universal_logging import get_logger

from services.git_integration_worker.mechanical_land.store import MechanicalLandStore

logger = get_logger(__name__)

_FROM = "git-integration-worker"
_BLOCKED_AGENT = "web-anthropic"
_BLOCKED_THREAD = "12286"
_BODY_CAP = 2000


def _clip(text: str, limit: int = _BODY_CAP) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def build_blocked_subject(
    *,
    run_id: str,
    step_id: str,
    attempt: int,
    reason_code: str,
) -> str:
    return f"BLOCKED|mechanical-land|{run_id}|{step_id}@{attempt}|{reason_code}"


def build_blocked_body(
    *,
    run_id: str,
    kind: str,
    source_ref: str,
    work_key: str,
    step_id: str,
    attempt: int,
    reason_code: str,
    detail: str,
    lane_thread_id: str,
    last_dispatch: str | None,
    closeout_ref: str | None,
    head_sha: str | None,
    branch: str | None,
    master_sha: str | None,
    journal_uri: str,
    tip_seq: int,
) -> str:
    lines = [
        "TYPE: BLOCKED",
        f"run: {run_id}  kind: {kind}  source_ref: {source_ref}  work_key: {work_key}",
        f"step: {step_id} attempt: {attempt}  reason: {reason_code}",
        f"detail: {detail}",
        "bound: none",
        "review_route: none  reviewer: none",
        f"lane_thread: agent-bus:{lane_thread_id}  last_dispatch: {last_dispatch or ''}  "
        f"closeout: {closeout_ref or ''}",
        f"head_sha: {head_sha or ''}  branch: {branch or ''}  master: {master_sha or ''}",
        f"journal: {journal_uri}  tip_seq: {tip_seq}",
        f"resume_with: RULING: RESUME run={run_id}   |   "
        f"RULING: BLOCK run={run_id} reason=<text>",
    ]
    return _clip("\n".join(lines))


def build_pointer_body(
    *,
    run_id: str,
    step_id: str,
    attempt: int,
    reason_code: str,
    journal_uri: str,
    full_ref: str,
) -> str:
    return _clip(
        "\n".join(
            [
                "TYPE: BLOCKED-POINTER",
                f"run: {run_id}",
                f"step: {step_id}@{attempt}",
                f"reason: {reason_code}",
                f"journal: {journal_uri}",
                f"full: {full_ref}",
            ]
        )
    )


def _post_one_turn(
    *,
    thread_id: str,
    to_agent: str,
    subject: str,
    body: str,
    bus_post_fn: Callable[..., int | None] | None,
) -> int | None:
    post = bus_post_fn or _default_bus_post
    try:
        return post(
            thread_id=thread_id,
            to_agent=to_agent,
            subject=subject,
            body=body,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("BLOCKED bus post failed thread=%s: %s", thread_id, exc)
        return None


def _default_bus_post(
    *,
    thread_id: str,
    to_agent: str,
    subject: str,
    body: str,
) -> int | None:
    _thread, _turn_id, _ts, turn_number, _marked = create_turn(
        thread_id=thread_id,
        from_agent=_FROM,
        to_agent=to_agent,
        subject=subject,
        body=body,
    )
    return turn_number


def emit_blocked(
    store: MechanicalLandStore,
    *,
    run_id: str,
    step_id: str,
    attempt: int,
    reason_code: str,
    detail: str,
    bus_post_fn: Callable[..., int | None] | None = None,
) -> int | None:
    """Append ``blocked`` journal event and post bus turns; returns new seq."""
    snap = store.get_run(run_id)
    if snap is None:
        return None
    state = snap["state"]
    lane_thread_id = str(state.get("lane_thread_id") or "")
    tip = int(state.get("tip_seq") or 0)
    journal_uri = f"cortex://notes/system/runs/mechanical-land/{run_id}.md"
    subject = build_blocked_subject(
        run_id=run_id,
        step_id=step_id,
        attempt=attempt,
        reason_code=reason_code,
    )
    last_closeout = state.get("last_closeout") or {}
    body = build_blocked_body(
        run_id=run_id,
        kind=str(state.get("kind") or "implement"),
        source_ref=str(state.get("source_ref") or ""),
        work_key=str(state.get("work_key") or state.get("source_ref") or ""),
        step_id=step_id,
        attempt=attempt,
        reason_code=reason_code,
        detail=detail,
        lane_thread_id=lane_thread_id,
        last_dispatch=str(last_closeout.get("dispatch_id") or ""),
        closeout_ref=str(last_closeout.get("sidecar_ref") or ""),
        head_sha=str(last_closeout.get("head_sha") or ""),
        branch=str(last_closeout.get("branch") or ""),
        master_sha=None,
        journal_uri=journal_uri,
        tip_seq=tip,
    )
    posted_turn = _post_one_turn(
        thread_id=_BLOCKED_THREAD,
        to_agent=_BLOCKED_AGENT,
        subject=subject,
        body=body,
        bus_post_fn=bus_post_fn,
    )
    pointer_body = build_pointer_body(
        run_id=run_id,
        step_id=step_id,
        attempt=attempt,
        reason_code=reason_code,
        journal_uri=journal_uri,
        full_ref=f"agent-bus:{_BLOCKED_THREAD}#{posted_turn or ''}",
    )
    posted_lane = _post_one_turn(
        thread_id=lane_thread_id,
        to_agent=_BLOCKED_AGENT,
        subject=subject,
        body=pointer_body,
        bus_post_fn=bus_post_fn,
    )
    new_seq = store.append_event(
        run_id=run_id,
        prev_seq=tip,
        step_id=step_id,
        attempt=attempt,
        flow="main",
        event="blocked",
        payload={
            "step_id": step_id,
            "attempt": attempt,
            "reason_code": reason_code,
            "detail": detail,
            "posted_turn": posted_turn,
            "posted_turn_lane": posted_lane,
        },
        source="reactor",
    )
    store._set_run_status(run_id, "blocked")
    return new_seq
