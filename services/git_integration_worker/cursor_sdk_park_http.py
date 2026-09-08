"""HTTP shaping for the park routes — thin relay between FastAPI and the park ladder.

``routes/cursor_sdk.py`` stays a call site: it parses the body and hands the
admission controller here. Refusals render as the standard error envelope
(``{code, message, source, retryable, data}``) with the D3 status; a requested
park is 202 (the row is still ``running`` until its worker thread unwinds), an
idempotent re-park of an already-parked row is 200.
"""

from __future__ import annotations

import asyncio
from typing import Any

from universal_protocol import error_envelope

from services.git_integration_worker.cursor_sdk_park_converge import (
    converge_bridges_after_park,
)
from services.git_integration_worker.cursor_sdk_park_for_restart import (
    ParkSignalResult,
    signal_park,
)
from services.git_integration_worker.cursor_sdk_park_preflight import (
    REFUSAL_HTTP,
    ParkRefusal,
)
from services.git_integration_worker.cursor_sdk_park_sweep import (
    ParkSweepSummary,
    park_for_restart_sweep,
)

_SOURCE = "git_integration_worker"


def park_dispatch_response(result: ParkSignalResult) -> tuple[int, dict[str, Any]]:
    """Map one ``signal_park`` outcome to ``(status_code, body)``."""
    if result.requested:
        return 202, result.as_dict()
    assert result.refusal is not None
    if result.refusal is ParkRefusal.ALREADY_TERMINAL and result.already_parked:
        return 200, result.as_dict()
    status, retryable = REFUSAL_HTTP[result.refusal]
    return status, error_envelope(
        code=f"CURSOR_PARK_{result.refusal.value}",
        message=result.error or f"park refused: {result.refusal.value}",
        source=_SOURCE,
        retryable=retryable,
        data=result.as_dict(),
    )


async def park_one_dispatch(
    *,
    dispatch_id: str,
    intent_id: str | None,
    drain_epoch: int | None,
    actor: str,
    reason: str,
    controller: Any,
) -> tuple[int, dict[str, Any]]:
    """Park a single live dispatch; on success start bridge-close convergence."""
    result = await asyncio.to_thread(
        signal_park,
        dispatch_id,
        intent_id=intent_id,
        drain_epoch=drain_epoch,
        actor=actor,
        reason=reason,
    )
    if result.requested:
        controller.create_tracked_task(
            converge_bridges_after_park(
                parked_ids=[dispatch_id],
                intent_id=intent_id,
                controller=controller,
            ),
            op_id=f"park-converge:{dispatch_id}",
        )
    return park_dispatch_response(result)


async def park_for_restart(
    *,
    intent_id: str,
    drain_epoch: int | None,
    actor: str,
    reason: str,
    controller: Any,
) -> tuple[int, dict[str, Any]]:
    """Sweep every live dispatch for an intent; converge bridges in the background."""
    summary: ParkSweepSummary = await asyncio.to_thread(
        park_for_restart_sweep,
        intent_id=intent_id,
        drain_epoch=drain_epoch,
        actor=actor,
        reason=reason,
    )
    if summary.requested:
        controller.create_tracked_task(
            converge_bridges_after_park(
                parked_ids=list(summary.requested),
                intent_id=intent_id,
                controller=controller,
            ),
            op_id=f"park-converge:{intent_id}",
        )
    else:
        # Nothing to wait on — an idle worker still owes the drain event.
        controller.recheck_drain_idle()
    return 202, summary.as_dict()


__all__ = ["park_dispatch_response", "park_for_restart", "park_one_dispatch"]
