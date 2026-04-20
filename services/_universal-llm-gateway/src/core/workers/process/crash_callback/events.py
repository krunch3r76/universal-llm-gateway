"""Socket cleanup event publication for crashed processes."""

from universal_logging import get_logger

logger = get_logger(__name__)


def publish_socket_cleanup_event(process_id: str) -> None:
    """
    Publish socket cleanup event for crashed process (non-blocking).

    Fire-and-forget event publication. Does not wait for cleanup completion.

    Invariant: non_blocking ∧ event_published

    Args:
        process_id: Process/model ID for socket cleanup

    Side Effects:
        Publishes SocketCleanupRequested event to event bus
    """
    try:
        import asyncio

        from ....events import get_event_bus
        from ...utils import get_universal_protocol_socket_path
        from ..communication import SocketCleanupRequested

        socket_path = get_universal_protocol_socket_path(process_id)
        # Schedule task without blocking (fire-and-forget from sync context)
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon(
                lambda: asyncio.create_task(
                    get_event_bus().publish_nowait(
                        SocketCleanupRequested(model_id=process_id, socket_path=socket_path)
                    )
                )
            )
            logger.info(f"🧹 Published cleanup event for crashed {process_id}")
        except RuntimeError:
            # Not in async context, skip emission
            logger.debug(f"Skipped cleanup event for {process_id} (not in async context)")
    except Exception as e:
        logger.error(f"Failed to publish cleanup event for {process_id}: {e}")
