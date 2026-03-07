"""Map executor event publishing."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

from universal_event_bus import Event

from ....events.lifecycle import MapIterationCompleted
from ....events.map import (
    MapIterationCompleted as BusMapIterationCompleted,
)
from ....events.map import (
    MapIterationFailed,
    MapIterationInferenceFallbackUsed,
    MapIterationInferenceSignalLost,
    MapIterationInferenceStarted,
    MapIterationStarted,
    MapStepStarted,
)
from ....events.map import MapStepCompleted as BusMapStepCompleted
from ..iteration_state import IterationResult, IterationStatus

if TYPE_CHECKING:
    from ....schemas import StepConfig, StepOutput


class RuntimeProtocol(Protocol):
    """Runtime contract needed by map event publishing paths."""

    pipeline: Any
    execution_id: str
    recorder: Any
    _proxy: Any


logger = logging.getLogger(__name__)

# Naming note:
# - `BusMapIterationCompleted` is the global event-bus signal factory.
# - `MapIterationCompleted` (from lifecycle) is recorder-only output with richer fields.


class MapEventPublisher:
    """Handles all event publishing for map step execution."""

    def __init__(self, step: StepConfig, runtime: RuntimeProtocol) -> None:
        self._step = step
        self._runtime = runtime

    def get_event_context(self) -> tuple[str, str]:
        """Extract pipeline_id and execution_id from runtime context."""
        pipeline_obj = getattr(self._runtime, "pipeline", None)
        pipeline_id = (
            getattr(pipeline_obj, "id", "unknown") if pipeline_obj else "unknown"
        )
        execution_id = getattr(self._runtime, "execution_id", "unknown")
        return pipeline_id, execution_id

    def publish_event(self, event: Event) -> None:
        """Publish event via runtime's event bus (fire-and-forget)."""
        proxy = getattr(self._runtime, "_proxy", None)
        event_bus = getattr(proxy, "event_bus", None) if proxy else None
        if event_bus:
            _ = asyncio.create_task(event_bus.publish_async_nowait(event))

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
            MapIterationInferenceFallbackUsed(
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
            MapIterationInferenceSignalLost(
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
        recorder = getattr(self._runtime, "recorder", None)

        for result in iteration_results:
            if result.status == IterationStatus.COMPLETED:
                self._emit_bus_iteration_completed(
                    pipeline_id=pipeline_id,
                    execution_id=execution_id,
                    result=result,
                )
                if recorder:
                    out = results_by_index.get(result.index)
                    recorder.emit(
                        MapIterationCompleted(
                            step_name=self._step.name,
                            model_id=result.model_id,
                            iteration_index=result.index,
                            iteration_key=key_by_idx.get(result.index) or "",
                            duration_ms=(result.duration_seconds or 0.0) * 1000,
                            output_text=(getattr(out, "text", "") if out else ""),
                            prompt_tokens=(
                                getattr(out, "prompt_tokens", 0) if out else 0
                            ),
                            completion_tokens=(
                                getattr(out, "completion_tokens", 0) if out else 0
                            ),
                        )
                    )
            else:
                if result.status == IterationStatus.TIMEOUT:
                    failure_type = "timeout"
                elif result.status == IterationStatus.CANCELLED:
                    failure_type = "cancelled"
                else:
                    failure_type = "error"
                error_msg = result.error_message or f"Iteration {result.status.value}"
                self._emit_bus_iteration_failed(
                    pipeline_id=pipeline_id,
                    execution_id=execution_id,
                    result=result,
                    error_message=error_msg,
                    failure_type=failure_type,
                )

    def _emit_bus_iteration_completed(
        self,
        *,
        pipeline_id: str,
        execution_id: str,
        result: IterationResult,
    ) -> None:
        """Emit bus-level completed signal for one iteration."""
        self.publish_event(
            BusMapIterationCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                iteration_index=result.index,
                duration_seconds=result.duration_seconds or 0.0,
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
            )
        )
