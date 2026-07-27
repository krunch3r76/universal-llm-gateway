"""Same-thread supersede policy for ``lane:cursor-auto``.

A second ``agent_bus.request`` on a private thread whose previous job is still
in flight is read as a **backtrack**, not as a queue append: the live nested run
is interrupted, its writes are reverted from the admit baseline, and the new
DIRECTIVE carries a notice of what the void episode left behind.

Scope bind: same ``thread_id`` only. Cross-thread contention stays FIFO on the
capacity gate — a request on another thread never interrupts this one.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, AutoJobQueue
from services.git_integration_worker.cursor_bus import CursorBusClient
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


async def supersede_same_thread_inflight(
    new_job: AutoJob, *, queue: AutoJobQueue
) -> dict[str, Any] | None:
    """Interrupt the in-flight job on ``new_job.thread_id``, if there is one.

    Returns interrupt evidence for the enqueue response, or ``None`` when the
    thread is idle and the request is a plain FIFO append.
    """
    old_job = queue.claimed_for_thread(new_job.thread_id)
    if old_job is None or old_job.job_id == new_job.job_id:
        return None
    live = live_run_for_thread(new_job.thread_id)
    reason = f"same_thread_request_turn_{new_job.turn_number}"
    mark: dict[str, Any] = {"method": "queued_only", "reason": reason}
    if live is not None:
        mark = await asyncio.to_thread(
            signal_supersede,
            dispatch_id=live.dispatch_id,
            superseded_by=new_job.job_id,
            reason=reason,
        )
    queue.mark_superseded(old_job.job_id, superseded_by=new_job.job_id)
    new_job.supersedes = old_job.job_id
    new_job.superseded_dispatch_id = live.dispatch_id if live else None
    _PENDING[new_job.job_id] = SupersedeContext(
        superseded_job_id=old_job.job_id,
        superseded_dispatch_id=live.dispatch_id if live else None,
        source_repo=live.source_repo if live else None,
        mark=mark,
    )
    logger.warning(
        "cursor-auto supersede thread=%s superseded_job=%s by_job=%s "
        "dispatch_id=%s method=%s",
        new_job.thread_id,
        old_job.job_id,
        new_job.job_id,
        new_job.superseded_dispatch_id,
        mark.get("method"),
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


async def post_superseded_terminal(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    dispatch_id: str | None,
) -> dict[str, Any]:
    """Close the displaced job as ``status:superseded`` — never success-shaped."""
    summary = (
        f"Episode superseded by a newer same-thread request "
        f"(job {job.superseded_by}); work reverted, no closeout is authoritative."
    )
    payload = {
        "summary": summary,
        "superseded_by_job": job.superseded_by,
        "dispatch_id": dispatch_id,
        "terminal_vocabulary": SUPERSEDED_TERMINAL,
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
