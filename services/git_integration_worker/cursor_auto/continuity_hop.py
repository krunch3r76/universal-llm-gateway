"""Continuity-hop path — CDP successor launch without supersede or implement admit.

Row 21: a structural ``TYPE: CONTINUITY_HANDOFF`` must not interrupt an
in-flight commission. F5: classification alone is insufficient — the hop must
reach CDP commission **before** contract grading / vision-scope admit gates.
A handoff has no ACs; routing it as ``contract: implement`` is a category error.
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
    incumbent: AutoJob | None,
    dispatch_id: str | None,
) -> dict[str, Any]:
    """Name any live commission so the successor has a harvest target."""
    mailbox = normalize_bus_address(job.from_agent)
    if incumbent is None:
        payload = {
            "type": "CONTINUITY_HARVEST_RESIDUAL",
            "incumbent_job_id": None,
            "incumbent_dispatch_id": dispatch_id,
            "incumbent_subject": None,
            "hop_job_id": job.job_id,
            "hop_matched_token": job.continuity_matched_token,
            "re_issue_subject": None,
            "note": (
                "No claimed Auto commission on this lane at hop time. "
                "CDP successor still commissioned; harvest any non-Auto "
                "in-flight work from the lane tip."
            ),
        }
    else:
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
        subject=(
            f"continuity harvest residual — "
            f"{(incumbent.job_id if incumbent else job.job_id)[:12]}"
        ),
        body=json.dumps(payload, indent=2),
        allow_long_body=True,
    )
    return {
        "ok": reply.status_code < 400,
        "status_code": reply.status_code,
        "to_agent": mailbox,
        "payload": payload,
    }


async def complete_continuity_hop(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    incumbent: AutoJob | None = None,
    client: CursorBusClient | None = None,
) -> dict[str, Any]:
    """Harvest residual → CDP commission → terminal (job already claimed).

    Called from the concurrent enqueue task after ``claim_job``, or from
    ``process_job`` when the serial worker won the claim race — never runs
    ``effective_contract`` / admit gates.
    """
    bus = client or CursorBusClient()
    live = live_run_for_thread(job.thread_id)
    dispatch_id = live.dispatch_id if live else None
    residual = await post_harvest_residual(
        job,
        client=bus,
        incumbent=incumbent,
        dispatch_id=dispatch_id,
    )
    model = _hop_cdp_model(job)
    commissioned = await commission_cdp_escalation(
        job,
        model=model,
        purpose="operator-proxy",
        mission_kind="hop",
        parent_thread=str(job.thread_id),
    )
    if not commissioned.get("ok"):
        terminal = await post_terminal_status(
            job,
            client=bus,
            queue=queue,
            summary=(
                "continuity hop CDP commission failed: "
                f"{commissioned.get('error')}"
            ),
            disposition="failed",
            contract=job.contract,
            terminal_status="status:failed",
            failed=True,
            payload={
                "summary": "continuity hop CDP commission failed",
                "continuity_hop": True,
                "matched_token": job.continuity_matched_token,
                "harvest_residual": residual,
                "commission": commissioned,
            },
        )
        return terminal
    execution_id = commissioned.get("execution_id")
    terminal = await post_terminal_status(
        job,
        client=bus,
        queue=queue,
        summary=f"continuity hop CDP commissioned model={model}",
        disposition="dispatched-and-relayed",
        contract=job.contract,
        terminal_status="status:done",
        payload={
            "summary": f"continuity hop CDP commissioned model={model}",
            "reason": "continuity_hop_cdp_commissioned",
            "continuity_hop": True,
            "matched_token": job.continuity_matched_token,
            "harvest_residual": residual,
            "execution_id": execution_id,
            "incumbent_job_id": incumbent.job_id if incumbent else None,
            "incumbent_dispatch_id": dispatch_id,
        },
    )
    if execution_id and not terminal.get("execution_id"):
        terminal = {**terminal, "execution_id": execution_id}
    return terminal


async def run_continuity_hop_concurrent(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    incumbent: AutoJob | None = None,
) -> dict[str, Any]:
    """Claim hop (if still queued), then commission CDP — leave any incumbent."""
    claimed = queue.claim_job(job.job_id)
    if claimed is None:
        logger.warning(
            "continuity hop concurrent skip job=%s — not queued",
            job.job_id,
        )
        return {"ok": False, "reason": "hop_not_queued"}
    return await complete_continuity_hop(
        claimed,
        queue=queue,
        incumbent=incumbent,
    )
