"""Map executor class shell: fan-out parallel execution with partial success.

Holds the ``MapExecutor`` class itself. ``__init__`` constructs the sibling
collaborators (``MapEventPublisher``, ``MapIterationPreparer``,
``MapConcurrencyManager``, ``MapExecutionModes``) used through the lifetime of
``execute()``. Every public and private method is a thin delegator that imports
its implementation from a sibling submodule inside the body and calls it with
``self`` as the executor. Inline-imports inside delegators sidestep the cycle
that would arise if the shell imported all submodules at top level (the
submodules in turn TYPE_CHECKING-import ``MapExecutor`` from here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..concurrency_manager import MapConcurrencyManager
from ..events import MapEventPublisher
from ..execution_modes import MapExecutionModes
from ..iteration_preparer import MapIterationPreparer
from .protocols import MapIterationHandlerProtocol, MapIterationRuntimeProtocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .....schemas import StepConfig, StepOutput
    from ....checkpoint import CheckpointManager
    from ....request_inference_boundary import RequestInferenceBoundaryTracker
    from ....resolver import NamespaceResolver
    from ...map_output_collection import MapOutputCollection


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
        """Initialize map execution dependencies for a single map step.

        Args:
            step: Map step configuration for this executor.
            handler: Per-iteration handler implementation.
            resolver: Namespace resolver used for map input preparation.
            runtime: Shared execution runtime used to derive iteration runtime state.
            checkpoint_manager: Optional checkpoint manager for iteration caching.
            cancel_callback: Optional cancellation callback for fail-fast paths.
        """
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
        from .execute_flow import execute_map

        return await execute_map(self)

    def _subscribe_inference_start(
        self, iteration_context: dict[int, dict[str, Any]]
    ) -> RequestInferenceBoundaryTracker:
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

        Returns a reusable request-boundary tracker. Caller must close it after
        execution completes.
        """
        from .inference_boundary import subscribe_inference_start

        return subscribe_inference_start(self, iteration_context)

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
        from .inference_boundary import emit_deferred_inference_signals

        emit_deferred_inference_signals(self, iteration_context)

    def _derive_model_capacity(self, pool_assignments: dict[int, str]) -> int | None:
        """Sum parallel_slots across loaded/busy gateways for the assigned model.

        Uses the first pool assignment as the representative model_id (all
        iterations in a batch pipeline target the same model). Returns None
        when the proxy, federated_manager, or model_resources are unavailable
        so the caller falls back to uncapped dispatch.

        Only nodes where the model is currently loaded or busy contribute —
        nodes in loading/draining/unhealthy state are excluded, matching the
        filter used by GET /api/v1/model-capacity/{model_id}.
        """
        from .capacity import derive_model_capacity

        return derive_model_capacity(self, pool_assignments)

    @staticmethod
    def _extract_input_fingerprint(typed_inputs: Any) -> str | None:
        """Return deterministic input fingerprint when available."""
        from .iteration_execution import extract_input_fingerprint

        return extract_input_fingerprint(typed_inputs)

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
        from .iteration_execution import execute_iteration

        return await execute_iteration(
            self,
            index,
            value,
            total,
            key,
            iteration_context,
            assigned_model,
        )

    def _build_iteration_context(
        self,
        *,
        iteration_items: list[tuple[int, object, str | None]],
        pool_assignments: dict[int, str],
        total: int,
    ) -> dict[int, dict[str, Any]]:
        """Build and seed per-iteration context used by event correlation."""
        from .iteration_context import build_iteration_context

        return build_iteration_context(
            self,
            iteration_items=iteration_items,
            pool_assignments=pool_assignments,
            total=total,
        )

    def _build_iteration_runtime(
        self,
        ctx: dict[str, Any],
    ) -> MapIterationRuntimeProtocol:
        """Decorate runtime with iteration and request-level correlation IDs."""
        from .iteration_context import build_iteration_runtime

        return build_iteration_runtime(self, ctx)
