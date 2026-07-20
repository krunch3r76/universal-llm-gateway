"""Model loading lifecycle event emission for started, progress, and failed states."""

from typing import TYPE_CHECKING

from .deps import get_event_classes, publish_event
from .failure_classify import classify_load_failure

if TYPE_CHECKING:
    from ...controller import WorkerController


async def emit_loading_event(
    controller: "WorkerController",
    model_id: str,
    status: str,
    error: str | None = None,
) -> None:
    """Emit model loading lifecycle events (started, failed).

    Publishes MODEL_LOADING_STARTED or MODEL_LOAD_FAILED via the controller's
    event bus. Does not handle the "loaded" status - MODEL_LOADED is emitted
    by finalize_load() after resource measurement.

    On status="failed": attaches a best-effort worker_snapshot capturing
    peer worker processes, llama-cpp/vLLM child processes, and live
    hardware VRAM/RAM at failure time. Forensics-only — snapshot capture
    failures degrade silently and never block event emission.
    """
    model_load_failed, _, _, model_loading_started = get_event_classes()
    event_to_publish = None
    if status == "started":
        event_to_publish = model_loading_started(model_id=model_id)
    elif status == "failed":
        classified_error, failure_reason = classify_load_failure(error or "Unknown")
        from ..failure_snapshot import build_worker_snapshot

        worker_snapshot = build_worker_snapshot(controller, model_id)
        event_to_publish = model_load_failed(
            model_id=model_id,
            error_message=classified_error,
            failure_reason=failure_reason,
            worker_snapshot=worker_snapshot,
        )
    if event_to_publish:
        await publish_event(controller.event_bus, event_to_publish)


async def emit_loading_progress(
    controller: "WorkerController",
    model_id: str,
    phase: str,
    pct: int | float,
) -> None:
    """Publish model.loading.progress heartbeat during active load."""
    _, _, model_loading_progress, _ = get_event_classes()
    await publish_event(
        controller.event_bus,
        model_loading_progress(model_id=model_id, phase=phase, pct=pct),
    )
