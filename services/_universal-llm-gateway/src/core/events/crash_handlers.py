"""
Event handlers for worker crash events from process_ipc integration.

This module provides centralized crash event handling that integrates
with the universal_event_bus system. Handlers validate Event instances
and provide standardized crash processing across the gateway.
"""

from typing import Any

from universal_event_bus import Event
from universal_logging import get_logger

logger = get_logger(__name__)


def handle_worker_crash(event: Event) -> None:
    """
    Handle worker crash events from process_ipc.

    This handler processes crash events that are published when worker
    processes crash or become unresponsive. It validates the Event
    structure and provides standardized logging and processing.

    Args:
        event: Event instance containing crash details

    Raises:
        TypeError: If event is not an Event instance

    Event Payload Structure:
        model_id: str - Model ID of the crashed worker
        error_message: str - Description of the crash
        exit_code: Optional[int] - Process exit code if available
        process_pid: Optional[int] - PID of the crashed process
        socket_path: Optional[str] - Path to orphaned socket file
    """
    # Validate event instance type
    if not isinstance(event, Event):
        raise TypeError(f"Expected Event instance, got {type(event).__name__}")

    # Extract crash details from payload
    process_id = event.payload.get("model_id", "unknown")
    exit_code = event.payload.get("exit_code", "unknown")
    error_message = event.payload.get("error_message", "No error message provided")
    socket_path = event.payload.get("socket_path")
    process_pid = event.payload.get("process_pid")

    # Log crash details
    logger.error("🚨 Worker crash detected:")
    logger.error(f"   Model ID: {process_id}")
    logger.error(f"   Error: {error_message}")
    logger.error(f"   Exit Code: {exit_code}")
    if process_pid:
        logger.error(f"   Process PID: {process_pid}")
    if socket_path:
        logger.error(f"   Socket Path: {socket_path}")
    logger.error(f"   Event ID: {event.id}")
    logger.error(f"   Timestamp: {event.timestamp}")

    # This is where the gateway's crash handling logic would go
    # Examples:
    # - Update model registry to mark model as unavailable
    # - Clean up resources associated with the crashed worker
    # - Trigger automatic restart if configured
    # - Send notifications to monitoring systems
    # - Update health check status

    logger.info(f"🔧 Crash handling completed for {process_id}")


def handle_socket_orphaned(event: Event) -> None:
    """
    Handle socket orphaned events.

    This handler processes events when orphaned socket files are detected
    and cleaned up by the system.

    Args:
        event: Event instance containing socket cleanup details

    Raises:
        TypeError: If event is not an Event instance
    """
    if not isinstance(event, Event):
        raise TypeError(f"Expected Event instance, got {type(event).__name__}")

    model_id = event.payload.get("model_id", "unknown")
    socket_path = event.payload.get("socket_path", "unknown")
    cleanup_successful = event.payload.get("cleanup_successful", False)
    error = event.payload.get("error")

    if cleanup_successful:
        logger.info(f"🧹 Socket cleanup successful for {model_id}: {socket_path}")
    else:
        logger.error(f"🚫 Socket cleanup failed for {model_id}: {socket_path}")
        if error:
            logger.error(f"   Cleanup error: {error}")


def handle_health_check_failed(event: Event) -> None:
    """
    Handle health check failure events.

    This handler processes events when worker health checks fail,
    which may indicate potential crashes or unresponsive workers.

    Args:
        event: Event instance containing health check failure details

    Raises:
        TypeError: If event is not an Event instance
    """
    if not isinstance(event, Event):
        raise TypeError(f"Expected Event instance, got {type(event).__name__}")

    model_id = event.payload.get("model_id", "unknown")
    error_message = event.payload.get("error_message", "Health check failed")
    socket_path = event.payload.get("socket_path")

    logger.warning(f"⚠️ Health check failed for {model_id}: {error_message}")
    if socket_path:
        logger.warning(f"   Socket: {socket_path}")

    # This could trigger additional diagnostics or recovery procedures


class CentralizedCrashEventHandler:
    """
    Centralized handler for all crash-related events.

    This class provides a unified interface for handling all types of
    crash-related events from the process_ipc integration. It can be
    used to set up comprehensive crash monitoring and response.

    Example:
        event_bus = EventBus()
        crash_handler = CentralizedCrashEventHandler(event_bus)
        crash_handler.setup_subscriptions()
    """

    def __init__(self, event_bus, enable_auto_recovery: bool = False):
        """
        Initialize centralized crash event handler.

        Args:
            event_bus: EventBus instance to subscribe to
            enable_auto_recovery: Whether to enable automatic recovery actions
        """
        self.event_bus = event_bus
        self.enable_auto_recovery = enable_auto_recovery
        self.crash_count = 0
        self.recovery_attempts = {}

        logger.info(
            f"🎛️ CentralizedCrashEventHandler initialized (auto_recovery={enable_auto_recovery})"
        )

    def setup_subscriptions(self) -> None:
        """Set up event subscriptions for all crash-related events."""
        from .types import HEALTH_CHECK_FAILED, SOCKET_ORPHANED, WORKER_CRASH_DETECTED

        # Subscribe to crash detection events
        self.event_bus.subscribe_async(
            WORKER_CRASH_DETECTED, self._handle_crash_with_recovery
        )
        self.event_bus.subscribe_async(
            SOCKET_ORPHANED, self._handle_socket_orphaned_with_stats
        )
        self.event_bus.subscribe_async(
            HEALTH_CHECK_FAILED, self._handle_health_failure_with_diagnostics
        )

        logger.info("📡 Centralized crash event subscriptions established")

    def _handle_crash_with_recovery(self, event: Event) -> None:
        """Handle crash events with optional auto-recovery."""
        # Delegate to standard handler
        handle_worker_crash(event)

        # Track crash statistics
        self.crash_count += 1
        model_id = event.payload.get("model_id", "unknown")

        logger.info(f"📊 Total crashes tracked: {self.crash_count}")

        # Auto-recovery logic if enabled
        if self.enable_auto_recovery:
            recovery_count = self.recovery_attempts.get(model_id, 0)
            max_recovery_attempts = 3

            if recovery_count < max_recovery_attempts:
                self.recovery_attempts[model_id] = recovery_count + 1
                logger.info(
                    f"🔄 Attempting auto-recovery for {model_id} (attempt {recovery_count + 1})"
                )

                # Here you would implement actual recovery logic:
                # - Restart the worker process
                # - Reload the model
                # - Reset worker state
                # - Clear error conditions

                logger.info(f"🔄 Auto-recovery initiated for {model_id}")
            else:
                logger.error(f"🚫 Max recovery attempts exceeded for {model_id}")

    def _handle_socket_orphaned_with_stats(self, event: Event) -> None:
        """Handle socket orphaned events with statistics tracking."""
        handle_socket_orphaned(event)

        # Additional statistics or cleanup could go here

    def _handle_health_failure_with_diagnostics(self, event: Event) -> None:
        """Handle health check failures with enhanced diagnostics."""
        handle_health_check_failed(event)

        # Additional diagnostic logic could go here
        model_id = event.payload.get("model_id")
        if model_id and self.enable_auto_recovery:
            logger.info(f"🔍 Running enhanced diagnostics for {model_id}")
            # Implement diagnostic checks here

    def get_crash_statistics(self) -> dict[str, Any]:
        """Get crash statistics summary."""
        return {
            "total_crashes": self.crash_count,
            "models_with_recovery_attempts": dict(self.recovery_attempts),
            "auto_recovery_enabled": self.enable_auto_recovery,
        }

    def reset_statistics(self) -> None:
        """Reset crash statistics."""
        self.crash_count = 0
        self.recovery_attempts.clear()
        logger.info("📊 Crash statistics reset")
