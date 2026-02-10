"""Event handlers for worker cleanup events."""

from pathlib import Path

from universal_event_bus import Event
from universal_logging import get_logger

from .....core.events import get_event_bus

logger = get_logger(__name__)


async def handle_socket_cleanup(event: Event) -> None:
    """
    Handle socket cleanup event.

    Args:
        event: Socket cleanup request event
    """
    try:
        model_id = event.payload["model_id"]
        socket_path_str = event.payload["socket_path"]

        socket_path = Path(socket_path_str)
        if socket_path.exists():
            socket_path.unlink()
            logger.info(f"✅ Removed socket for {model_id}: {socket_path_str}")
        else:
            logger.debug(f"Socket already removed for {model_id}: {socket_path_str}")
    except Exception as e:
        model_id = event.payload.get("model_id", "unknown")
        logger.warning(
            f"⚠️ Error removing socket for {model_id}: {e}",
            exc_info=True,
        )


async def handle_supervisor_termination(event: Event) -> None:
    """
    Handle supervisor termination event.

    Note: Supervisor is already stopped by cleanup logic before event is published.
    This handler is for tracking/logging purposes and any additional cleanup.

    Args:
        event: Supervisor termination request event
    """
    model_id = event.payload["model_id"]
    supervisor_id = event.payload["supervisor_id"]

    logger.info(
        f"📋 Supervisor termination logged for {model_id} "
        f"(supervisor_id: {supervisor_id})"
    )


async def handle_resource_state_update(event: Event) -> None:
    """
    Handle resource tracker state update event.

    Args:
        event: Resource state update request event
    """
    try:
        from ....resources import ModelStatus, resource_tracker

        model_id = event.payload["model_id"]
        state = event.payload["state"]
        error = event.payload.get("error")

        if state == "failed" and error:
            # Mark as failed in resource tracker
            state_machine = resource_tracker.get_state_machine(model_id)
            if state_machine and hasattr(state_machine, "set_error"):
                state_machine.set_error(error)
                logger.info(f"📊 Marked {model_id} as failed in resource tracker")
        elif state == "unloaded":
            # Mark as unloaded
            resource_tracker.set_model_status(model_id, ModelStatus.NOT_LOADED)
            logger.info(f"📊 Marked {model_id} as unloaded in resource tracker")
    except Exception as e:
        model_id = event.payload.get("model_id", "unknown")
        logger.warning(
            f"⚠️ Error updating resource tracker for {model_id}: {e}",
            exc_info=True,
        )


async def handle_worker_cleanup(event: Event) -> None:
    """
    Handle overall worker cleanup event.

    This is published after all specific cleanup events.
    Can be used for telemetry, logging, or notifications.

    Args:
        event: Worker cleanup request event
    """
    model_id = event.payload["model_id"]
    reason = event.payload["reason"]
    supervisor_id = event.payload.get("supervisor_id")

    logger.info(
        f"🧹 Worker cleanup completed for {model_id}: {reason} "
        f"(supervisor: {supervisor_id})"
    )


def register_cleanup_event_handlers() -> None:
    """
    Register all cleanup event handlers with the event bus.

    Should be called during gateway initialization.
    """
    event_bus = get_event_bus()

    # Subscribe using dot-notation signal names
    event_bus.subscribe_async("worker.socket.cleanup.requested", handle_socket_cleanup)
    event_bus.subscribe_async(
        "worker.supervisor.termination.requested", handle_supervisor_termination
    )
    event_bus.subscribe_async(
        "worker.resource.state.update.requested", handle_resource_state_update
    )
    event_bus.subscribe_async("worker.cleanup.requested", handle_worker_cleanup)

    logger.info("✅ Registered cleanup event handlers")
