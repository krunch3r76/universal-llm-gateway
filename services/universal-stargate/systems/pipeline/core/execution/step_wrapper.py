"""
Step execution wrappers integrating retry, timeout, and checkpoint.

Composition order (outer to inner):
1. Step timeout (absolute wall time)
2. Retry with backoff
3. Handler timeout (per-attempt)
4. Handler execution
"""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .checkpoint import CheckpointManager
from .retry import execute_with_retry
from .timeout import execute_with_handler_timeout, execute_with_step_timeout

if TYPE_CHECKING:
    from ..handlers.protocol import StepOutput
    from ..schemas import StepConfig

logger = logging.getLogger(__name__)


async def execute_step_with_wrappers(
    step: "StepConfig",
    handler_fn: Callable[[], Awaitable["StepOutput"]],
    checkpoint_manager: CheckpointManager | None = None,
) -> "StepOutput":
    """
    Execute step with retry, timeout, and checkpoint wrappers.

    Invariant: ∀ wrapper ∈ {retry, timeout, checkpoint}: applied iff configured

    Composition:
        step_timeout(
            checkpoint_load_or_execute(
                retry(
                    handler_timeout(
                        handler_fn()
                    )
                )
            )
        )
    """
    # Check for cached checkpoint first
    if checkpoint_manager:
        cached = await _try_load_checkpoint(step, checkpoint_manager)
        if cached:
            return cached

    # Build execution chain from inner to outer
    async def with_handler_timeout() -> "StepOutput":
        if step.handler_timeout_seconds:
            return await execute_with_handler_timeout(
                handler_fn,
                step.handler_timeout_seconds,
                step.name,
            )
        return await handler_fn()

    async def with_retry() -> "StepOutput":
        policy = step.get_retry_policy()
        if policy:
            return await execute_with_retry(
                with_handler_timeout,
                policy,
                step.name,
            )
        return await with_handler_timeout()

    # Execute with optional step timeout
    if step.timeout_seconds:
        result = await execute_with_step_timeout(
            with_retry,
            step.timeout_seconds,
            step.name,
        )
    else:
        result = await with_retry()

    # Save checkpoint after successful execution
    if checkpoint_manager and _should_checkpoint(step, checkpoint_manager):
        await _save_checkpoint(step, result, checkpoint_manager)

    return result


async def _try_load_checkpoint(
    step: "StepConfig",
    manager: CheckpointManager,
) -> "StepOutput | None":
    """Load checkpoint if exists."""
    from ..handlers.protocol import StepOutput

    data = await manager.load_checkpoint(step.name)
    if data:
        logger.info(f"[{step.name}] Resuming from checkpoint (saved: {data.saved_at})")
        output = StepOutput(
            raw=data.output_raw,
            json=data.output_json,
        )
        # Restore metadata fields if present
        if data.output_meta:
            for key, value in data.output_meta.items():
                if hasattr(output, key):
                    setattr(output, key, value)
        return output
    return None


def _should_checkpoint(step: "StepConfig", manager: CheckpointManager) -> bool:
    """Check if step should be checkpointed."""
    if step.checkpoint is False:
        return False
    if step.checkpoint is True or step.checkpoint == "milestone":
        return True
    return manager.should_checkpoint_step(step.name)


async def _save_checkpoint(
    step: "StepConfig",
    output: "StepOutput",
    manager: CheckpointManager,
) -> None:
    """Save checkpoint (non-fatal on failure)."""
    try:
        await manager.save_checkpoint(step.name, output)
    except Exception as e:
        logger.warning(f"[{step.name}] Checkpoint save failed (non-fatal): {e}")
