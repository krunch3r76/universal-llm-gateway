"""Fail-closed finalize when CDP generate has no deliverable reply mailbox."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from .cdp_events import CdpGenerateStalled, publish_cdp_kwargs
from .cdp_generate_inflight_ledger import mark_proof_emitted, try_claim_proof_publish

FinalizeVia = Literal["worker", "reconcile", "attest"]

CALLER_MISSING_REASON = "cdp_caller_agent_missing"


TerminateDispatchLink = Callable[..., Awaitable[bool | None]]


async def finalize_caller_agent_missing(
    *,
    result: CdpGenerateResult,
    request_id: str,
    thread_id: str,
    pointer_turn: int,
    via: FinalizeVia,
    terminate_dispatch_link: TerminateDispatchLink | None = None,
) -> None:
    """Publish stall once, mark proof, and relay terminal failed (no delivery retry)."""
    holder = f"{via}:{uuid.uuid4().hex[:8]}"
    if not try_claim_proof_publish(execution_id=result.execution_id, holder=holder):
        return
    if terminate_dispatch_link is not None:
        await terminate_dispatch_link(terminal_status="failed")
    sat_id = result.satellite_execution_id
    publish_cdp_kwargs(
        CdpGenerateStalled,
        request_id=request_id,
        execution_id=result.execution_id,
        satellite_execution_id=sat_id,
        stall_stage=CALLER_MISSING_REASON,
        error=CALLER_MISSING_REASON,
        thread_id=thread_id,
        pointer_turn=pointer_turn,
    )
    mark_proof_emitted(result.execution_id)
    from .cdp_generate_reconcile import _drop_registry_heartbeat_entry

    _drop_registry_heartbeat_entry(result.execution_id)
    from .work_registry_admit_relay import relay_registry_terminal

    await relay_registry_terminal(
        request_id=request_id,
        dispatch_id=result.execution_id,
        terminal_status="failed",
        reason=CALLER_MISSING_REASON,
        harvest_chars=len(str(result.body or "")),
    )
