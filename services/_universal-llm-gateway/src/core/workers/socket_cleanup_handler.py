"""
Event-driven socket cleanup handler.

This module provides event-driven handling of socket cleanup.
It subscribes to crash events and automatically cleans up orphaned socket files.
"""

import asyncio
from pathlib import Path

from universal_logging import get_logger

from src.core.events import WORKER_CRASH_DETECTED, Event, EventBus

from .orphan_detector import OrphanedSocketDetector
from .socket_utils import safe_delete_socket

logger = get_logger(__name__)


class SocketCleanupHandler:
    """
    Event-driven handler for socket cleanup.

    Subscribes to crash events and automatically cleans up orphaned socket files.
    """

    def __init__(self, event_bus: EventBus, socket_dir: Path, worker_controller):
        """
        Initialize the socket cleanup handler.

        Args:
            event_bus: EventBus instance for publishing events
            socket_dir: Directory containing socket files
            worker_controller: WorkerController instance for orphan detection (required)
        """
        self.event_bus = event_bus
        self.socket_dir = socket_dir
        self.orphan_detector = OrphanedSocketDetector(worker_controller)

        # Limit concurrent cleanup operations to prevent resource exhaustion
        self._cleanup_semaphore = asyncio.Semaphore(10)  # Max 10 concurrent cleanups

        # Subscribe to crash events directly (no wrapper needed)
        self.event_bus.subscribe_async(WORKER_CRASH_DETECTED, self._handle_worker_crash)

        logger.info("🧹 SocketCleanupHandler initialized")

    async def _handle_worker_crash(self, event: Event):
        """
        Handle worker crash events by cleaning up sockets.

        Uses semaphore to limit concurrent cleanup operations.

        Args:
            event: Event containing crash details
        """
        model_id = event.payload.get("model_id")
        socket_path = event.payload.get("socket_path")

        if not model_id or not socket_path:
            logger.warning("Worker crash event missing model_id or socket_path")
            return

        # Limit concurrent cleanup operations
        async with self._cleanup_semaphore:
            logger.info(f"🧹 Handling socket cleanup for crashed worker {model_id}")

            try:
                # Clean up the socket file using safe deletion
                socket_file = Path(socket_path)
                if socket_file.exists():
                    if safe_delete_socket(socket_file):
                        logger.info(f"🧹 Cleaned up orphaned socket: {socket_path}")

                        # Publish cleanup success event
                        from ..events.types import SocketOrphaned

                        cleanup_event = SocketOrphaned(
                            model_id=model_id,
                            socket_path=socket_path,
                            cleanup_successful=True,
                        )
                        await self.event_bus.publish_async_nowait(cleanup_event)
                    else:
                        logger.warning(
                            f"Socket {socket_path} is in use, skipping cleanup"
                        )

                        # Publish cleanup event indicating socket is in use
                        from ..events.types import SocketOrphaned

                        cleanup_event = SocketOrphaned(
                            model_id=model_id,
                            socket_path=socket_path,
                            cleanup_successful=False,
                            error="Socket is in use",
                        )
                        await self.event_bus.publish_async_nowait(cleanup_event)

                else:
                    logger.warning(f"Socket file not found: {socket_path}")

                    # Publish cleanup event indicating socket was already gone
                    from ..events.types import SocketOrphaned

                    cleanup_event = SocketOrphaned(
                        model_id=model_id,
                        socket_path=socket_path,
                        cleanup_successful=True,
                    )
                    await self.event_bus.publish_async_nowait(cleanup_event)

            except Exception as e:
                logger.error(f"Failed to cleanup socket {socket_path}: {e}")

                # Publish cleanup failure event
                from ..events.types import SocketOrphaned

                cleanup_event = SocketOrphaned(
                    model_id=model_id,
                    socket_path=socket_path,
                    cleanup_successful=False,
                    error=str(e),
                )
                await self.event_bus.publish_async_nowait(cleanup_event)
