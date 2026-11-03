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

import asyncio
import json
from typing import Any, NamedTuple

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
    """Subject for the bus turn posted after a nest ``resume_of`` is admitted.

    Names the new child and the parked parent so pollers can tell this
    advisory from a terminal closeout.
    """
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
    """Store a capped excerpt of the parent's last turn for the resume preamble.

    The resume agent reads this back as prior-turn context. The write merges
    into ``record_json`` and does not change dispatch status.
    """
    text = (body or "").strip()
    if len(text) > _BODY_CAP:
        text = text[: _BODY_CAP - 20] + "\n…(truncated)…"
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id=dispatch_id,
        patch={NEST_PARENT_EXIT_BODY: text},
    )


class ParkResumeClaim(NamedTuple):
    """Result of the single parked-parent resume claim.

    ``outcome`` is ``claimed`` when this call moved the row out of
    ``parked_waiting``. ``park_child_id`` is the link read under the same
    transaction, before the claim clears it.
    """

    outcome: str
    park_child_id: str | None


class NestResumeAdmit(NamedTuple):
    """Whether one nest-parent resume took the lease.

    ``admitted`` means the resume row landed and owns the lease. ``lost``
    means another resumer already owns the claim, so the caller must not
    release or promote. ``refused`` means this call declined. A permanent
    refusal leaves the row sealed so the caller may promote. A retryable
    refusal restores the park first; promote then sees that row and waits.
    """

    outcome: str


class HandResumeClaim(NamedTuple):
    """Shared-admit gate for one parked parent resume.

    ``refusal`` is set when this call must not admit. ``claimed`` is true
    only when this call's update sealed the parent. ``park_child_id`` is
    the link that update cleared, so a later admit failure can restore it.
    """

    refusal: str | None = None
    claimed: bool = False
    park_child_id: str | None = None


def claim_parked_parent_resume(
    parent_id: str,
    *,
    park_child_id: str | None,
    resume_id: str,
) -> ParkResumeClaim:
    """Claim one parked parent for a single resume admit.

    One ``UPDATE`` under ``BEGIN IMMEDIATE``: ``status='parked_waiting'``,
    the ``nest_parent_run_exited`` stamp set, and ``park_child_dispatch_id``
    equal to ``park_child_id`` when that link is given. The row moves to
    ``cancelled`` and records ``park_resumed_by``. Callers admit only when
    ``outcome`` is ``claimed`` or ``already_claimed_by_us``. A second resumer
    gets ``lost`` or ``already_claimed_by_other`` and must not admit.
    ``not_waiting`` means the row was not parked (a steer park stays on its
    own ``park_resumed_by`` stamp). ``not_exited`` means the parent is parked
    on a live nest and must not be hand-resumed.
    """
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, park_child_dispatch_id, park_resumed_by, record_json "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
        if row is None:
            return ParkResumeClaim("not_waiting", None)
        link = row["park_child_dispatch_id"]
        link_text = str(link) if link else None
        stamped = bool(_record(row["record_json"]).get(NEST_PARENT_RUN_EXITED))
        status = str(row["status"] or "")
        owner = str(row["park_resumed_by"] or "") or None
        if status != "parked_waiting":
            if stamped and owner == resume_id:
                return ParkResumeClaim("already_claimed_by_us", link_text)
            if stamped and owner and owner != resume_id:
                return ParkResumeClaim("already_claimed_by_other", link_text)
            return ParkResumeClaim("not_waiting", link_text)
        if park_child_id is not None and link_text != park_child_id:
            return ParkResumeClaim("lost", link_text)
        if not stamped:
            return ParkResumeClaim("not_exited", link_text)
        from services.git_integration_worker.cursor_dispatch_ledger import _now

        cur = conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='cancelled', "
            "terminal_status='cancelled', terminal_at=?, "
            "park_child_dispatch_id=NULL, park_resumed_by=? "
            "WHERE dispatch_id=? AND status='parked_waiting' "
            "AND park_child_dispatch_id IS ? "
            "AND COALESCE(json_extract(record_json, '$.nest_parent_run_exited'), 0) = 1",
            (_now(), resume_id, parent_id, link),
        )
        if cur.rowcount != 1:
            return ParkResumeClaim("lost", link_text)
    ledger.mark_terminal(dispatch_id=parent_id, terminal_status="cancelled")
    persist_resume_retain(dispatch_id=parent_id)
    return ParkResumeClaim("claimed", link_text)


async def _release_handed_slot(resume_id: str) -> None:
    """Drop a resume slot when the admit that claimed it did not land."""
    from services.git_integration_worker.cursor_sdk_gate import (
        force_release_sdk_dispatch_slot,
    )

    await force_release_sdk_dispatch_slot(dispatch_id=resume_id)


async def _hand_slot_to_resume(
    *, parent_id: str, child_id: str | None, resume_id: str
) -> None:
    """Move the capacity slot onto the resume id without waking FIFO waiters."""
    from services.git_integration_worker.cursor_sdk_gate import (
        transfer_sdk_dispatch_slot,
    )

    for holder in (parent_id, child_id):
        if not holder or holder == resume_id:
            continue
        try:
            await transfer_sdk_dispatch_slot(from_id=holder, to_id=resume_id)
            return
        except Exception:
            logger.info(
                "nest resume slot not held by %s; trying the other id resume=%s",
                holder,
                resume_id,
            )
    logger.info(
        "nest resume slot transfer found no holder parent=%s child=%s resume=%s",
        parent_id,
        child_id,
        resume_id,
    )


async def refuse_second_parked_resume(req: Any) -> HandResumeClaim:
    """Claim a parked exited parent on the shared admit path.

    Returns a refusal when this ``resume_of`` lost the claim or the parent
    is parked but its run has not exited. ``claimed`` is false when the
    admit may proceed without this call having sealed the row: an earlier
    call in this same resume won, or the parent is not ``parked_waiting``
    (steer restart parks). Callers that see ``claimed`` and then fail
    before inserting the resume row must undo the claim.
    """
    parent_id = getattr(req, "resume_of", None)
    resume_id = getattr(req, "dispatch_id", None)
    if not parent_id or not resume_id:
        return HandResumeClaim()
    claim = await asyncio.to_thread(
        claim_parked_parent_resume,
        str(parent_id),
        park_child_id=None,
        resume_id=str(resume_id),
    )
    if claim.outcome in ("lost", "not_exited", "already_claimed_by_other"):
        return HandResumeClaim(
            refusal=(
                f"parked parent {parent_id} resume claim {claim.outcome} "
                f"for {resume_id}"
            )
        )
    if claim.outcome == "claimed":
        await _hand_slot_to_resume(
            parent_id=str(parent_id),
            child_id=claim.park_child_id,
            resume_id=str(resume_id),
        )
        return HandResumeClaim(claimed=True, park_child_id=claim.park_child_id)
    return HandResumeClaim()


def _undo_unadmitted_hand_claim_sync(
    *, parent_id: str, resume_id: str, park_child_id: str | None
) -> bool:
    """Put a cancelled parent back to ``parked_waiting`` if no resume row exists."""
    ledger = CursorDispatchLedger.instance()
    if ledger.dispatch_status_by_id(dispatch_id=resume_id) is not None:
        return False
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='parked_waiting', "
            "terminal_status=NULL, terminal_at=NULL, "
            "park_child_dispatch_id=?, park_resumed_by=NULL, "
            "memo_state=CASE WHEN memo_state='owed' THEN NULL ELSE memo_state END, "
            "memo_next_at=CASE WHEN memo_state='owed' THEN NULL ELSE memo_next_at END, "
            "record_json=json_remove(COALESCE(record_json, '{}'), '$.resume_retain') "
            "WHERE dispatch_id=? AND status='cancelled' AND park_resumed_by=?",
            (park_child_id, parent_id, resume_id),
        )
        conn.execute(
            "DELETE FROM cursor_dispatch_stop_service "
            "WHERE stop_id=? AND serviced_admit IS NULL",
            (parent_id,),
        )
    return True


async def undo_unadmitted_hand_claim(
    *, parent_id: str, resume_id: str, park_child_id: str | None
) -> bool:
    """Restore a hand-claimed parent when its resume row never landed.

    The claim cancels the parent and moves the capacity slot onto
    ``resume_id``. A later ``ledger.admit`` exception would leave that
    parent cancelled and the slot stuck. This runs only while no resume
    row exists, then drops the handed slot. A row that did land is left
    alone so a queued or running resume keeps the parent sealed.

    Returns True when no resume row existed and the restore statement
    ran, including a no-op update. Returns False when the resume row
    already landed, and does not release that row's slot.
    """
    restored = await asyncio.to_thread(
        _undo_unadmitted_hand_claim_sync,
        parent_id=parent_id,
        resume_id=resume_id,
        park_child_id=park_child_id,
    )
    if restored:
        await _release_handed_slot(resume_id)
    return restored


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
        request_id=record.get("request_id"),
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


def _retryable_nest_admit_refusal(response: Any) -> bool:
    """True when a failed nest admit should put the parent back to parked.

    HTTP 503 is always retryable: drain, lane pin, and worktree mint.
    HTTP 409 is retryable only when the body names a held write lease.
    Other refusals stay sealed so the sweeper can release that lease.
    """
    status = getattr(response, "status_code", 500)
    if status == 503:
        return True
    if status != 409:
        return False
    raw = getattr(response, "body", None)
    if raw is None:
        raw = getattr(response, "content", None)
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    elif isinstance(raw, str):
        text = raw
    else:
        text = str(raw or "")
    return "CURSOR_WRITE_LEASE_HELD" in text


async def _restore_claimed_parent_on_retryable(
    *,
    claim: ParkResumeClaim,
    parent_id: str,
    resume_id: str,
) -> None:
    """Put a claimed parent back to parked after a retryable nest admit miss.

    The caller clears ``release_handed`` after this returns. The undo
    already drops the handed slot when the resume row never landed, so
    the finally block must not release that slot a second time.
    """
    if claim.outcome != "claimed":
        return
    await undo_unadmitted_hand_claim(
        parent_id=parent_id,
        resume_id=resume_id,
        park_child_id=claim.park_child_id,
    )


async def admit_nest_parent_resume(
    parent_id: str,
    *,
    child_id: str,
    controller: Any,
    cfg: Any,
    bus: Any | None = None,
) -> NestResumeAdmit:
    """Seal the parked parent and admit one ``resume_of`` child.

    The seal is ``claim_parked_parent_resume``. Only the call whose update
    changed the row admits. ``lost`` covers a second resumer, including
    ``already_claimed_by_us``, so the caller must not promote. ``refused``
    is an ineligible or failed admit after this call sealed the row; the
    handed slot is released before return. A retryable failure (HTTP 503,
    HTTP 409 ``CURSOR_WRITE_LEASE_HELD``, or a raised admit) restores the
    parent through ``undo_unadmitted_hand_claim`` instead of leaving it
    cancelled. The child must already be terminal so the work-key peer
    gate sees a finished nest. ``parent_still_live`` is expected while
    the row is parked.
    """
    from services.git_integration_worker.cursor_sdk_resume import (
        resume_eligibility_reason,
    )
    from services.git_integration_worker.routes.cursor_sdk import admit_cursor_dispatch

    ledger = CursorDispatchLedger.instance()
    reason = resume_eligibility_reason(ledger, parent_id=parent_id)
    resume_id = f"{parent_id}-nr1"
    claim = await asyncio.to_thread(
        claim_parked_parent_resume,
        parent_id,
        park_child_id=child_id,
        resume_id=resume_id,
    )
    if claim.outcome != "claimed" and claim.outcome != "not_waiting":
        # already_claimed_by_us is the other resumer, or this resume's admit
        # already owns the row. Only the call that changed the row admits.
        logger.info(
            "nest parent resume not claimed parent=%s outcome=%s",
            parent_id,
            claim.outcome,
        )
        return NestResumeAdmit("lost")
    release_handed = False
    try:
        if claim.outcome == "not_waiting":
            status = ledger.dispatch_status_by_id(dispatch_id=parent_id)
            state = str((status or {}).get("status") or "")
            if state != "running":
                return NestResumeAdmit("refused")
            # Restored live parent: the parked claim does not apply. Seal it
            # before admit so the work-key gate sees a terminal resume_of.
            persist_resume_retain(dispatch_id=parent_id)
            await ledger_mark_cancelled(parent_id)
            ledger.clear_park_child(parent_id=parent_id)
        elif claim.outcome == "claimed":
            await _hand_slot_to_resume(
                parent_id=parent_id,
                child_id=claim.park_child_id,
                resume_id=resume_id,
            )
            release_handed = True
        if reason not in (None, "parent_still_live"):
            logger.warning(
                "nest parent resume ineligible parent=%s reason=%s",
                parent_id,
                reason,
            )
            return NestResumeAdmit("refused")
        req = _resume_request(parent_id, child_id=child_id)
        if req is None:
            return NestResumeAdmit("refused")
        response = await admit_cursor_dispatch(req, cfg=cfg, controller=controller)
        if getattr(response, "status_code", 500) not in (200, 202):
            logger.warning(
                "nest parent resume refused parent=%s child=%s status=%s",
                parent_id,
                req.dispatch_id,
                getattr(response, "status_code", None),
            )
            if _retryable_nest_admit_refusal(response):
                await _restore_claimed_parent_on_retryable(
                    claim=claim,
                    parent_id=parent_id,
                    resume_id=resume_id,
                )
                release_handed = False
            return NestResumeAdmit("refused")
        release_handed = False
        if bus is not None:
            await bus.reply(
                thread_id=req.thread_id,
                to_agent=req.caller_agent or "dispatch",
                from_agent="cursor-sdk",
                subject=nest_resumed_subject(
                    child_id=req.dispatch_id, parent_id=parent_id
                ),
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
        return NestResumeAdmit("admitted")
    except Exception:
        await _restore_claimed_parent_on_retryable(
            claim=claim,
            parent_id=parent_id,
            resume_id=resume_id,
        )
        release_handed = False
        raise
    finally:
        if release_handed:
            await _release_handed_slot(resume_id)


async def ledger_mark_cancelled(parent_id: str) -> None:
    """Mark a restored live parent ``cancelled`` off the event loop.

    Used when the parked-parent claim does not apply because the row is
    already ``running``. The resume admit then sees a terminal parent.
    """
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
