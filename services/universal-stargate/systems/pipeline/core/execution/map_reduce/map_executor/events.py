"""Map executor event publishing."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol, cast

from universal_event_bus import Event

from ....events.lifecycle import MapIterationCompleted
from ....events.map import (
    MapIterationCompleted as BusMapIterationCompleted,
)
from ....events.map import (
    MapIterationFailed,
    MapIterationInferenceFallback,
    MapIterationInferenceLost,
    MapIterationInferenceStarted,
    MapIterationStarted,
    MapStepEmptyIterations,
    MapStepStarted,
)
from ....events.map import MapStepCompleted as BusMapStepCompleted
from ..iteration_state import IterationResult, IterationStatus

if TYPE_CHECKING:
    from ....schemas import StepConfig, StepOutput


class PipelineProtocol(Protocol):
    """Pipeline contract required by map event publishing paths."""

    id: str


class RecorderProtocol(Protocol):
    """Recorder contract required by map event publishing paths."""

    def emit(self, event: MapIterationCompleted) -> None:
        """Emit one lifecycle event."""


class EventBusProtocol(Protocol):
    """Event bus contract required by map event publishing paths."""

    async def publish_async_nowait(self, event: Event) -> None:
        """Publish one bus event asynchronously."""


class ProxyProtocol(Protocol):
    """Proxy contract required by map event publishing paths."""

    event_bus: EventBusProtocol | None


class RuntimeProtocol(Protocol):
    """Runtime contract needed by map event publishing paths."""

    pipeline: PipelineProtocol | None
    execution_id: str
    recorder: RecorderProtocol | None


logger = logging.getLogger(__name__)

# Naming note:
# - `BusMapIterationCompleted` is the global event-bus signal factory.
# - `MapIterationCompleted` (from lifecycle) is recorder-only output with richer fields.


class MapEventPublisher:
    """Handles all event publishing for map step execution."""

    def __init__(self, step: StepConfig, runtime: RuntimeProtocol) -> None:
        self._step: StepConfig = step
        self._runtime: RuntimeProtocol = runtime

    def get_event_context(self) -> tuple[str, str]:
        """Extract pipeline_id and execution_id from runtime context."""
        pipeline_obj = self._runtime.pipeline
        pipeline_id = pipeline_obj.id if pipeline_obj is not None else "unknown"
        execution_id = self._runtime.execution_id
        return pipeline_id, execution_id

    def publish_event(self, event: Event) -> None:
        """Publish event via runtime's event bus (fire-and-forget)."""
        proxy = cast(ProxyProtocol | None, getattr(self._runtime, "_proxy", None))
        event_bus = proxy.event_bus if proxy is not None else None
        if event_bus is not None:
            task = asyncio.create_task(event_bus.publish_async_nowait(event))
            task.add_done_callback(self._log_publish_failure)

    @staticmethod
    def _log_publish_failure(task: asyncio.Task[None]) -> None:
        """Log failed fire-and-forget publishes for observability."""
        if task.cancelled():
            logger.warning("Map event publish task was cancelled")
            return
        error = task.exception()
        if error is not None:
            logger.warning("Map event publish failed", exc_info=error)

    def emit_empty_iterations(self) -> None:
        """Emit MapStepEmptyIterations when map_over resolved to empty collection."""
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            MapStepEmptyIterations(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
            )
        )

    def emit_step_started(
        self,
        total: int,
        timeout_seconds: float | None,
        threshold: int | float | None,
    ) -> None:
        """Emit MapStepStarted event."""
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            MapStepStarted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                total_iterations=total,
                timeout_seconds=timeout_seconds,
                threshold=threshold,
            )
        )

    def emit_step_completed(
        self,
        succeeded_count: int,
        failed_count: int,
        total: int,
        duration_seconds: float,
        met_threshold: bool,
    ) -> None:
        """Emit MapStepCompleted event."""
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            BusMapStepCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                succeeded_count=succeeded_count,
                failed_count=failed_count,
                total_count=total,
                duration_seconds=duration_seconds,
                met_threshold=met_threshold,
            )
        )

    def emit_iteration_started(
        self,
        index: int,
        model_id: str | None,
        gateway_id: str | None,
        request_id: str | None = None,
    ) -> None:
        """Emit MapIterationStarted event."""
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            MapIterationStarted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                iteration_index=index,
                model_id=model_id,
                gateway_id=gateway_id,
                request_id=request_id,
            )
        )

    def emit_iteration_inference_started(
        self,
        index: int,
        request_id: str,
        model_id: str | None,
        queue_wait_seconds: float,
    ) -> None:
        """Emit MapIterationInferenceStarted — bridges runtime-start telemetry."""
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            MapIterationInferenceStarted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                iteration_index=index,
                request_id=request_id,
                model_id=model_id,
                queue_wait_seconds=queue_wait_seconds,
            )
        )

    def emit_iteration_inference_fallback_used(
        self,
        *,
        index: int,
        request_id: str,
        fallback_signal: str,
        reason: str,
    ) -> None:
        """Emit fallback marker when primary signal never arrived."""
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            MapIterationInferenceFallback(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                iteration_index=index,
                request_id=request_id,
                fallback_signal=fallback_signal,
                reason=reason,
            )
        )

    def emit_iteration_inference_signal_lost(
        self,
        *,
        index: int,
        request_id: str,
    ) -> None:
        """Emit signal-lost marker when no boundary signal was observed."""
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            MapIterationInferenceLost(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                iteration_index=index,
                request_id=request_id,
            )
        )

    def emit_iteration_events(
        self,
        iteration_results: list[IterationResult],
        results_by_index: dict[int, StepOutput],
        key_by_idx: dict[int, str | None],
    ) -> None:
        """Emit per-iteration bus events and recorder events."""
        pipeline_id, execution_id = self.get_event_context()
        recorder = self._runtime.recorder

        for result in iteration_results:
            if result.status == IterationStatus.COMPLETED:
                # Bus event already emitted immediately in _tracked_iteration;
                # only the recorder path remains here.
                if recorder is not None:
                    out = results_by_index.get(result.index)
                    output_text = getattr(out, "text", "") if out else ""
                    prompt_tokens = getattr(out, "prompt_tokens", 0) if out else 0
                    completion_tokens = (
                        getattr(out, "completion_tokens", 0) if out else 0
                    )
                    recorder.emit(
                        MapIterationCompleted(
                            step_name=self._step.name,
                            model_id=result.model_id,
                            iteration_index=result.index,
                            iteration_key=key_by_idx.get(result.index) or "",
                            duration_ms=(result.duration_seconds or 0.0) * 1000,
                            output_text=output_text,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                        )
                    )
            else:
                failure_type = {
                    IterationStatus.TIMEOUT: "timeout",
                    IterationStatus.CANCELLED: "cancelled",
                }.get(result.status, "error")
                error_msg = result.error_message or f"Iteration {result.status.value}"
                self._emit_bus_iteration_failed(
                    pipeline_id=pipeline_id,
                    execution_id=execution_id,
                    result=result,
                    error_message=error_msg,
                    failure_type=failure_type,
                )

    def emit_iteration_completed_immediate(
        self,
        *,
        index: int,
        elapsed_seconds: float,
        inference_seconds: float | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """
        Emit per-iteration completed event immediately on success.

        Called from _tracked_iteration as each task resolves — provides
        real-time progress visibility rather than a burst at step end.
        ∀ successful iteration: one event emitted immediately on completion.
        """
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            BusMapIterationCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                iteration_index=index,
                elapsed_seconds=elapsed_seconds,
                inference_seconds=inference_seconds,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )

    def _emit_bus_iteration_completed(
        self,
        *,
        pipeline_id: str,
        execution_id: str,
        result: IterationResult,
    ) -> None:
        """Emit bus-level completed signal for one iteration (bulk path)."""
        self.publish_event(
            BusMapIterationCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                iteration_index=result.index,
                elapsed_seconds=result.duration_seconds or 0.0,
            )
        )

    def _emit_bus_iteration_failed(
        self,
        *,
        pipeline_id: str,
        execution_id: str,
        result: IterationResult,
        error_message: str,
        failure_type: str,
    ) -> None:
        """Emit bus-level failed signal for one iteration."""
        self.publish_event(
            MapIterationFailed(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                iteration_index=result.index,
                error=error_message,
                duration_seconds=result.duration_seconds,
                failure_type=failure_type,
                truncated_response=result.truncated_response,
                truncation_tokens=result.truncation_tokens,
            )
        )
