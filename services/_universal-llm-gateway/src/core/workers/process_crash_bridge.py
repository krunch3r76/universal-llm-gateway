"""
Bridge handler for translating process_ipc crash events to gateway crash events.

This module subscribes to PROCESS_CRASH_DETECTED from process_ipc and emits
WORKER_CRASH_DETECTED for consumption by gateway crash handlers.

Single responsibility: event translation only (no state mutations).
"""

from universal_logging import get_logger

from src.core.events import Event, EventBus

logger = get_logger(__name__)


class ProcessCrashBridge:
    """
    Translates process_ipc PROCESS_CRASH_DETECTED to gateway WORKER_CRASH_DETECTED.

    Ensures single crash signal topology within gateway while maintaining
    compatibility with process_ipc event schema.

    Invariant: ∀ PROCESS_CRASH_DETECTED, emit exactly one WORKER_CRASH_DETECTED
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize the process crash bridge.

        Args:
            event_bus: EventBus instance for subscribing/publishing
        """
        self.event_bus = event_bus
        self._bridge_count = 0

        # Subscribe to process_ipc crash events
        try:
            from process_ipc import PROCESS_CRASH_DETECTED

            self.event_bus.subscribe_async(
                PROCESS_CRASH_DETECTED, self._handle_process_crash_detected
            )
            logger.info(
                "🌉 ProcessCrashBridge initialized (subscribed to PROCESS_CRASH_DETECTED)"
            )
        except ImportError as e:
            logger.error(
                f"Failed to import PROCESS_CRASH_DETECTED from process_ipc: {e}"
            )
            raise

    async def _handle_process_crash_detected(self, event: Event):
        """
        Handle PROCESS_CRASH_DETECTED from process_ipc and emit WORKER_CRASH_DETECTED.

        Payload mapping:
            process_id → model_id
            All other fields passed through

        Args:
            event: Event containing process crash details from process_ipc
        """
        process_id = event.payload.get("process_id")
        if not process_id:
            logger.warning("PROCESS_CRASH_DETECTED event missing process_id")
            return

        self._bridge_count += 1

        # Map process_ipc schema to gateway schema
        from ..events.types import WorkerCrashDetected

        worker_crash_event = WorkerCrashDetected(
            model_id=process_id,  # process_id = model_id in gateway
            error_message=event.payload.get(
                "error_message", "Worker process crashed"
            ),
            socket_path=event.payload.get("socket_path"),
            process_pid=event.payload.get("pid"),
            exit_code=event.payload.get("exit_code"),
        )

        try:
            await self.event_bus.publish_async_nowait(worker_crash_event)
            logger.info(
                f"🌉 Bridged crash event: PROCESS_CRASH_DETECTED → WORKER_CRASH_DETECTED for {process_id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to publish WORKER_CRASH_DETECTED for {process_id}: {e}",
                exc_info=True,
            )

    def get_bridge_count(self) -> int:
        """Get total number of crash events bridged (for monitoring)."""
        return self._bridge_count
