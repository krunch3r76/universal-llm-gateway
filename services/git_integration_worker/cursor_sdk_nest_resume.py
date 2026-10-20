"""Resume a freeform parent whose run ended while its nest child was live.

Nest admit parks the parent (``parked_waiting`` + ``park_child_dispatch_id``).
The parent worker is not blocked on the child. When that run ends first,
``mark_terminal`` used to overwrite the park, post a closeout, and terminate
the bus link. Child completion then hop-resumes conductors only, so a
freeform parent's lane commit was never harvested (friction:39846,
parent ``5d856fb39dbd-82ad0245``, child ``8e1dc13b35cd-48bffb1c``).

A non-conductor parent claims ``nest_parent_run_exited`` and posts a PARKED
advisory instead of a terminal closeout. When the child terminals, GIW seals
the parent and admits ``resume_of`` so the same execution can harvest and land.
"""

from __future__ import annotations

import json
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_resume import persist_resume_retain
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

logger = get_logger(__name__)

NEST_PARENT_RUN_EXITED = "nest_parent_run_exited"
NEST_PARENT_EXIT_BODY = "nest_parent_exit_body"
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_LIVE = frozenset({"queued", "admitted", "running", "parked_waiting", "claimed"})
_BODY_CAP = 2000


def nest_parked_subject(*, dispatch_id: str, child_id: str) -> str:
    """Advisory subject. ``PARKED`` keeps poll_hint from treating it as closeout."""
    return f"cursor-sdk dispatch {dispatch_id} PARKED (nest child {child_id})"


def nest_resumed_subject(*, child_id: str, parent_id: str) -> str:
    return (
        f"cursor-sdk dispatch {child_id} RESUMED "
        f"(resume_of {parent_id}, nest_child_terminal)"
    )


def _record(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def claim_nest_parent_exit(dispatch_id: str) -> str | None:
    """Stamp exit on a non-conductor parked on a still-live child.

    Returns the child id when this call claimed the exit. Conductors keep
    the hop path. A child that is already terminal is not claimed.
    """
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, contract, park_child_dispatch_id, record_json "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            return None
        if str(row["contract"] or "").lower() == "conductor":
            return None
        if row["status"] != "parked_waiting":
            return None
        child_id = row["park_child_dispatch_id"]
        if not child_id:
            return None
        child = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (child_id,),
        ).fetchone()
        if child is None or str(child["status"] or "") in _TERMINAL:
            return None
        data = _record(row["record_json"])
        data[NEST_PARENT_RUN_EXITED] = True
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (json.dumps(data, sort_keys=True), dispatch_id),
        )
        return str(child_id)


def remember_exit_body(*, dispatch_id: str, body: str) -> None:
    text = (body or "").strip()
    if len(text) > _BODY_CAP:
        text = text[: _BODY_CAP - 20] + "\n…(truncated)…"
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id=dispatch_id,
        patch={NEST_PARENT_EXIT_BODY: text},
    )


def nest_resume_parent_id(child_id: str) -> str | None:
    """Parked non-conductor parent that claimed exit, with no other live child."""
    ledger = CursorDispatchLedger.instance()
    parked = ledger.find_parked_parent_for_child(child_id=child_id)
    if parked is None:
        return None
    parent_id, _repo = parked
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT contract, record_json FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    if row is None:
        return None
    if str(row["contract"] or "").lower() == "conductor":
        return None
    if not _record(row["record_json"]).get(NEST_PARENT_RUN_EXITED):
        return None
    if _other_live_nested(parent_id, except_child=child_id):
        return None
    return parent_id


def _other_live_nested(parent_id: str, *, except_child: str) -> bool:
    ledger = CursorDispatchLedger.instance()
    for child_id in ledger.list_nested_children(parent_dispatch_id=parent_id):
        if child_id == except_child:
            continue
        status = ledger.dispatch_status_by_id(dispatch_id=child_id)
        if status and str(status.get("status") or "") in _LIVE:
            return True
    return False


def _resume_request(parent_id: str, *, child_id: str) -> CursorDispatchRequest | None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT thread_id, execution_id, caller_agent, resolved_model, "
            "contract, work_key, source_ref, record_json "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    if row is None:
        return None
    record = _record(row["record_json"])
    model = str(record.get("model") or row["resolved_model"] or "")
    if model and "/" not in model:
        model = f"cursor/{model}"
    excerpt = str(record.get(NEST_PARENT_EXIT_BODY) or "").strip() or "none"
    notice = (
        "NEST-RESUME — substrate notice, not operator prose.\n"
        f"Dispatch {parent_id} ended its run while nested child {child_id} "
        "was still in flight. That child is now terminal. You are the same "
        f"agent on agent-bus:{row['thread_id']}.\n"
        "Harvest the nested child's commit, review, and land. Do not "
        "re-implement from scratch.\n"
        f"Prior turn excerpt:\n{excerpt}"
    )
    original = str(record.get("prompt_preamble") or "").strip()
    preamble = f"{notice}\n\n{original}".strip() if original else notice
    lane = record.get("lane")
    return CursorDispatchRequest(
        thread_id=str(row["thread_id"]),
        model=model,
        dispatch_id=f"{parent_id}-nr1",
        execution_id=str(row["execution_id"] or parent_id),
        caller_agent=row["caller_agent"],
        packet_path=record.get("packet_path"),
        message=record.get("message"),
        handoff_contract=str(record.get("handoff_contract") or row["contract"] or "")
        or None,
        prompt_preamble=preamble,
        skills=record.get("skills"),
        model_knobs=record.get("model_knobs"),
        read_only=bool(record.get("read_only", False)),
        lane=lane if lane in ("A", "B") else None,
        worktree_isolated=bool(record.get("worktree_isolated", False)),
        worktree_path=record.get("worktree_path"),
        work_key=row["work_key"],
        source_ref=row["source_ref"],
        resume_of=parent_id,
    )


async def admit_nest_parent_resume(
    parent_id: str,
    *,
    child_id: str,
    controller: Any,
    cfg: Any,
    bus: Any | None = None,
) -> bool:
    """Seal the parked parent and admit one ``resume_of`` child.

    The child dispatch must already be terminal so the work-key peer gate
    sees a finished nest. Parent ``parent_still_live`` is expected while
    the row is parked; eligibility is re-checked after the seal by admit.
    """
    from services.git_integration_worker.cursor_sdk_resume import (
        resume_eligibility_reason,
    )
    from services.git_integration_worker.routes.cursor_sdk import admit_cursor_dispatch

    ledger = CursorDispatchLedger.instance()
    reason = resume_eligibility_reason(ledger, parent_id=parent_id)
    persist_resume_retain(dispatch_id=parent_id)
    await ledger_mark_cancelled(parent_id)
    ledger.clear_park_child(parent_id=parent_id)
    if reason not in (None, "parent_still_live"):
        logger.warning(
            "nest parent resume ineligible parent=%s reason=%s",
            parent_id,
            reason,
        )
        return False
    req = _resume_request(parent_id, child_id=child_id)
    if req is None:
        return False
    response = await admit_cursor_dispatch(req, cfg=cfg, controller=controller)
    if getattr(response, "status_code", 500) not in (200, 202):
        logger.warning(
            "nest parent resume refused parent=%s child=%s status=%s",
            parent_id,
            req.dispatch_id,
            getattr(response, "status_code", None),
        )
        return False
    if bus is not None:
        await bus.reply(
            thread_id=req.thread_id,
            to_agent=req.caller_agent or "dispatch",
            from_agent="cursor-sdk",
            subject=nest_resumed_subject(child_id=req.dispatch_id, parent_id=parent_id),
            body=(
                f"resume_of {parent_id} admitted as {req.dispatch_id} "
                f"after nested child {child_id} terminated."
            ),
        )
    logger.info(
        "nest parent resumed parent=%s resume=%s nest_child=%s",
        parent_id,
        req.dispatch_id,
        child_id,
    )
    return True


async def ledger_mark_cancelled(parent_id: str) -> None:
    import asyncio

    ledger = CursorDispatchLedger.instance()
    await asyncio.to_thread(
        ledger.mark_terminal,
        dispatch_id=parent_id,
        terminal_status="cancelled",
    )


async def defer_finished_parent_for_live_nest(
    *,
    dispatch_id: str,
    thread_id: str,
    reply_to: str,
    bus: Any,
    closeout_body: str,
) -> bool:
    """Claim nest exit and post PARKED. True means do not terminal-close."""
    child_id = claim_nest_parent_exit(dispatch_id)
    if child_id is None:
        return False
    remember_exit_body(dispatch_id=dispatch_id, body=closeout_body)
    try:
        await bus.reply(
            thread_id=thread_id,
            to_agent=reply_to,
            from_agent="cursor-sdk",
            subject=nest_parked_subject(dispatch_id=dispatch_id, child_id=child_id),
            body=(
                f"Parent run ended while nested child {child_id} is still "
                "in flight. GIW will admit a resume_of this dispatch when "
                "that child terminals. This notice is not the closeout."
            ),
        )
    except Exception:
        logger.exception(
            "nest PARKED post failed dispatch=%s child=%s",
            dispatch_id,
            child_id,
        )
    return True
