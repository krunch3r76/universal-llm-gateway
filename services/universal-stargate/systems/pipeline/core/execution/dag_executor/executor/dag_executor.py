"""``DAGExecutor`` class shell: instance state plus thin method delegators.

Holds the ``DAGExecutor`` class itself — the single asyncio DAG scheduler for
pipeline step execution. ``__init__`` owns the instance state (``nodes``,
``context``, ``execution_order``, ``_checkpoint_manager``, ``_pending_tasks``,
``_proxy_client``) and constructs the two collaborators it delegates to,
``StepObservability`` and ``StepModelCoordinator``. Every public and private
method is a thin delegator that inline-imports its implementation from a sibling
submodule (``lifecycle``, ``scheduling``, ``completions``, ``step_runner``,
``map_step``) and calls it with ``self`` as the executor. Inline imports inside
the delegators sidestep the cycle that would otherwise arise because those
submodules TYPE_CHECKING-import ``DAGExecutor`` from here. Method signatures and
docstrings are preserved verbatim so siblings (``model_coordination``,
``observability``) that reach private methods through the instance keep their
surface, and so consumers importing ``DAGExecutor`` via the package ``__init__``
see an unchanged class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model_coordination import StepModelCoordinator
from ..observability import StepObservability

if TYPE_CHECKING:
    import asyncio

    from ....dag import StepNode, StepState
    from ....handlers.protocol import PipelineContext, StepOutput
    from ....schemas import StepConfig
    from ....step_config import ResolvedTargetModel
    from ...checkpoint import CheckpointManager
    from ...proxy_client import ProxyClient


class DAGExecutor:
    """
    Execute pipeline DAG with automatic parallelization.

    Steps execute as soon as their dependencies are satisfied.
    Independent steps run in parallel using asyncio.

    CONCURRENCY INVARIANT:
    - Only this executor writes to context.outputs
    - Handlers return StepOutput, never write directly
    - This is the single-writer for parallel safety

    Invariants:
    - ∀ step: dependencies complete before step starts
    - Parallel steps share no mutable state
    - First failure cancels remaining (fail-fast)

    SKIP SEMANTICS:
    - SKIPPED counts as "dependency satisfied" (dependents become ready)
    - Dependents must guard against missing inputs via conditions
    - Skip propagation is NOT automatic (use conditions for skip chains)
    """

    def __init__(
        self,
        nodes: dict[str, StepNode],
        context: PipelineContext,
        checkpoint_manager: CheckpointManager | None = None,
    ):
        self.nodes = nodes
        self.context = context
        self.execution_order: list[str] = []
        self._checkpoint_manager = checkpoint_manager
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}
        self._proxy_client: ProxyClient | None = None
        self._observability = StepObservability(self)
        self._model_coordination = StepModelCoordinator(self)

    async def _ensure_proxy_client(self) -> ProxyClient:
        """Lazily initialize ProxyClient for handler invocations."""
        from .lifecycle import ensure_proxy_client

        return await ensure_proxy_client(self)

    async def shutdown(self) -> None:
        """Cleanup resources."""
        from .lifecycle import shutdown

        await shutdown(self)

    async def cancel(self) -> None:
        """Cancel pipeline execution and cleanup resources.

        Called when client disconnects or external cancellation is requested.
        Best-effort drift check during cleanup - logs but does not raise.
        """
        from .lifecycle import cancel

        await cancel(self)

    async def execute(self) -> None:
        """
        Execute all steps respecting dependencies.

        Raises:
            PipelineExecutionError: On deadlock, timeout, or execution failure
        """
        from .lifecycle import execute_dag

        await execute_dag(self)

    async def _process_ready_steps(self) -> bool:
        """Filter and launch ready steps.

        Returns True when any progress is made in this pass:
        - one or more steps launched, or
        - one or more ready steps transitioned to SKIPPED.
        """
        from .scheduling import process_ready_steps

        return await process_ready_steps(self)

    async def _launch_steps(self, steps_to_launch: list[StepNode]) -> bool:
        """Launch selected steps as asyncio tasks."""
        from .scheduling import launch_steps

        return await launch_steps(self, steps_to_launch)

    async def _filter_ready_steps(self, ready_steps: list[StepNode]) -> list[StepNode]:
        """Filter ready steps by condition and model availability."""
        from .scheduling import filter_ready_steps

        return await filter_ready_steps(self, ready_steps)

    async def _await_and_handle_completions(self) -> None:
        """Wait for tasks and handle completion/failure."""
        from .completions import await_and_handle_completions

        await await_and_handle_completions(self)

    def _propagate_completion(self, completed_step_id: str) -> None:
        """Mark dependents ready when all prerequisites are terminal-success states.

        Dependency satisfaction intentionally excludes FAILED prerequisites; only
        COMPLETED/SKIPPED permit downstream execution.
        """
        from .scheduling import propagate_completion

        propagate_completion(self, completed_step_id)

    def _incomplete_step_ids(self) -> list[str]:
        """Return non-terminal step IDs for timeout/deadlock diagnostics."""
        from .scheduling import incomplete_step_ids

        return incomplete_step_ids(self)

    def _all_done(self) -> bool:
        """Check if all steps are complete."""
        from .scheduling import all_done

        return all_done(self)

    def _step_state_counts(self) -> dict[StepState, int]:
        """Count terminal and intermediate step states for completion telemetry."""
        from .scheduling import step_state_counts

        return step_state_counts(self)

    def _should_execute_step(self, step: StepConfig) -> tuple[bool, str | None]:
        """Check if step should execute based on enabled flag and condition."""
        from .step_runner import should_execute_step

        return should_execute_step(self, step)

    async def _execute_step(self, node: StepNode) -> None:
        """Execute step and update state."""
        from .step_runner import execute_step

        await execute_step(self, node)

    async def _run_step(
        self,
        node: StepNode,
        *,
        target_model: str | None = None,
        target_resolution: ResolvedTargetModel | None = None,
    ) -> StepOutput:
        """Execute step, falling back to alternative models on eligible failures.

        Receives the coordinator-resolved target model so fallback can use it
        as the authoritative primary model identity without re-resolving.
        """
        from .step_runner import run_step

        return await run_step(
            self,
            node,
            target_model=target_model,
            target_resolution=target_resolution,
        )

    async def _run_step_inner(self, step: StepConfig) -> StepOutput:
        """Execute step through the standard wrapper chain."""
        from .step_runner import run_step_inner

        return await run_step_inner(self, step)

    async def _try_step_model_fallback(
        self,
        step: StepConfig,
        primary_err: Exception,
        *,
        target_model: str | None = None,
        target_resolution: ResolvedTargetModel | None = None,
    ) -> StepOutput:
        """Delegate fallback with the coordinator-resolved model.

        Passes primary_model_id so fallback does not independently re-resolve,
        avoiding silent divergence from the coordinated model identity.
        """
        from .step_runner import try_step_model_fallback

        return await try_step_model_fallback(
            self,
            step,
            primary_err,
            target_model=target_model,
            target_resolution=target_resolution,
        )

    async def _execute_map_step(self, node: StepNode) -> StepOutput:
        """
        Execute map step with MapExecutor.

        MAP is an execution mode, not a handler type. The step.type field
        contains the actual handler type (e.g., "generate"). type="map"
        is rejected at parse time by StepConfig.reject_map_type validator.
        """
        from .map_step import execute_map_step

        return await execute_map_step(self, node)
