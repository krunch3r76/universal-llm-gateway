"""Map executor: fan-out parallel execution with partial success support."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ..map_output_collection import MapOutputCollection
from .concurrency_manager import MapConcurrencyManager
from .events import MapEventPublisher
from .execution_modes import MapExecutionModes
from .iteration_preparer import MapIterationPreparer

if TYPE_CHECKING:
    from ....schemas import StepConfig, StepOutput
    from ...checkpoint import CheckpointManager
    from ...resolver import NamespaceResolver

logger = logging.getLogger(__name__)


class MapExecutor:
    """
    Executes map steps with fan-out parallelism.

    Execution flow:
    1. Resolve map_over to list/dict
    2. For each item, create MapState and inject mapNs
    3. Execute handler with iteration context
    4. Return list of StepOutput (partial success if threshold configured)

    Async-safety:
    - All iterations execute concurrently
    - Each iteration gets fresh MapState (no shared state)
    - System-level concurrency managed by Stargate proxy

    Partial Success:
    - If timeout_seconds set: uses asyncio.wait() with timeout
    - If min_success_threshold set: allows partial completion
    - Returns successful results if threshold met
    - Raises MapPartialFailureError if below threshold
    """

    def __init__(
        self,
        step: StepConfig,
        handler: Any,
        resolver: NamespaceResolver,
        runtime: Any,
        checkpoint_manager: CheckpointManager | None = None,
        cancel_callback: Callable[[str, str | None], Awaitable[bool]] | None = None,
    ) -> None:
        self._step = step
        self._handler = handler
        self._runtime = runtime
        self._checkpoint_manager = checkpoint_manager
        self._map_config = step.get_map_config()
        self._event_publisher = MapEventPublisher(step, runtime)
        self._iteration_preparer = MapIterationPreparer(
            step, self._map_config, resolver, handler
        )
        self._concurrency_manager = MapConcurrencyManager(
            step, cancel_callback, self._event_publisher
        )
        self._execution_modes = MapExecutionModes(
            step, self._event_publisher, self._concurrency_manager
        )

    async def execute(self) -> MapOutputCollection:
        """
        Execute map step.

        Returns MapOutputCollection for wildcard access.
        Uses partial success pattern if timeout/threshold configured.
        """
        if not self._map_config:
            raise ValueError(f"Step '{self._step.name}' missing map_config")

        start_time = time.monotonic()

        iteration_items = self._iteration_preparer.resolve_map_over(
            self._map_config.map_over
        )
        total = len(iteration_items)

        if total == 0:
            logger.warning(
                "[%s] Map step has 0 iterations (empty map_over collection). "
                "Returning empty MapOutputCollection.",
                self._step.name,
            )
            return MapOutputCollection([], keys=[])

        self._event_publisher.emit_step_started(
            total=total,
            timeout_seconds=self._map_config.timeout_seconds,
            threshold=self._map_config.min_success_threshold,
        )

        logger.info(
            "[%s] Map step: %d iterations (timeout=%s, threshold=%s, fail_fast=%s)",
            self._step.name,
            total,
            self._map_config.timeout_seconds,
            self._map_config.min_success_threshold,
            self._map_config.fail_fast,
        )

        pool_assignments = self._iteration_preparer.build_pool_assignments(
            iteration_items, self._runtime
        )

        # Build iteration context for tracking
        iteration_context: dict[int, dict[str, Any]] = {}
        for idx, value, key in iteration_items:
            assigned_model = pool_assignments.get(idx)
            iter_inputs = self._iteration_preparer.prepare_iteration_inputs(
                idx, value, total, key, assigned_model
            )
            iter_step = self._iteration_preparer.create_iteration_step(
                iter_inputs[2], assigned_model
            )
            model_id_for_iteration = getattr(iter_step, "model_ref", None) or getattr(
                iter_step, "model_id", None
            )
            iteration_context[idx] = {
                "model_id": model_id_for_iteration,
                "gateway_id": None,
                "started_at": time.monotonic(),
                "map_iteration_request_id": str(uuid.uuid4()),
            }

        iteration_metadata = [(idx, key) for idx, _, key in iteration_items]

        async def _tracked_iteration(
            idx: int, value: Any, key: str | None
        ) -> StepOutput:
            result = await self._execute_iteration(
                idx, value, total, key, iteration_context, pool_assignments.get(idx)
            )
            iteration_context[idx]["completed_at"] = time.monotonic()
            return result

        tasks = {
            asyncio.create_task(_tracked_iteration(idx, value, key)): idx
            for idx, value, key in iteration_items
        }

        if self._map_config.fail_fast:
            outputs, output_keys = await self._execution_modes.execute_with_fail_fast(
                tasks,
                total,
                self._map_config.min_success_threshold,
                iteration_metadata,
                iteration_context,
            )
        elif self._map_config.timeout_seconds is not None:
            outputs, output_keys = await self._execution_modes.execute_with_timeout(
                tasks,
                total,
                self._map_config.timeout_seconds,
                self._map_config.min_success_threshold,
                iteration_metadata,
                iteration_context,
            )
        else:
            # Strict mode: all must succeed, gather() preserves order
            outputs = await asyncio.gather(*tasks.keys())
            output_keys = [key for _, _, key in iteration_items]

        duration = time.monotonic() - start_time
        succeeded_count = len(outputs)
        failed_count = total - succeeded_count
        met_threshold = self._execution_modes.success_count_meets_threshold(
            succeeded_count, total, self._map_config.min_success_threshold
        )
        self._event_publisher.emit_step_completed(
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            total=total,
            duration_seconds=duration,
            met_threshold=met_threshold,
        )

        return MapOutputCollection(list(outputs), keys=output_keys)

    async def _execute_iteration(
        self,
        index: int,
        value: Any,
        total: int,
        key: str | None = None,
        iteration_context: dict[int, dict[str, Any]] | None = None,
        assigned_model: str | None = None,
    ) -> StepOutput:
        """Execute single map iteration."""
        (
            _iter_resolver,
            _all_inputs,
            map_inputs,
            typed_inputs,
        ) = self._iteration_preparer.prepare_iteration_inputs(
            index, value, total, key, assigned_model
        )

        iteration_key = f"{self._step.name}:{index}"
        if self._checkpoint_manager:
            fingerprint = (
                typed_inputs.fingerprint()
                if typed_inputs and hasattr(typed_inputs, "fingerprint")
                else None
            )
            cached = await self._checkpoint_manager.load_checkpoint(
                iteration_key,
                input_fingerprint=fingerprint,
            )
            if cached:
                from ....schemas import StepOutput

                return StepOutput(
                    data=cached.output_json
                    if cached.output_json
                    else {"raw": cached.output_raw},
                    metadata=cached.output_meta or {},
                )

        step_for_iteration = self._iteration_preparer.create_iteration_step(
            map_inputs, assigned_model
        )

        if iteration_context is not None and index in iteration_context:
            model_ref = getattr(step_for_iteration, "model_ref", None)
            if model_ref:
                iteration_context[index]["model_id"] = model_ref

        ctx = iteration_context.get(index, {}) if iteration_context else {}
        self._event_publisher.emit_iteration_started(
            index=index,
            model_id=ctx.get("model_id"),
            gateway_id=ctx.get("gateway_id"),
        )

        map_iteration_request_id: str | None = None
        if iteration_context and index in iteration_context:
            map_iteration_request_id = iteration_context[index].get(
                "map_iteration_request_id"
            )

        iter_runtime = self._runtime
        if map_iteration_request_id:
            iter_runtime = self._runtime.with_map_iteration_request_id(
                map_iteration_request_id
            )

        source_step_name = self._iteration_preparer.extract_source_step_name()
        if source_step_name:
            from ....handlers.protocol import MapIterationState

            map_state = MapIterationState(
                source_step_name=source_step_name,
                iteration_key=key,
                iteration_index=index,
            )
            iter_runtime = iter_runtime.with_map_state(map_state)

        output = await self._handler.execute(step_for_iteration, iter_runtime)

        if self._checkpoint_manager and self._checkpoint_manager.should_checkpoint(
            self._step
        ):
            fingerprint = (
                typed_inputs.fingerprint()
                if typed_inputs and hasattr(typed_inputs, "fingerprint")
                else None
            )
            await self._checkpoint_manager.save_checkpoint(
                iteration_key,
                output,
                input_fingerprint=fingerprint,
            )

        logger.debug("[%s] Iteration %d/%d complete", self._step.name, index + 1, total)
        return output
