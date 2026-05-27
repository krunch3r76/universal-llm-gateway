"""Top-level orchestration for ``MapExecutor.execute()``.

Owns the empty-iteration early return, the step-started / step-completed event
boundary, the optional ``FifoCapacityGate`` construction from the auto-derived
max-concurrency, the three-way mode dispatch (fail-fast, timeout-with-inference,
strict ``asyncio.gather``), the ``finally``-block tracker tear-down with a
single-yield ``await asyncio.sleep(0)`` so in-flight event callbacks can stamp
context before subscription teardown, and the final ``MapOutputCollection``
assembly with queue-wait / processing-time accounting.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from universal_concurrency import FifoCapacityGate
from universal_logging import get_logger

from ...map_output_collection import MapOutputCollection
from .scheduled_iteration import scheduled_iteration

if TYPE_CHECKING:
    from .map_executor import MapExecutor

logger = get_logger(__name__)


async def execute_map(executor: MapExecutor) -> MapOutputCollection:
    """
    Execute map step.

    Returns MapOutputCollection for wildcard access.
    Uses partial success pattern if timeout/threshold configured.
    """
    if not executor._map_config:
        raise ValueError(f"Step '{executor._step.name}' missing map_config")

    start_time = time.monotonic()

    iteration_items = executor._iteration_preparer.resolve_map_over(
        executor._map_config.map_over
    )
    total = len(iteration_items)

    if total == 0:
        executor._event_publisher.emit_empty_iterations()
        logger.warning(
            "[%s] Map step has 0 iterations (empty map_over collection). "
            "Returning empty MapOutputCollection.",
            executor._step.name,
        )
        return MapOutputCollection([], keys=[])

    executor._event_publisher.emit_step_started(
        total=total,
        timeout_seconds=executor._map_config.timeout_seconds,
        threshold=executor._map_config.min_success_threshold,
    )

    logger.info(
        "[%s] Map step: %d iterations "
        "(timeout=%s, threshold=%s, fail_fast=%s, max_concurrency=%s)",
        executor._step.name,
        total,
        executor._map_config.timeout_seconds,
        executor._map_config.min_success_threshold,
        executor._map_config.fail_fast,
        executor._map_config.max_concurrency,
    )

    pool_assignments = await executor._iteration_preparer.build_pool_assignments(
        iteration_items
    )

    # Auto-derive concurrency cap from live model capacity when not set in config.
    # Prevents flooding the CapacityPool queue beyond what the cluster can absorb.
    derived_max_concurrency = executor._map_config.max_concurrency
    if derived_max_concurrency is None and pool_assignments:
        derived_max_concurrency = executor._derive_model_capacity(pool_assignments)
        if derived_max_concurrency is not None:
            logger.info(
                "[%s] Auto-derived max_concurrency=%d from model capacity",
                executor._step.name,
                derived_max_concurrency,
            )

    iteration_context = executor._build_iteration_context(
        iteration_items=iteration_items,
        pool_assignments=pool_assignments,
        total=total,
    )

    # Subscribe to both boundaries with primary-preferred stamping semantics.
    inference_boundary_tracker = executor._subscribe_inference_start(iteration_context)

    iteration_metadata = [(idx, key) for idx, _, key in iteration_items]

    gate = (
        FifoCapacityGate(
            derived_max_concurrency,
            gate_id=f"map:{executor._step.name}",
        )
        if derived_max_concurrency is not None
        else None
    )

    tasks = {
        asyncio.create_task(
            scheduled_iteration(
                executor,
                idx,
                value,
                key,
                total=total,
                iteration_context=iteration_context,
                pool_assignments=pool_assignments,
                gate=gate,
            )
        ): idx
        for idx, value, key in iteration_items
    }

    try:
        strict_output_keys = [key for _, _, key in iteration_items]
        has_timeout_constraints = (
            executor._map_config.timeout_seconds is not None
            or executor._map_config.inference_timeout_seconds is not None
        )
        if executor._map_config.fail_fast:
            (
                outputs,
                output_keys,
                output_positions,
            ) = await executor._execution_modes.execute_with_fail_fast(
                tasks,
                total,
                executor._map_config.min_success_threshold,
                iteration_metadata,
                iteration_context,
            )
        elif has_timeout_constraints:
            outer_timeout = executor._map_config.timeout_seconds or 3600.0
            (
                outputs,
                output_keys,
                output_positions,
            ) = await executor._execution_modes.execute_with_timeout(
                tasks,
                total,
                outer_timeout,
                executor._map_config.min_success_threshold,
                iteration_metadata,
                iteration_context,
                inference_timeout_seconds=(
                    executor._map_config.inference_timeout_seconds
                ),
            )
        else:
            # Strict mode is intentionally fail-fast.
            # gather() preserves order and raises on first task failure.
            outputs = await asyncio.gather(*tasks.keys())
            output_keys = strict_output_keys
            output_positions = list(range(total))
    finally:
        # Yield once so in-flight event callbacks can stamp context before we
        # tear down subscriptions at execution boundary.
        await asyncio.sleep(0)
        inference_boundary_tracker.close()

    executor._emit_deferred_inference_signals(iteration_context)

    duration = time.monotonic() - start_time
    succeeded_count = len(outputs)
    failed_count = total - succeeded_count
    met_threshold = executor._execution_modes.success_count_meets_threshold(
        succeeded_count, total, executor._map_config.min_success_threshold
    )
    executor._event_publisher.emit_step_completed(
        succeeded_count=succeeded_count,
        failed_count=failed_count,
        total=total,
        duration_seconds=duration,
        met_threshold=met_threshold,
    )

    processing_seconds = None
    queue_wait_seconds = None
    first_inference_at = None
    for ctx in iteration_context.values():
        t = ctx.get("inference_started_at")
        if isinstance(t, int | float):
            first_inference_at = (
                t if first_inference_at is None else min(first_inference_at, t)
            )
    if first_inference_at is not None:
        queue_wait_seconds = first_inference_at - start_time
        processing_seconds = max(0.0, duration - queue_wait_seconds)

    return MapOutputCollection(
        list(outputs),
        keys=output_keys,
        output_positions=output_positions,
        total_count=total,
        processing_seconds=processing_seconds,
        queue_wait_seconds=queue_wait_seconds,
    )
