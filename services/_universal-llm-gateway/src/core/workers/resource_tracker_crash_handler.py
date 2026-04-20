"""
Event-driven resource tracker crash handler.

This module provides event-driven handling of resource tracker updates
when workers crash. It subscribes to crash events and automatically
updates the resource tracker to mark crashed models as not loaded.
"""

from universal_logging import get_logger

from src.core.events import WORKER_CRASH_DETECTED, Event, EventBus

logger = get_logger(__name__)


def _classify_crash_error(
    error_message: str,
    exit_code: int | None,
) -> tuple[str, str]:
    """Normalize crash text so load-time crashes surface as definitive failures."""
    normalized = (error_message or "Worker crashed").strip()
    lower = normalized.lower()
    oom_indicators = (
        "out of memory",
        "cuda out of memory",
        "cuda oom",
        "memory allocation failed",
        "cannot allocate",
        "oom",
    )
    if normalized.startswith("OOM:"):
        return normalized, "oom"
    if exit_code not in (None, 0):
        if exit_code in (137, 143):
            return f"OOM:{normalized} (exit_code={exit_code})", "oom"
        return f"{normalized} (exit_code={exit_code})", "unknown"
    if any(indicator in lower for indicator in oom_indicators):
        return f"OOM:{normalized}", "oom"
    return normalized, "unknown"


class ResourceTrackerCrashHandler:
    """
    Event-driven handler for resource tracker updates on worker crashes.

    Subscribes to crash events and automatically updates the resource
    tracker to mark crashed models as not loaded, clearing busy states.

    Edge cases handled:
    - Multiple concurrent crashes: Handler is async-safe
    - Crashes during loading: ResourceTracker domain verbs sync SM and derived status
    - Missing model_id: Logged and skipped
    - Resource tracker errors: Caught and logged, don't block other handlers
    """

    def __init__(self, event_bus: EventBus, worker_controller=None):
        """
        Initialize the resource tracker crash handler.

        Args:
            event_bus: EventBus instance for subscribing to events
            worker_controller: Optional WorkerController for proper model unloading
        """
        self.event_bus = event_bus
        self.worker_controller = worker_controller
        self._crash_count = 0  # Track total crashes for monitoring

        # Subscribe to crash events directly (no wrapper needed)
        self.event_bus.subscribe_async(WORKER_CRASH_DETECTED, self._handle_worker_crash)

        logger.info("🔧 ResourceTrackerCrashHandler initialized")

    async def _handle_worker_crash(self, event: Event):
        """
        Handle worker crash events by updating resource tracker.

        This handler ensures that crashed workers are immediately marked as
        unavailable in the resource tracker, allowing new requests to be
        properly routed to healthy workers.

        State transitions handled:
        - BUSY → NOT_LOADED (crashed during inference)
        - LOADING → NOT_LOADED (crashed during model load)
        - LOADED → NOT_LOADED (crashed while idle)
        - ERROR → NOT_LOADED (crashed in error state)

        Args:
            event: Event containing crash details
        """
        model_id = event.payload.get("model_id")
        error_message = event.payload.get("error_message", "Worker crashed")
        exit_code = event.payload.get("exit_code")

        if not model_id:
            logger.warning("Worker crash event missing model_id")
            return

        self._crash_count += 1
        logger.info(
            f"📊 Updating resource tracker for crashed worker {model_id} (crash #{self._crash_count})"
        )

        try:
            # Import resource tracker (lazy import to avoid circular dependencies)
            from src.core.resources import resource_tracker
            from src.core.resources.types import ModelStatus

            # Get current model state for debugging
            current_info = resource_tracker.get_model_info(model_id)
            crashed_while_loading = False
            if current_info:
                logger.debug(
                    f"Model {model_id} crashed in state: {current_info.status}"
                )
                crashed_while_loading = current_info.status == ModelStatus.LOADING

            classified_error, failure_reason = _classify_crash_error(
                error_message,
                exit_code,
            )

            # Set model to ERROR state so Stargate can poll and see the crash
            # The error message persists until explicitly cleared, allowing proper error reporting
            logger.info(
                f"🧹 [crash_handler] Setting crashed model {model_id} to ERROR state"
            )
            resource_tracker.set_model_error(
                model_id,
                classified_error if crashed_while_loading else f"Worker crashed: {error_message}",
            )

            if crashed_while_loading:
                from src.core.events.types import ModelLoadFailed

                await self.event_bus.publish_nowait(
                    ModelLoadFailed(
                        model_id=model_id,
                        error_message=classified_error,
                        failure_reason=failure_reason,
                    )
                )
                logger.info(
                    "📡 Emitted MODEL_LOAD_FAILED for crashed loading worker %s: %s "
                    "(exit_code=%s)",
                    model_id,
                    classified_error,
                    exit_code,
                )

            logger.info(f"✅ Resource tracker updated for crashed worker {model_id}")

        except Exception as e:
            logger.error(
                f"❌ Failed to update resource tracker for crashed worker {model_id}: {e}",
                exc_info=True,
            )

    def get_crash_count(self) -> int:
        """Get total number of crashes handled (for monitoring)."""
        return self._crash_count
