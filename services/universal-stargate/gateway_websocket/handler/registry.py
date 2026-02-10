"""Handler registry for WebSocket message dispatch."""

from typing import Any

from universal_logging import get_logger

from ..messages import MessageType
from .base import AsyncMessageHandler, SyncMessageHandler
from .context import HandlerContext

logger = get_logger(__name__)


class HandlerRegistry:
    """
    Registry mapping message types to handlers.

    Replaces monolithic if/elif chain with O(1) dispatch.

    Invariant: ∀ msg_type ∈ MessageType, ∃ handler ∨ logged warning
    """

    def __init__(self) -> None:
        self._sync_handlers: dict[str, SyncMessageHandler] = {}
        self._async_handlers: dict[str, AsyncMessageHandler] = {}

    def register_sync(self, msg_type: MessageType, handler: SyncMessageHandler) -> None:
        """Register a synchronous handler."""
        self._sync_handlers[msg_type.value] = handler

    def register_async(
        self, msg_type: MessageType, handler: AsyncMessageHandler
    ) -> None:
        """Register an asynchronous handler."""
        self._async_handlers[msg_type.value] = handler

    async def dispatch(
        self, msg_type: str | None, data: dict[str, Any], ctx: HandlerContext
    ) -> None:
        """
        Dispatch message to appropriate handler.

        Args:
            msg_type: Message type string (may be None for malformed messages)
            data: Message payload
            ctx: Handler context

        Non-blocking: Sync handlers execute inline; async handlers await minimal I/O.
        """
        if msg_type is None:
            logger.warning("Received message with missing 'type' field")
            return

        # DEBUG: Log dispatch for model loaded
        if msg_type == MessageType.MODEL_LOADED.value:
            logger.info(f"🔍 DEBUG: Dispatching {msg_type} to handler")

        if msg_type in self._sync_handlers:
            self._sync_handlers[msg_type].handle(data, ctx)
        elif msg_type in self._async_handlers:
            await self._async_handlers[msg_type].handle(data, ctx)
        else:
            logger.debug(f"Unhandled message type: {msg_type}")

    def verify_coverage(self, expected_types: set[MessageType]) -> list[str]:
        """
        Verify all expected message types have handlers.

        Args:
            expected_types: Set of MessageType values that should have handlers

        Returns:
            List of missing message type names (empty if all covered)
        """
        registered = set(self._sync_handlers.keys()) | set(self._async_handlers.keys())
        expected = {mt.value for mt in expected_types}
        missing = expected - registered
        return list(missing)
