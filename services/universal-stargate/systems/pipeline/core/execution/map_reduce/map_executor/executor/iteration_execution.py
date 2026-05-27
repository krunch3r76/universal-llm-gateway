"""Single-iteration execution for map step fan-out.

Runs one map iteration end-to-end: prepares inputs, consults the checkpoint
cache, materializes a per-iteration step config with the assigned model,
decorates the runtime with iteration-scoped correlation IDs, dispatches to the
handler, and persists the result to the checkpoint cache when caching is
enabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from .....schemas import StepOutput
    from .map_executor import MapExecutor

logger = get_logger(__name__)


def extract_input_fingerprint(typed_inputs: Any) -> str | None:
    """Return deterministic input fingerprint when available."""
    if typed_inputs and hasattr(typed_inputs, "fingerprint"):
        return typed_inputs.fingerprint()
    return None


async def execute_iteration(
    executor: MapExecutor,
    index: int,
    value: object,
    total: int,
    key: str | None = None,
    iteration_context: dict[int, dict[str, Any]] | None = None,
    assigned_model: str | None = None,
) -> StepOutput:
    """
    Execute one map iteration with optional checkpoint and runtime decoration.

    Responsibilities:
    - Build iteration-specific inputs and execution step
    - Emit per-iteration started signal with correlation IDs
    - Decorate runtime with map/inference request identity
    - Execute handler and persist checkpoint when enabled
    """
    (
        _iter_resolver,
        _all_inputs,
        map_inputs,
        typed_inputs,
    ) = executor._iteration_preparer.prepare_iteration_inputs(
        index, value, total, key, assigned_model
    )

    iteration_key = f"{executor._step.name}:{index}"
    if executor._checkpoint_manager:
        fingerprint = extract_input_fingerprint(typed_inputs)
        cached = await executor._checkpoint_manager.load_checkpoint(
            iteration_key,
            input_fingerprint=fingerprint,
        )
        if cached:
            from .....schemas import StepOutput

            return StepOutput(
                data=cached.output_json
                if cached.output_json
                else {"raw": cached.output_raw},
                metadata=cached.output_meta or {},
            )

    step_for_iteration = executor._iteration_preparer.create_iteration_step(
        map_inputs, assigned_model
    )

    if iteration_context is not None and index in iteration_context:
        model_ref = getattr(step_for_iteration, "model_ref", None)
        if model_ref:
            iteration_context[index]["model_id"] = model_ref

    ctx = (
        iteration_context[index]
        if iteration_context and index in iteration_context
        else {}
    )
    executor._event_publisher.emit_iteration_started(
        index=index,
        model_id=ctx.get("model_id"),
        gateway_id=ctx.get("gateway_id"),
        request_id=ctx.get("request_id"),
    )

    iter_runtime = executor._build_iteration_runtime(ctx)

    source_step_name = executor._iteration_preparer.extract_source_step_name()
    if source_step_name:
        from .....handlers.protocol import MapIterationState

        map_state = MapIterationState(
            source_step_name=source_step_name,
            iteration_key=key,
            iteration_index=index,
        )
        iter_runtime = iter_runtime.with_map_state(map_state)

    output = await executor._handler.execute(step_for_iteration, iter_runtime)

    if executor._checkpoint_manager and executor._checkpoint_manager.should_checkpoint(
        executor._step
    ):
        fingerprint = extract_input_fingerprint(typed_inputs)
        await executor._checkpoint_manager.save_checkpoint(
            iteration_key,
            output,
            input_fingerprint=fingerprint,
        )

    logger.debug("[%s] Iteration %d/%d complete", executor._step.name, index + 1, total)
    return output
