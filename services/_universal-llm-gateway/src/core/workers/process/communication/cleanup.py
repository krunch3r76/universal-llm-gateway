"""Worker cleanup and resource management - event-driven."""

from typing import Any

from process_ipc import ProcessSupervisor
from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from .....core.events import get_event_bus
from .config_builder import get_force_stop_timeout

logger = get_logger(__name__)


# Event factory functions (return proper Event objects with signal + payload)
@event_factory
def SocketCleanupRequested(model_id: str, socket_path: str) -> Event:
    """
    Create socket cleanup request event.
    
    Args:
        model_id: Model identifier
        socket_path: Path to socket file
        
    Returns:
        Event with SocketCleanupRequested signal
    """
    return Event(
        signal="worker.socket.cleanup.requested",
        payload={"model_id": model_id, "socket_path": socket_path},
    )


@event_factory
def WorkerCleanupRequested(
    model_id: str, socket_path: str, supervisor_id: str | None, reason: str
) -> Event:
    """
    Create worker cleanup request event.
    
    Args:
        model_id: Model identifier
        socket_path: Path to socket file
        supervisor_id: Optional supervisor ID
        reason: Cleanup reason
        
    Returns:
        Event with WorkerCleanupRequested signal
    """
    return Event(
        signal="worker.cleanup.requested",
        payload={
            "model_id": model_id,
            "socket_path": socket_path,
            "supervisor_id": supervisor_id,
            "reason": reason,
        },
    )


@event_factory
def SupervisorTerminationRequested(model_id: str, supervisor_id: str) -> Event:
    """
    Create supervisor termination request event.
    
    Args:
        model_id: Model identifier
        supervisor_id: Supervisor ID to terminate
        
    Returns:
        Event with SupervisorTerminationRequested signal
    """
    return Event(
        signal="worker.supervisor.termination.requested",
        payload={"model_id": model_id, "supervisor_id": supervisor_id},
    )


@event_factory
def ResourceStateUpdateRequested(
    model_id: str, state: str, error: str | None = None
) -> Event:
    """
    Create resource state update request event.
    
    Args:
        model_id: Model identifier
        state: Target state ("failed", "unloaded")
        error: Optional error message
        
    Returns:
        Event with ResourceStateUpdateRequested signal
    """
    return Event(
        signal="worker.resource.state.update.requested",
        payload={"model_id": model_id, "state": state, "error": error},
    )


async def cleanup_failed_worker(
    model_id: str,
    supervisor: ProcessSupervisor | None,
    gateway_config: Any,
    socket_path: str,
    error_message: str,
) -> None:
    """
    Clean up worker resources after failure (event-driven).

    Publishes cleanup events in order:
    1. Update resource tracker (mark as failed)
    2. Stop supervisor process (if applicable)
    3. Remove socket
    4. Overall cleanup event

    Invariant: ∀ cleanup: resource_update_event → supervisor_term_event → socket_cleanup_event

    Args:
        model_id: Model identifier
        supervisor: ProcessSupervisor instance (optional)
        gateway_config: Gateway configuration
        socket_path: Path to worker socket
        error_message: Error message describing failure
    """
    logger.info(f"🧹 Publishing cleanup events for worker {model_id}: {error_message}")

    event_bus = get_event_bus()

    try:
        # 1. Publish resource tracker update event
        await event_bus.publish_async(
            ResourceStateUpdateRequested(
                model_id=model_id,
                state="failed",
                error=f"Worker initialization failed: {error_message}",
            )
        )

        # 2. Stop supervisor if available and publish termination event
        if supervisor:
            try:
                timeout = get_force_stop_timeout(gateway_config)
                await supervisor.stop(force=True, timeout=timeout)

                if hasattr(supervisor, "id"):
                    await event_bus.publish_async(
                        SupervisorTerminationRequested(
                            model_id=model_id,
                            supervisor_id=supervisor.id,
                        )
                    )
            except Exception as supervisor_error:
                logger.warning(
                    f"⚠️ Error stopping supervisor for {model_id}: {supervisor_error}"
                )

        # 3. Publish socket cleanup event
        await event_bus.publish_async(
            SocketCleanupRequested(
                model_id=model_id,
                socket_path=socket_path,
            )
        )

        # 4. Publish overall cleanup event
        await event_bus.publish_async(
            WorkerCleanupRequested(
                model_id=model_id,
                socket_path=socket_path,
                supervisor_id=getattr(supervisor, "id", None) if supervisor else None,
                reason=error_message,
            )
        )

        # Reset model state to allow retry
        _reset_model_state_after_cleanup(model_id, cleanup_succeeded=True)

    except Exception as cleanup_error:
        logger.warning(
            f"⚠️ Error during cleanup of failed worker {model_id}: {cleanup_error}"
        )

        # Reset state even if cleanup partially failed
        _reset_model_state_after_cleanup(model_id, cleanup_succeeded=False)


def _reset_model_state_after_cleanup(
    model_id: str,
    cleanup_succeeded: bool,
) -> None:
    """
    Reset model state to NOT_LOADED after cleanup to allow retry.

    This ensures ERROR state doesn't permanently block retries.

    Args:
        model_id: Model identifier
        cleanup_succeeded: Whether cleanup succeeded
    """
    try:
        from ....resources import ModelStatus, resource_tracker

        current_info = resource_tracker.get_model_info(model_id)
        if current_info and current_info.status == ModelStatus.ERROR:
            reason = (
                "Process cleanup - allowing retry"
                if cleanup_succeeded
                else "Failed cleanup - still allowing retry"
            )

            logger.info(
                f"🔄 Resetting {model_id} from ERROR to NOT_LOADED to allow retry "
                + f"(cleanup {'succeeded' if cleanup_succeeded else 'failed'})"
            )

            state_machine = resource_tracker.get_state_machine(model_id)
            if state_machine:
                # Clear error state (ERROR → UNLOADED transition)
                clear_success = state_machine.clear_error(reason)
                if clear_success:
                    # Update resource tracker to match
                    resource_tracker.set_model_status(model_id, ModelStatus.NOT_LOADED)
                    logger.info(f"✅ Successfully reset {model_id} state after cleanup")
                else:
                    logger.warning(
                        f"⚠️ Failed to clear error state for {model_id} via state machine"
                    )
            else:
                # Fallback: directly reset without state machine
                resource_tracker.set_model_status(model_id, ModelStatus.NOT_LOADED)
                logger.info(f"✅ Directly reset {model_id} state after cleanup")

    except Exception as state_reset_error:
        logger.warning(
            f"⚠️ Could not reset model state for {model_id} after cleanup: "
            + f"{state_reset_error}"
        )


def determine_error_type_and_code(error_msg: str) -> tuple[str, str]:
    """
    Determine error type and code from error message.

    Args:
        error_msg: Error message

    Returns:
        Tuple of (error_type, error_code)
    """
    from ....errors import ErrorCode, ErrorType

    error_type = ErrorType.INITIALIZATION_ERROR
    error_code = ErrorCode.WORKER_INITIALIZATION_FAILED

    # Check for syntax errors
    if "syntax error" in error_msg.lower() or "expected" in error_msg.lower():
        error_type = ErrorType.SYNTAX_ERROR
        error_code = ErrorCode.SYNTAX_ERROR

    return error_type, error_code


async def cleanup_syntax_error_worker(
    model_id: str,
    supervisor: ProcessSupervisor | None,
    gateway_config: Any,
    socket_path: str,
) -> None:
    """
    Clean up worker after syntax error (event-driven).

    Args:
        model_id: Model identifier
        supervisor: ProcessSupervisor instance (optional)
        gateway_config: Gateway configuration
        socket_path: Path to worker socket
    """
    logger.info(f"🧹 Publishing cleanup events for syntax error in {model_id}")

    event_bus = get_event_bus()

    try:
        # Stop supervisor if available
        if supervisor:
            timeout = get_force_stop_timeout(gateway_config)
            await supervisor.stop(force=True, timeout=timeout)

            if hasattr(supervisor, "id"):
                await event_bus.publish_async(
                    SupervisorTerminationRequested(
                        model_id=model_id,
                        supervisor_id=supervisor.id,
                    )
                )

        # Publish socket cleanup event
        await event_bus.publish_async(
            SocketCleanupRequested(
                model_id=model_id,
                socket_path=socket_path,
            )
        )
    except Exception as cleanup_error:
        logger.warning(
            f"⚠️ Error during cleanup of failed worker {model_id}: {cleanup_error}"
        )
