"""HTTP shaping for steer inject — thin relay between FastAPI and deposit ladder."""

from __future__ import annotations

import asyncio
from typing import Any

from universal_protocol import error_envelope

from services.git_integration_worker.cursor_sdk_steer_inject import (
    DEFAULT_TTL_S,
    deposit_steer_directive,
    recover_undelivered_steer_from_thread,
)
from services.git_integration_worker.cursor_sdk_steer_inject_preflight import (
    REFUSAL_HTTP,
    preflight_inject,
)

_SOURCE = "git_integration_worker"


def inject_dispatch_response(
    *,
    dispatch_id: str,
    execution_id: str,
    entry_id: str,
    authority_turn_id: str,
    spool_path: str,
) -> tuple[int, dict[str, Any]]:
    """Map a successful deposit to ``(202, pending handle body)``."""
    return 202, {
        "dispatch_id": dispatch_id,
        "execution_id": execution_id,
        "steer": "inject",
        "inject_state": "pending",
        "entry_id": entry_id,
        "authority_turn_id": authority_turn_id,
        "spool_path": spool_path,
    }


async def inject_one_dispatch(
    *,
    dispatch_id: str,
    directive: str,
    reason: str,
    actor: str,
    ttl_s: int | None,
) -> tuple[int, dict[str, Any]]:
    """Deposit a steer directive on a live dispatch; 202 + pending handle."""
    if not directive.strip():
        return 422, error_envelope(
            code="CURSOR_INJECT_DIRECTIVE_REQUIRED",
            message="directive is required",
            source=_SOURCE,
            retryable=False,
            data={"dispatch_id": dispatch_id},
        )
    pre = preflight_inject(dispatch_id)
    if pre.refusal is not None:
        status, retryable = REFUSAL_HTTP[pre.refusal]
        return status, error_envelope(
            code=f"CURSOR_INJECT_{pre.refusal.value}",
            message=pre.detail or f"inject refused: {pre.refusal.value}",
            source=_SOURCE,
            retryable=retryable,
            data={"dispatch_id": dispatch_id, "refusal": pre.refusal.value},
        )
    assert pre.row is not None
    thread_id = str(pre.row["thread_id"])
    await asyncio.to_thread(
        recover_undelivered_steer_from_thread,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    deposit = await asyncio.to_thread(
        deposit_steer_directive,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        directive=directive,
        reason=reason,
        actor=actor,
        ttl_s=ttl_s if ttl_s is not None else DEFAULT_TTL_S,
    )
    execution_id = str(pre.row.get("execution_id") or dispatch_id)
    return inject_dispatch_response(
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        entry_id=deposit.entry_id,
        authority_turn_id=deposit.authority_turn_id,
        spool_path=deposit.spool_path,
    )


__all__ = ["inject_dispatch_response", "inject_one_dispatch"]
