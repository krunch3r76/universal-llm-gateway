"""Map executor: fan-out parallel execution with partial success support."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol, Self

from ..map_output_collection import MapOutputCollection
from .concurrency_manager import MapConcurrencyManager
from .events import MapEventPublisher
from .execution_modes import MapExecutionModes
from .iteration_preparer import MapIterationPreparer

if TYPE_CHECKING:
    from universal_event_bus import Subscription

    from ....schemas import StepConfig, StepOutput
    from ...checkpoint import CheckpointManager
    from ...resolver import NamespaceResolver

logger = logging.getLogger(__name__)


class MapIterationRuntimeProtocol(Protocol):
    """Runtime contract needed by map iteration execution paths."""

    pipeline: Any  # TODO: tighten to concrete runtime pipeline protocol
    execution_id: str
    recorder: Any  # TODO: tighten to concrete recorder protocol
    _proxy: Any  # TODO: tighten to concrete proxy protocol

    def with_map_iteration_request_id(self, request_id: str) -> Self: ...

    def with_inference_request_id(self, request_id: str) -> Self: ...

    def with_map_state(self, map_state: Any) -> Self: ...


class MapIterationHandlerProtocol(Protocol):
    """Handler contract needed by map executor."""

    async def execute(self, step: StepConfig, context: Any) -> Any: ...


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
        handler: MapIterationHandlerProtocol,
        resolver: NamespaceResolver,
        runtime: MapIterationRuntimeProtocol,
        checkpoint_manager: CheckpointManager | None = None,
        cancel_callback: Callable[[str, str | None], Awaitable[bool]] | None = None,
    ) -> None:
        """Initialize map execution dependencies for one map step."""
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

        pool_assignments = await self._iteration_preparer.build_pool_assignments(
            iteration_items, self._runtime
        )

        iteration_context = self._build_iteration_context(
            iteration_items=iteration_items,
            pool_assignments=pool_assignments,
            total=total,
        )

        # Subscribe to both boundaries with primary-preferred stamping semantics.
        inference_start_subscriptions = self._subscribe_inference_start(
            iteration_context
        )

        iteration_metadata = [(idx, key) for idx, _, key in iteration_items]

        async def _tracked_iteration(
            idx: int, value: object, key: str | None
        ) -> StepOutput:
            result = await self._execute_iteration(
                idx, value, total, key, iteration_context, pool_assignments.get(idx)
            )
            ctx = iteration_context[idx]
            ctx["completed_at"] = time.monotonic()
            elapsed = ctx["completed_at"] - ctx.get("started_at", ctx["completed_at"])
            inference_start = ctx.get("inference_started_at")
            inference_seconds = (
                ctx["completed_at"] - inference_start
                if inference_start is not None
                else None
            )
            self._event_publisher.emit_iteration_completed_immediate(
                index=idx,
                elapsed_seconds=round(elapsed, 3),
                inference_seconds=round(inference_seconds, 3)
                if inference_seconds is not None
                else None,
                prompt_tokens=getattr(result, "prompt_tokens", 0),
                completion_tokens=getattr(result, "completion_tokens", 0),
            )
            return result

        tasks = {
            asyncio.create_task(_tracked_iteration(idx, value, key)): idx
            for idx, value, key in iteration_items
        }

        try:
            strict_output_keys = [key for _, _, key in iteration_items]
            has_timeout_constraints = (
                self._map_config.timeout_seconds is not None
                or self._map_config.inference_timeout_seconds is not None
            )
            if self._map_config.fail_fast:
                outputs, output_keys, output_positions = (
                    await self._execution_modes.execute_with_fail_fast(
                        tasks,
                        total,
                        self._map_config.min_success_threshold,
                        iteration_metadata,
                        iteration_context,
                    )
                )
            elif has_timeout_constraints:
                outer_timeout = self._map_config.timeout_seconds or 3600.0
                outputs, output_keys, output_positions = (
                    await self._execution_modes.execute_with_timeout(
                        tasks,
                        total,
                        outer_timeout,
                        self._map_config.min_success_threshold,
                        iteration_metadata,
                        iteration_context,
                        inference_timeout_seconds=(
                            self._map_config.inference_timeout_seconds
                        ),
                    )
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
            for subscription in inference_start_subscriptions:
                subscription.unsubscribe()

        self._emit_deferred_inference_signals(iteration_context)

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

        return MapOutputCollection(
            list(outputs),
            keys=output_keys,
            output_positions=output_positions,
            total_count=total,
        )

    def _subscribe_inference_start(
        self, iteration_context: dict[int, dict[str, Any]]
    ) -> list[Subscription]:
        """
        Subscribe to inference-start boundary signals for this execute() scope.

        Primary-preferred model:
            - request.inference.started stamps inference_started_at (used by
              timeout monitor + telemetry) and emits pipeline event immediately
            - request.processing stamps fallback_boundary_at (deferred telemetry
              only, never used by timeout monitor)

        Deferred detection at iteration completion resolves:
            - primary arrived: no-op (already emitted)
            - only fallback arrived: deferred inference.started + fallback.used
            - neither arrived: signal.lost

        Returns Subscription handles (or empty list if event bus unavailable).
        Caller must unsubscribe after execution completes.
        """
        proxy = getattr(self._runtime, "_proxy", None)
        event_bus = getattr(proxy, "event_bus", None) if proxy else None
        if not event_bus:
            return []

        request_id_to_idx: dict[str, int] = {
            ctx["request_id"]: idx
            for idx, ctx in iteration_context.items()
            if "request_id" in ctx
        }
        subscriptions: list[Subscription] = []

        def _on_primary(rid: str) -> None:
            idx = request_id_to_idx.get(rid)
            if idx is None:
                return
            ctx = iteration_context.get(idx)
            if ctx is None or "inference_started_at" in ctx:
                return
            ctx["inference_started_at"] = time.monotonic()
            ctx["inference_start_source"] = "request.inference.started"
            queue_wait = ctx["inference_started_at"] - ctx["started_at"]
            self._event_publisher.emit_iteration_inference_started(
                index=idx,
                request_id=rid,
                model_id=ctx.get("model_id"),
                queue_wait_seconds=round(queue_wait, 3),
            )

        def _on_fallback(rid: str) -> None:
            idx = request_id_to_idx.get(rid)
            if idx is None:
                return
            ctx = iteration_context.get(idx)
            if ctx is None or "fallback_boundary_at" in ctx:
                return
            ctx["fallback_boundary_at"] = time.monotonic()

        async def _on_request_inference_started(event: Any) -> None:
            rid = event.payload.get("request_id")
            if rid in request_id_to_idx:
                _on_primary(rid)

        async def _on_request_processing(event: Any) -> None:
            rid = event.payload.get("request_id")
            if rid in request_id_to_idx:
                _on_fallback(rid)

        subscriptions.append(
            event_bus.subscribe_async(
                "request.inference.started", _on_request_inference_started
            )
        )
        subscriptions.append(
            event_bus.subscribe_async(
                "request.processing", _on_request_processing
            )
        )
        return subscriptions

    def _emit_deferred_inference_signals(
        self, iteration_context: dict[int, dict[str, Any]]
    ) -> None:
        """
        Resolve deferred inference timing when primary signal is absent.

        Outcomes per iteration:
            1) primary set (`inference_started_at`): already emitted, skip
            2) only fallback set (`fallback_boundary_at`): emit deferred
               inference.started + fallback.used
            3) neither set: emit signal.lost
        """
        fallback_warning_emitted = False
        for idx, ctx in iteration_context.items():
            request_id = ctx.get("request_id")
            if not request_id:
                continue
            if "inference_started_at" in ctx:
                continue

            fallback_boundary_at = ctx.get("fallback_boundary_at")
            if isinstance(fallback_boundary_at, float):
                if not fallback_warning_emitted:
                    logger.warning(
                        "Map execution fallback active: primary "
                        "request.inference.started missing; using "
                        "request.processing timing (execution_id=%s step=%s)",
                        self._runtime.execution_id,
                        self._step.name,
                    )
                    fallback_warning_emitted = True
                queue_wait = fallback_boundary_at - ctx["started_at"]
                self._event_publisher.emit_iteration_inference_started(
                    index=idx,
                    request_id=request_id,
                    model_id=ctx.get("model_id"),
                    queue_wait_seconds=round(queue_wait, 3),
                )
                self._event_publisher.emit_iteration_inference_fallback_used(
                    index=idx,
                    request_id=request_id,
                    fallback_signal="request.processing",
                    reason=(
                        "primary request.inference.started not received "
                        "before iteration completion"
                    ),
                )
                continue

            self._event_publisher.emit_iteration_inference_signal_lost(
                index=idx,
                request_id=request_id,
            )

    @staticmethod
    def _extract_input_fingerprint(typed_inputs: Any) -> str | None:
        """Return deterministic input fingerprint when available."""
        if typed_inputs and hasattr(typed_inputs, "fingerprint"):
            return typed_inputs.fingerprint()
        return None

    async def _execute_iteration(
        self,
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
        ) = self._iteration_preparer.prepare_iteration_inputs(
            index, value, total, key, assigned_model
        )

        iteration_key = f"{self._step.name}:{index}"
        if self._checkpoint_manager:
            fingerprint = self._extract_input_fingerprint(typed_inputs)
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
            request_id=ctx.get("request_id"),
        )

        iter_runtime = self._build_iteration_runtime(ctx)

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
            fingerprint = self._extract_input_fingerprint(typed_inputs)
            await self._checkpoint_manager.save_checkpoint(
                iteration_key,
                output,
                input_fingerprint=fingerprint,
            )

        logger.debug("[%s] Iteration %d/%d complete", self._step.name, index + 1, total)
        return output

    def _build_iteration_context(
        self,
        *,
        iteration_items: list[tuple[int, object, str | None]],
        pool_assignments: dict[int, str],
        total: int,
    ) -> dict[int, dict[str, Any]]:
        """Build and seed per-iteration context used by event correlation."""
        iteration_context: dict[int, dict[str, Any]] = {}
        for idx, value, key in iteration_items:
            assigned_model = pool_assignments.get(idx)
            iter_inputs = self._iteration_preparer.prepare_iteration_inputs(
                idx, value, total, key, assigned_model
            )
            iter_step = self._iteration_preparer.create_iteration_step(
                iter_inputs[2], assigned_model
            )
            model_id_for_iteration = getattr(
                iter_step, "model_ref", getattr(iter_step, "model_id", None)
            )
            iteration_context[idx] = {
                "model_id": model_id_for_iteration,
                "gateway_id": None,
                "started_at": time.monotonic(),
                "map_iteration_request_id": str(uuid.uuid4()),
                "request_id": str(uuid.uuid4()),
            }
        return iteration_context

    def _build_iteration_runtime(
        self,
        ctx: dict[str, Any],
    ) -> MapIterationRuntimeProtocol:
        """Decorate runtime with iteration and request-level correlation IDs."""
        iter_runtime = self._runtime
        map_iteration_request_id = ctx.get("map_iteration_request_id")
        if map_iteration_request_id:
            iter_runtime = iter_runtime.with_map_iteration_request_id(
                map_iteration_request_id
            )
        inference_request_id = ctx.get("request_id")
        if inference_request_id:
            iter_runtime = iter_runtime.with_inference_request_id(inference_request_id)
        return iter_runtime
