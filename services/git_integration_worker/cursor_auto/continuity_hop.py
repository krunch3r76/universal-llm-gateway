"""Concurrent continuity-hop path — CDP successor launch without supersede.

Row 21: a structural ``TYPE: CONTINUITY_HANDOFF`` must not interrupt an
in-flight commission. The Auto worker is serial, so hop jobs with a claimed
incumbent run on a bounded concurrent task: harvest residual → CDP commission
→ terminal. Never calls ``supersede_same_thread_inflight``, never
``submit_nested_dispatch`` / ``nest_under`` of the incumbent write lease.
"""

from __future__ import annotations

import json
from typing import Any

from agent_seat.registry import normalize_bus_address
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.cdp_escalation import (
    commission_cdp_escalation,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, AutoJobQueue
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_sdk_supersede import live_run_for_thread

logger = get_logger(__name__)

_FROM_AUTO = "cursor-auto"
_DEFAULT_CDP_MODEL = "cdp/opus-5"


def _hop_cdp_model(job: AutoJob) -> str:
    desired = (job.desired_model or "").strip()
    if desired.startswith("cdp/"):
        return desired
    return _DEFAULT_CDP_MODEL


async def post_harvest_residual(
    job: AutoJob,
    *,
    client: CursorBusClient,
    incumbent: AutoJob,
    dispatch_id: str | None,
) -> dict[str, Any]:
    """Name the live commission so the successor has a harvest target."""
    mailbox = normalize_bus_address(job.from_agent)
    payload = {
        "type": "CONTINUITY_HARVEST_RESIDUAL",
        "incumbent_job_id": incumbent.job_id,
        "incumbent_dispatch_id": dispatch_id,
        "incumbent_subject": incumbent.subject,
        "hop_job_id": job.job_id,
        "hop_matched_token": job.continuity_matched_token,
        "re_issue_subject": incumbent.subject,
        "note": (
            "In-flight commission on this lane was preserved (hop≠backtrack). "
            "Harvest its CLOSEOUT; do not treat it as superseded."
        ),
    }
    reply = await client.reply(
        thread_id=job.thread_id,
        to_agent=mailbox,
        from_agent=_FROM_AUTO,
        subject=f"continuity harvest residual — {incumbent.job_id[:12]}",
        body=json.dumps(payload, indent=2),
        allow_long_body=True,
    )
    return {
        "ok": reply.status_code < 400,
        "status_code": reply.status_code,
        "to_agent": mailbox,
        "payload": payload,
    }


async def run_continuity_hop_concurrent(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    incumbent: AutoJob,
) -> dict[str, Any]:
    """Claim hop, post residual, commission CDP, terminalize — leave incumbent."""
    claimed = queue.claim_job(job.job_id)
    if claimed is None:
        logger.warning(
            "continuity hop concurrent skip job=%s — not queued",
            job.job_id,
        )
        return {"ok": False, "reason": "hop_not_queued"}

    client = CursorBusClient()
    live = live_run_for_thread(job.thread_id)
    dispatch_id = live.dispatch_id if live else None
    residual = await post_harvest_residual(
        claimed,
        client=client,
        incumbent=incumbent,
        dispatch_id=dispatch_id,
    )
    model = _hop_cdp_model(claimed)
    commissioned = await commission_cdp_escalation(
        claimed,
        model=model,
        purpose="operator-proxy",
    )
    if not commissioned.get("ok"):
        return await post_terminal_status(
            claimed,
            client=client,
            queue=queue,
            summary=(
                "continuity hop CDP commission failed: "
                f"{commissioned.get('error')}"
            ),
            disposition="failed",
            contract=claimed.contract,
            terminal_status="status:failed",
            failed=True,
            payload={
                "summary": "continuity hop CDP commission failed",
                "continuity_hop": True,
                "matched_token": claimed.continuity_matched_token,
                "harvest_residual": residual,
                "commission": commissioned,
            },
        )
    return await post_terminal_status(
        claimed,
        client=client,
        queue=queue,
        summary=f"continuity hop CDP commissioned model={model}",
        disposition="dispatched-and-relayed",
        contract=claimed.contract,
        terminal_status="status:done",
        payload={
            "summary": f"continuity hop CDP commissioned model={model}",
            "reason": "continuity_hop_cdp_commissioned",
            "continuity_hop": True,
            "matched_token": claimed.continuity_matched_token,
            "harvest_residual": residual,
            "execution_id": commissioned.get("execution_id"),
            "incumbent_job_id": incumbent.job_id,
            "incumbent_dispatch_id": dispatch_id,
        },
    )
