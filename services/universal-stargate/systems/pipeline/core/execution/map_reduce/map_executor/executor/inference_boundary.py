"""Inference-start boundary subscription and deferred-signal resolution.

Subscribes per ``execute()`` scope to two boundary signals — primary
``request.inference.started`` and fallback ``request.processing`` — and stamps
the per-iteration context with the first one observed. At iteration completion
the deferred resolver walks the context dict and decides per iteration: primary
already emitted (no-op), only fallback arrived (emit deferred inference.started
plus fallback.used), or neither arrived (emit signal.lost).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ....request_inference_boundary import (
    RequestInferenceBoundaryState,
    RequestInferenceBoundaryTracker,
)

if TYPE_CHECKING:
    from .map_executor import MapExecutor

logger = get_logger(__name__)


def subscribe_inference_start(
    executor: MapExecutor, iteration_context: dict[int, dict[str, Any]]
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
    proxy = getattr(executor._runtime, "_proxy", None)
    event_bus = getattr(proxy, "event_bus", None) if proxy else None
    request_id_to_idx: dict[str, int] = {
        ctx["request_id"]: idx
        for idx, ctx in iteration_context.items()
        if "request_id" in ctx
    }

    def _on_primary(rid: str, tracker_state: RequestInferenceBoundaryState) -> None:
        idx = request_id_to_idx.get(rid)
        if idx is None:
            return
        ctx = iteration_context.get(idx)
        if ctx is None or "inference_started_at" in ctx:
            return
        observation = tracker_state.inference_started
        if observation is None:
            return
        ctx["inference_started_at"] = observation.observed_at_monotonic
        ctx["inference_start_source"] = observation.signal
        ctx["inference_started_event"] = observation.payload
        queue_wait = ctx["inference_started_at"] - ctx["started_at"]
        executor._event_publisher.emit_iteration_inference_started(
            index=idx,
            request_id=rid,
            model_id=ctx.get("model_id"),
            queue_wait_seconds=round(queue_wait, 3),
        )

    def _on_fallback(rid: str, tracker_state: RequestInferenceBoundaryState) -> None:
        idx = request_id_to_idx.get(rid)
        if idx is None:
            return
        ctx = iteration_context.get(idx)
        if ctx is None or "fallback_boundary_at" in ctx:
            return
        observation = tracker_state.fallback_processing
        if observation is None:
            return
        ctx["fallback_boundary_at"] = observation.observed_at_monotonic
        ctx["fallback_boundary_signal"] = observation.signal
        ctx["fallback_boundary_event"] = observation.payload

    return RequestInferenceBoundaryTracker.subscribe(
        event_bus=event_bus,
        request_ids=request_id_to_idx.keys(),
        on_inference_started=_on_primary,
        on_processing=_on_fallback,
    )


def emit_deferred_inference_signals(
    executor: MapExecutor, iteration_context: dict[int, dict[str, Any]]
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
                    executor._runtime.execution_id,
                    executor._step.name,
                )
                fallback_warning_emitted = True
            ctx["inference_start_source"] = ctx.get(
                "fallback_boundary_signal", "request.processing"
            )
            if (
                "inference_started_event" not in ctx
                and "fallback_boundary_event" in ctx
            ):
                ctx["inference_started_event"] = ctx["fallback_boundary_event"]
            queue_wait = fallback_boundary_at - ctx["started_at"]
            executor._event_publisher.emit_iteration_inference_started(
                index=idx,
                request_id=request_id,
                model_id=ctx.get("model_id"),
                queue_wait_seconds=round(queue_wait, 3),
            )
            executor._event_publisher.emit_iteration_inference_fallback_used(
                index=idx,
                request_id=request_id,
                fallback_signal="request.processing",
                reason=(
                    "primary request.inference.started not received "
                    "before iteration completion"
                ),
            )
            continue

        executor._event_publisher.emit_iteration_inference_signal_lost(
            index=idx,
            request_id=request_id,
        )
