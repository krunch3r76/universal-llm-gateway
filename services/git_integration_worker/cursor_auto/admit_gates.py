"""Pre-nest admit gates — relay trust and unacknowledged synthesized closeouts.

Both gates refuse the job before any nested SDK capacity is spent, so a thread
whose history cannot be verified never reaches ``submit_nested_dispatch``.
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_auto.episode_briefing import (
    fetch_thread_turns,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.relay_trust import (
    pending_synthesized_closeout,
)
from services.git_integration_worker.cursor_bus import CursorBusClient


async def blocking_admit_gate(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
) -> dict[str, Any] | None:
    """Return a terminal ``status:blocked`` result when an admit gate refuses.

    ``None`` means both gates passed and the caller may continue to nest.
    """
    turns = await fetch_thread_turns(job.thread_id)
    if turns is None:
        summary = (
            "Relay trust gate cannot verify thread history "
            "(relay_trust_unverifiable)."
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={"summary": summary, "relay_trust_unverifiable": True},
        )
    pending = pending_synthesized_closeout(turns, operator_from=job.from_agent)
    if pending:
        summary = (
            f"Synthesized closeout {pending} awaits operator ack "
            "(synthesized_closeout_ack: <dispatch_id>)."
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={"summary": summary, "pending_synthesized_closeout": pending},
        )
    return None


async def _blocked(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    summary: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="blocked",
        contract=job.contract,
        terminal_status="status:blocked",
        payload=payload,
        failed=True,
    )
