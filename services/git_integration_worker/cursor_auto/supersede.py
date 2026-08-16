"""Same-thread supersede policy for ``lane:cursor-auto``.

A second ``agent_bus.request`` on a private thread whose previous job is still
in flight **or still queued** is read as a **backtrack**, not as a queue append:
the live nested run is interrupted (or a queued predecessor is withdrawn), its
writes are reverted from the admit baseline when applicable, and the new
DIRECTIVE carries a notice of what the void episode left behind.

Queued predecessors use method token ``queue_withdraw`` (≠ ``queued_only`` or
``pre_register_live_run``). The loser receives ``status:superseded`` on the bus
before it can be claimed — ``mark_superseded`` alone is insufficient.

**Exception (row 21):** a structural continuity hop
(``TYPE: CONTINUITY_HANDOFF`` / wire ``continuity_hop=true``) is **not** a
backtrack — enqueue skips this module and runs the concurrent CDP hop path
(``continuity_hop.py``). Ordinary unlabeled second requests keep supersede.

Scope bind: same ``thread_id`` only. Cross-thread contention stays FIFO on the
capacity gate — a request on another thread never interrupts this one.
Contract is irrelevant to the candidate predicate.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claim_register import claimed_derived
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, AutoJobQueue
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_sdk_cancel_events import (
    emit_sdk_worker_cancelled,
)
from services.git_integration_worker.cursor_sdk_revert import (
    RevertReport,
    revert_dispatch_writes,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    escalate_supersede_abort,
    is_dispatch_live,
    live_run_for_thread,
    signal_supersede,
)

logger = get_logger(__name__)

SUPERSEDED_TERMINAL = "status:superseded"
_RELEASE_GRACE_S = 20.0
_ABORT_GRACE_S = 10.0
_POLL_S = 0.5


@dataclass(frozen=True)
class SupersedeContext:
    """What the superseding job must settle before it may run."""

    superseded_job_id: str
    superseded_dispatch_id: str | None
    source_repo: str | None
    mark: dict[str, Any]


_PENDING: dict[str, SupersedeContext] = {}

# Honesty token for claimed ∧ ¬register_live_run: names the window, does not
# claim process stop. Distinct from ``queued_only`` (pre-mint cancel vocabulary).
PRE_REGISTER_LIVE_RUN = "pre_register_live_run"
# Queued predecessor retract — distinct from ``queued_only`` and pre-live tokens.
QUEUE_WITHDRAW = "queue_withdraw"


def _bound_auto_dispatch_id(job_id: str) -> str | None:
    """Return bound ``auto-*`` for *job_id* when the job ledger has one."""
    try:
        from services.git_integration_worker.cursor_auto.job_ledger import (
            get_ledger,
        )

        state = get_ledger().read_relay_state(job_id)
    except Exception:  # noqa: BLE001 — ledger optional in durable=False tests
        return None
    dispatch_id = state.get("dispatch_id")
    if isinstance(dispatch_id, str) and dispatch_id.startswith("auto-"):
        return dispatch_id
    return None


async def supersede_same_thread_inflight(
    new_job: AutoJob,
    *,
    queue: AutoJobQueue,
    client: CursorBusClient | None = None,
) -> dict[str, Any] | None:
    """Interrupt or withdraw the predecessor on ``new_job.thread_id``, if any.

    Returns interrupt evidence for the enqueue response, or ``None`` when the
    thread is idle and the request is a plain FIFO append.

    Candidate resolution prefers a claimed in-flight job, else the oldest queued
    peer on the thread. Queued withdraw emits ``queue_withdraw`` and posts
    ``status:superseded`` immediately so the loser is observable before
    ``claim_next`` could ever run it.

    When ``live_run_for_thread`` is ``None`` on a **claimed** candidate, the job
    may be never-submitted **or** already past ``bind_dispatch``/POST without
    ``register_live_run``. That probe cannot license ``queued_only`` +
    ``terminal_status=cancelled``.

    Mission negotiation turns and open negotiation ledger rows skip supersede.
    """
    from services.git_integration_worker.cursor_auto.directive import (
        is_mission_negotiation_directive,
    )
    from services.git_integration_worker.cursor_auto.mission_negotiation_ledger import (
        get_negotiation_ledger,
    )

    if is_mission_negotiation_directive(new_job.body):
        return None
    if get_negotiation_ledger().open_on_thread(new_job.thread_id) is not None:
        return None
    old_job = queue.supersede_candidate_for_thread(new_job.thread_id)
    if old_job is None or old_job.job_id == new_job.job_id:
        return None
    if is_mission_negotiation_directive(old_job.body):
        return None
    reason = f"same_thread_request_turn_{new_job.turn_number}"
    superseded_dispatch_id: str | None = None
    source_repo: str | None = None
    was_queued = old_job.status == "queued"
    if was_queued:
        mark = {"method": QUEUE_WITHDRAW, "reason": reason}
        emit_sdk_worker_cancelled(
            dispatch_id=old_job.job_id,
            method=QUEUE_WITHDRAW,
            reason=reason,
            thread_id=new_job.thread_id,
            superseded_by=new_job.job_id,
            terminal_status="displaced_queued",
        )
        queue.mark_superseded(old_job.job_id, superseded_by=new_job.job_id)
        new_job.supersedes = old_job.job_id
        new_job.superseded_dispatch_id = None
        _PENDING[new_job.job_id] = SupersedeContext(
            superseded_job_id=old_job.job_id,
            superseded_dispatch_id=None,
            source_repo=None,
            mark=mark,
        )
        bus = client or CursorBusClient()
        from services.git_integration_worker.cursor_auto.superseded_seat_notify import (
            notify_superseded_seat,
        )

        await notify_superseded_seat(
            old_job,
            new_job,
            mark=mark,
            client=bus,
            queue=queue,
            dispatch_id=None,
            post_bus_terminal=True,
        )
    else:
        live = live_run_for_thread(new_job.thread_id)
        if live is not None:
            mark = await asyncio.to_thread(
                signal_supersede,
                dispatch_id=live.dispatch_id,
                superseded_by=new_job.job_id,
                reason=reason,
            )
            superseded_dispatch_id = live.dispatch_id
            source_repo = live.source_repo
        else:
            # Pre-CancelRun window — mark displacement honestly; do not claim stop.
            bound_dispatch = _bound_auto_dispatch_id(old_job.job_id)
            mark = {"method": PRE_REGISTER_LIVE_RUN, "reason": reason}
            if bound_dispatch is not None:
                mark["dispatch_id"] = bound_dispatch
            emit_sdk_worker_cancelled(
                dispatch_id=bound_dispatch or old_job.job_id,
                method=PRE_REGISTER_LIVE_RUN,
                reason=reason,
                thread_id=new_job.thread_id,
                superseded_by=new_job.job_id,
            )
            superseded_dispatch_id = bound_dispatch
        queue.mark_superseded(old_job.job_id, superseded_by=new_job.job_id)
        new_job.supersedes = old_job.job_id
        new_job.superseded_dispatch_id = superseded_dispatch_id
        _PENDING[new_job.job_id] = SupersedeContext(
            superseded_job_id=old_job.job_id,
            superseded_dispatch_id=superseded_dispatch_id,
            source_repo=source_repo,
            mark=mark,
        )
        bus = client or CursorBusClient()
        from services.git_integration_worker.cursor_auto.superseded_seat_notify import (
            notify_superseded_seat,
        )

        await notify_superseded_seat(
            old_job,
            new_job,
            mark=mark,
            client=bus,
            queue=queue,
            dispatch_id=superseded_dispatch_id,
            post_bus_terminal=live is None,
        )
    logger.warning(
        "cursor-auto supersede thread=%s superseded_job=%s by_job=%s "
        "dispatch_id=%s method=%s queued=%s",
        new_job.thread_id,
        old_job.job_id,
        new_job.job_id,
        new_job.superseded_dispatch_id,
        mark.get("method"),
        was_queued,
    )
    return {
        "superseded_job_id": old_job.job_id,
        "superseded_dispatch_id": new_job.superseded_dispatch_id,
        **mark,
    }


async def settle_supersede(job: AutoJob) -> dict[str, Any] | None:
    """Wait for the interrupted run to release, then revert its writes.

    Runs on the superseding job before it submits its own nested dispatch, so
    the new episode starts from a tree the void episode no longer owns.
    """
    context = _PENDING.pop(job.job_id, None)
    if context is None:
        return None
    dispatch_id = context.superseded_dispatch_id
    if dispatch_id is None:
        return {"mark": context.mark, "revert": None, "released": True}
    released = await _await_release(dispatch_id, _RELEASE_GRACE_S)
    if not released:
        await asyncio.to_thread(escalate_supersede_abort, dispatch_id=dispatch_id)
        released = await _await_release(dispatch_id, _ABORT_GRACE_S)
    report: RevertReport | None = None
    if context.source_repo:
        report = await asyncio.to_thread(
            revert_dispatch_writes,
            dispatch_id=dispatch_id,
            source_repo=Path(context.source_repo),
        )
        await asyncio.to_thread(
            _mark_lane_b_supersede_disposition,
            dispatch_id=dispatch_id,
            superseded_by=job.job_id,
            source_repo=context.source_repo,
        )
    return {
        "mark": context.mark,
        "released": released,
        "revert": report.as_dict() if report is not None else None,
    }


async def _await_release(dispatch_id: str, grace_s: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + grace_s
    while loop.time() < deadline:
        if not is_dispatch_live(dispatch_id=dispatch_id):
            return True
        await asyncio.sleep(_POLL_S)
    return not is_dispatch_live(dispatch_id=dispatch_id)


def _mark_lane_b_supersede_disposition(
    *,
    dispatch_id: str,
    superseded_by: str,
    source_repo: str,
) -> None:
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        mark_lane_b_disposition_for_dispatch,
    )

    mark_lane_b_disposition_for_dispatch(
        dispatch_id=dispatch_id,
        source_repo=Path(source_repo),
        reason=f"superseded_by:{superseded_by}",
    )


def compose_supersede_preamble(settlement: dict[str, Any]) -> str:
    """Build the notice prepended to the superseding job's SDK message."""
    revert = settlement.get("revert") or {}
    dispatch_id = revert.get("dispatch_id") or settlement.get("mark", {}).get(
        "dispatch_id"
    )
    lines = [
        "=== SUPERSEDE NOTICE (substrate-generated) ===",
        (
            f"A newer DIRECTIVE on this thread superseded dispatch {dispatch_id}. "
            "That episode is void: do not resume it, do not report on it."
        ),
    ]
    if not revert:
        lines.append(
            "Substrate revert did not run (no baseline-owning dispatch was live). "
            "Verify the tree yourself before new writes."
        )
    else:
        lines.append(
            "Substrate revert of the void episode's git-tracked writes: "
            f"ok={revert.get('ok')}"
        )
        for label, key in (
            ("restored to HEAD", "restored"),
            ("created by the void episode and NOT deleted", "created_left"),
            ("could not be reverted automatically", "unrevertable"),
        ):
            paths = revert.get(key) or []
            if paths:
                lines.append(f"  {label}: {', '.join(paths[:20])}")
        if not revert.get("ok"):
            lines.append(
                f"  revert INCOMPLETE (reason={revert.get('reason')}) — finish the "
                "revert of the void episode's writes before starting new work."
            )
        if revert.get("created_left"):
            lines.append(
                "  Untracked paths are never auto-deleted in a shared checkout; "
                "remove only the ones the void episode created."
            )
    lines.append("Treat the DIRECTIVE below as the only live instruction.")
    lines.append("=== END SUPERSEDE NOTICE ===")
    return "\n".join(lines)


def superseded_terminal_summary(
    *,
    superseded_by: str | None,
    dispatch_id: str | None,
) -> tuple[str, str]:
    """Build observation-keyed superseded summary + revert disposition token.

    Interrupt runs before successor ``settle_supersede``, so this surface must
    never claim a completed revert (commits survive ``revert_dispatch_writes``).
    """
    if dispatch_id:
        disposition = "revert-pending"
        clause = (
            "episode void; revert-pending (successor settle reports tree)"
        )
    else:
        disposition = "revert-skipped"
        clause = "episode void; revert-skipped"
    summary = (
        f"Episode superseded by a newer same-thread request "
        f"(job {superseded_by}); {clause}; no closeout is authoritative."
    )
    return summary, disposition


async def post_superseded_terminal(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    dispatch_id: str | None,
) -> dict[str, Any]:
    """Close the displaced job as ``status:superseded`` — never success-shaped."""
    summary, revert_disposition = superseded_terminal_summary(
        superseded_by=job.superseded_by,
        dispatch_id=dispatch_id,
    )
    # Member 3: dispositional summary is derived counsel (interrupt never
    # observed a completed revert). Keep bare summary for replay/consumers;
    # type travels on additive claim_register (wire-compatible via
    # post_terminal_status — no schema strip). render_claim on summary would
    # rewrite the falsifier sentence — rejected this packet.
    summary_claim = claimed_derived(
        summary,
        basis="supersede.dispositional_summary",
    )
    re_issue_subject = None
    if job.superseded_by:
        newer = queue.get(job.superseded_by) if hasattr(queue, "get") else None
        if newer is not None:
            re_issue_subject = newer.subject
    payload = {
        "summary": summary,
        "superseded_by_job": job.superseded_by,
        "dispatch_id": dispatch_id,
        "re_issue_subject": re_issue_subject or "(unknown)",
        "terminal_vocabulary": SUPERSEDED_TERMINAL,
        "revert_disposition": revert_disposition,
        "claim_register": summary_claim.to_wire(),
    }
    logger.warning(
        "cursor-auto job=%s terminal=%s dispatch_id=%s",
        job.job_id,
        SUPERSEDED_TERMINAL,
        dispatch_id,
    )
    result = await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="superseded",
        contract=job.contract,
        terminal_status=SUPERSEDED_TERMINAL,
        payload=payload,
        failed=True,
        dispatch_id=dispatch_id,
    )
    return {
        **result,
        "phase": "superseded",
        "dispatch_id": dispatch_id,
        "payload": json.loads(json.dumps(payload)),
    }
