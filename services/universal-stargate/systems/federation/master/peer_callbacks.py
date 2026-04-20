"""
Peer connection/disconnection callback factory.

Creates event-emitting callbacks that wrap optional user-provided callbacks.
Fire-and-forget tasks are tracked to surface exceptions via logging.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from universal_logging import get_logger

logger = get_logger(__name__)

type PeerCallback = Callable[[str], Awaitable[None]]

# Prevent fire-and-forget tasks from being garbage-collected before completion.
# Also surfaces exceptions via the done callback.
_background_tasks: set[asyncio.Task[None]] = set()


def _track_task(task: asyncio.Task[None]) -> None:
    """Register a fire-and-forget task and log exceptions on completion."""
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)


def _on_task_done(task: asyncio.Task[None]) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    if exc := task.exception():
        logger.error(f"Background event-bus publish failed: {exc}", exc_info=exc)


def build_peer_callbacks(
    *,
    event_bus: object | None,
    on_peer_connected: PeerCallback | None = None,
    on_peer_disconnected: PeerCallback | None = None,
) -> tuple[PeerCallback, PeerCallback]:
    """
    Build peer connection callbacks that emit federation events.

    Wraps optional user-provided callbacks with event emission logic.
    Returned callbacks are safe to call even when event_bus is None.

    Args:
        event_bus: Event bus for publishing federation events (or None)
        on_peer_connected: Optional additional callback on connection
        on_peer_disconnected: Optional additional callback on disconnection

    Returns:
        Tuple of (connected_callback, disconnected_callback)
    """

    async def emit_peer_connected(peer_id: str) -> None:
        logger.info(f"Peer {peer_id} connected")
        if event_bus:
            from src.scheduling.events import FederationConnectionEstablished

            task = asyncio.create_task(
                event_bus.publish_nowait(  # type: ignore[union-attr]
                    FederationConnectionEstablished(
                        remote_id=peer_id,
                        transport="websocket",
                    )
                )
            )
            _track_task(task)
        if on_peer_connected:
            await on_peer_connected(peer_id)

    async def emit_peer_disconnected(peer_id: str) -> None:
        logger.warning(f"Peer {peer_id} disconnected")
        if event_bus:
            from src.scheduling.events import FederationConnectionLost

            task = asyncio.create_task(
                event_bus.publish_nowait(  # type: ignore[union-attr]
                    FederationConnectionLost(
                        remote_id=peer_id,
                        reason="disconnected",
                    )
                )
            )
            _track_task(task)
        if on_peer_disconnected:
            await on_peer_disconnected(peer_id)

    return emit_peer_connected, emit_peer_disconnected
