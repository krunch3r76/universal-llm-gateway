"""Model lock and global tracking coordination for DAG executor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from src.core.gateway_tracker import gateway_tracker

from ..model_tracker import ModelUsageTracker

if TYPE_CHECKING:
    from ...dag import StepNode
    from .executor import DAGExecutor

logger = get_logger(__name__)


class StepModelCoordinator:
    """Encapsulate model lock and global usage tracking decisions."""

    def __init__(self, executor: DAGExecutor) -> None:
        self._executor = executor
        self._model_tracker = ModelUsageTracker()
        self._resolved_target_models: dict[
            tuple[str, tuple[tuple[str, str], ...] | None],
            str | None,
        ] = {}

    async def resolve_target_model(self, node: StepNode) -> str | None:
        """Resolve step target model using pipeline registry/domain context."""
        return await self._resolve_target_model(node, model_ref_overrides=None)

    async def resolve_target_model_for_execution(
        self,
        node: StepNode,
        model_ref_overrides: dict[str, str] | None,
    ) -> str | None:
        """Resolve target model using execution-time model overrides."""
        return await self._resolve_target_model(
            node,
            model_ref_overrides=model_ref_overrides,
        )

    async def _resolve_target_model(
        self,
        node: StepNode,
        *,
        model_ref_overrides: dict[str, str] | None,
    ) -> str | None:
        """Resolve once per step+override set to avoid repeated self-selection."""
        override_key = (
            tuple(sorted(model_ref_overrides.items())) if model_ref_overrides else None
        )
        cache_key = (node.step.id, override_key)
        if cache_key in self._resolved_target_models:
            return self._resolved_target_models[cache_key]

        try:
            resolved = await node.step.get_target_model_id_async(
                self._executor.context._registry,
                domain=self._executor.context.pipeline.domain,
                search_path=self._executor.context.pipeline.source_search_path,
                model_ref_overrides=model_ref_overrides,
                context=self._executor.context,
            )
        except KeyError as exc:
            model_ref = node.step.model_ref or "unknown"
            self._executor._observability.emit_pipeline_model_registry_lookup_failed(
                step_id=node.step.id,
                model_ref=model_ref,
                error=str(exc),
            )
            logger.warning(
                "Model registry lookup failed for step '%s' (model_ref=%s): %s",
                node.step.id,
                model_ref,
                exc,
            )
            resolved = None
        self._resolved_target_models[cache_key] = resolved
        return resolved

    @staticmethod
    def get_lock_model(node: StepNode, target_model: str | None) -> str | None:
        """
        Return local lock model for this step.

        Sub-pipeline steps intentionally bypass local lock, but still use
        global tracking for eviction protection.
        """
        return (
            None if node.step.get_domain_field("_sub_pipeline_step") else target_model
        )

    def can_launch_with_lock(self, lock_model: str | None) -> bool:
        """Check whether local model lock can be acquired."""
        if not lock_model:
            return True
        return self._model_tracker.can_acquire(lock_model)

    def on_step_launched(
        self,
        *,
        step_id: str,
        target_model: str | None,
        lock_model: str | None,
        models_in_use_this_iteration: set[str],
    ) -> None:
        """Register local/global tracking when a step transitions to RUNNING."""
        if lock_model:
            self._model_tracker.acquire(lock_model, step_id)
            models_in_use_this_iteration.add(lock_model)
            self._executor._observability.emit_pipeline_model_gate_claimed(
                step_id=step_id,
                model_id=lock_model,
            )
        if target_model:
            self.register_global_tracking(target_model, step_id)

    def on_step_finished(
        self,
        *,
        step_id: str,
        target_model: str | None,
        outcome: str = "success",
    ) -> None:
        """Release local/global tracking for a finished step."""
        self._model_tracker.release(target_model, step_id)
        if target_model:
            self._executor._observability.emit_pipeline_model_gate_released(
                step_id=step_id,
                model_id=target_model,
                outcome=outcome,
            )
            self.unregister_global_tracking(target_model, step_id)

    def on_cancelled_step(self, *, step_id: str, target_model: str | None) -> None:
        """Release tracking for a cancelled step."""
        self.on_step_finished(
            step_id=step_id,
            target_model=target_model,
            outcome="cancelled",
        )

    def register_global_tracking(self, model_id: str, step_id: str) -> None:
        """
        Register pipeline step model usage with global tracker.

        Prevents eviction of models actively used by pipeline steps.
        """
        parsed_model_id = ModelId.parse(model_id)
        routing_key = parsed_model_id.routing_key

        pipeline_request_id = (
            f"pipeline_{self._executor.context.execution_id}_{step_id}"
        )
        gateway_name = (
            getattr(self._executor.context, "selected_gateway_instance", None)
            or "localhost"
        )

        gateway_tracker.track_request(
            gateway_id=gateway_name,
            request_id=pipeline_request_id,
            routing_key=routing_key,
        )

        logger.debug(
            f"🔒 Registered pipeline step '{step_id}' with global tracker "
            + f"(model={model_id}, routing_key={routing_key}, gateway={gateway_name})"
        )

    def unregister_global_tracking(self, model_id: str, step_id: str) -> None:
        """Unregister pipeline step model usage from global tracker."""
        pipeline_request_id = (
            f"pipeline_{self._executor.context.execution_id}_{step_id}"
        )
        gateway_name = (
            getattr(self._executor.context, "selected_gateway_instance", None)
            or "localhost"
        )
        gateway_tracker.complete_request(gateway_name, pipeline_request_id)

        logger.debug(
            f"🔓 Unregistered pipeline step '{step_id}' from global tracker "
            + f"(model={model_id}, gateway={gateway_name})"
        )
