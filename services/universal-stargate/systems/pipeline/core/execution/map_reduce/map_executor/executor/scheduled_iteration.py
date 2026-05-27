"""Per-iteration tracking and concurrency-gated scheduling helpers.

Extracts the two nested closures from the monolith's ``execute()`` body — the
tracking wrapper that stamps completion timestamps and emits the immediate
per-iteration completion event, and the scheduling wrapper that optionally
acquires a ``FifoCapacityGate`` slot before delegating to the tracker. Both
functions are module-level ``async def``s; the previously-closed-over locals
(``total``, ``iteration_context``, ``pool_assignments``, ``gate``) are passed
in as keyword arguments.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from universal_concurrency import FifoCapacityGate

    from .....schemas import StepOutput
    from .map_executor import MapExecutor


async def tracked_iteration(
    executor: MapExecutor,
    idx: int,
    value: object,
    key: str | None,
    *,
    total: int,
    iteration_context: dict[int, dict[str, Any]],
    pool_assignments: dict[int, str],
) -> StepOutput:
    """Execute one iteration and stamp completion telemetry on the context."""
    result = await executor._execute_iteration(
        idx, value, total, key, iteration_context, pool_assignments.get(idx)
    )
    ctx = iteration_context[idx]
    ctx["completed_at"] = time.monotonic()
    elapsed = ctx["completed_at"] - ctx.get("started_at", ctx["completed_at"])
    inference_start = ctx.get("inference_started_at")
    inference_seconds = (
        ctx["completed_at"] - inference_start if inference_start is not None else None
    )
    executor._event_publisher.emit_iteration_completed_immediate(
        index=idx,
        elapsed_seconds=round(elapsed, 3),
        inference_seconds=round(inference_seconds, 3)
        if inference_seconds is not None
        else None,
        prompt_tokens=getattr(result, "prompt_tokens", 0),
        completion_tokens=getattr(result, "completion_tokens", 0),
    )
    return result


async def scheduled_iteration(
    executor: MapExecutor,
    idx: int,
    value: object,
    key: str | None,
    *,
    total: int,
    iteration_context: dict[int, dict[str, Any]],
    pool_assignments: dict[int, str],
    gate: FifoCapacityGate | None,
) -> StepOutput:
    """Optionally acquire a capacity-gate slot, then run the tracked iteration."""
    if gate is None:
        return await tracked_iteration(
            executor,
            idx,
            value,
            key,
            total=total,
            iteration_context=iteration_context,
            pool_assignments=pool_assignments,
        )
    ctx = iteration_context.get(idx, {})
    request_id = ctx.get("request_id", str(idx))
    await gate.acquire(request_id)
    try:
        return await tracked_iteration(
            executor,
            idx,
            value,
            key,
            total=total,
            iteration_context=iteration_context,
            pool_assignments=pool_assignments,
        )
    finally:
        await gate.release()
