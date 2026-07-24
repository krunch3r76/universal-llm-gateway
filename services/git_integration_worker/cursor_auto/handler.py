"""Auto job processor — admit → classify → terminal status reply.

v0 unattended allowlist: answer/verify/investigate in-seat; implement without
Gate-2 → ``needs-attended``. Nested SDK dispatch is planned via gate_serialize
but not fired while the holder occupies limit=1.
"""

from __future__ import annotations

import json
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.gate_serialize import (
    plan_nested_dispatch,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, get_queue
from services.git_integration_worker.cursor_auto.wire_map import (
    resolve_contract_disposition,
    resolve_desired_effort,
    resolve_desired_model,
)
from services.git_integration_worker.cursor_bus import CursorBusClient

logger = get_logger(__name__)

_NEEDS_ATTENDED_CONTRACTS = frozenset({"implement"})
_FROM_AUTO = "cursor-auto"


async def process_job(
    job: AutoJob,
    *,
    bus: CursorBusClient | None = None,
) -> dict[str, Any]:
    """Process one Auto job: status:admitted → work → terminal status turn."""
    client = bus or CursorBusClient()
    queue = get_queue()
    model = resolve_desired_model(job.desired_model, contract=job.contract)
    effort = resolve_desired_effort(job.desired_effort)
    contract_info = resolve_contract_disposition(job.contract)
    gate_plan = plan_nested_dispatch(work_bounded=job.contract != "investigate")

    admit = await client.reply(
        thread_id=job.thread_id,
        to_agent=job.from_agent,
        from_agent=_FROM_AUTO,
        subject=f"status:admitted — {job.subject[:80]}",
        body=(
            "Auto admitted lane:cursor-auto request.\n"
            f"requested_model={model['requested']} "
            f"resolved={model['resolved_model_id']}\n"
            f"requested_effort={effort['requested']} "
            f"resolved={effort['resolved_effort']}\n"
            f"contract={contract_info['contract']}\n"
            f"gate_plan={gate_plan['action']}"
        ),
    )
    if admit.status_code >= 400:
        queue.mark_done(job.job_id, failed=True)
        return {
            "ok": False,
            "phase": "admit",
            "status_code": admit.status_code,
            "body": admit.body,
        }

    if job.contract in _NEEDS_ATTENDED_CONTRACTS:
        terminal_status = "status:needs-attended"
        disposition = "needs-attended"
        summary = (
            "Implement contract requires Gate-2/implement_ready — "
            "Auto will not skip skeptic/Gate-2 unattended."
        )
    else:
        terminal_status = "status:done"
        disposition = str(contract_info["disposition_hint"])
        summary = (
            f"v0 in-seat Auto handled contract={job.contract} "
            f"(gate_plan={gate_plan['action']}; nested SDK not fired under limit=1)."
        )

    reply_body = {
        "summary": summary,
        "disposition": disposition,
        "requested_model": model["requested"],
        "actual_model": model["resolved_model_id"],
        "requested_effort": effort["requested"],
        "actual_effort": effort["resolved_effort"],
        "gate_plan": gate_plan,
        "request_turn": job.turn_number,
    }
    terminal = await client.reply(
        thread_id=job.thread_id,
        to_agent=job.from_agent,
        from_agent=_FROM_AUTO,
        subject=f"{terminal_status} — {job.subject[:60]}",
        body=json.dumps(reply_body, indent=2),
        allow_long_body=True,
    )
    failed = terminal.status_code >= 400
    queue.mark_done(job.job_id, failed=failed)
    return {
        "ok": not failed,
        "phase": "terminal",
        "terminal_status": terminal_status,
        "disposition": disposition,
        "status_code": terminal.status_code,
        "model": model,
        "effort": effort,
        "gate_plan": gate_plan,
    }
