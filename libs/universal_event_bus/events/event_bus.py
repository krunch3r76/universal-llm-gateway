"""
Event Bus - Simple event-driven communication for Universal Stargate.

This module provides a clean event-driven architecture that eliminates
race conditions by design through loose coupling and no shared state.

Optional debug broadcasting can be enabled by setting a debug broadcaster.
"""

import asyncio
from collections.abc import Callable
from itertools import count
from typing import Any

from universal_logging import get_logger

from .event import Event, create_timestamp

logger = get_logger(__name__)


class Subscription:
    """
    Handle for unsubscribing from EventBus.

    Returned by subscribe_async(). Call unsubscribe() to remove handler.
    Idempotent: multiple unsubscribe() calls are safe.
    """

    __slots__ = ("_bus", "_signal", "_handler", "_active")

    def __init__(self, bus: "EventBus", signal: str, handler: Callable):
        self._bus = bus
        self._signal = signal
        self._handler = handler  # The wrapped handler (post-filtering)
        self._active = True

    def unsubscribe(self) -> bool:
        """
        Remove handler from EventBus.

        Returns:
            True if handler was removed, False if already unsubscribed.
        """
        if not self._active:
            logger.debug(f"Subscription already inactive for signal '{self._signal}'")
            return False

        handlers = self._bus._async_subscribers.get(self._signal, [])
        if self._handler in handlers:
            handlers.remove(self._handler)
            self._active = False
            logger.debug(f"Unsubscribed handler from signal '{self._signal}'")
            return True

        # Handler missing from list (unexpected but handle gracefully)
        logger.debug(
            f"Handler not found in subscribers for signal '{self._signal}' "
            f"(marking inactive)"
        )
        self._active = False
        return False

    @property
    def is_active(self) -> bool:
        """Check if subscription is still active."""
        return self._active


class EventBus:
    """
    Simple event bus for component communication.

    Components publish events and subscribe to events they care about.
    No shared mutable state - communication via immutable events only.

    Optional debug broadcasting can be enabled by setting a debug broadcaster.
    """

    def __init__(self, debug_broadcaster=None, udp_bridge=None):
        """
        Initialize event bus.

        Args:
            debug_broadcaster: Optional debug broadcaster for event monitoring
            udp_bridge: Optional UDP bridge for remote event monitoring
        """
        self._async_subscribers: dict[str, list[Callable]] = {}
        self.debug_broadcaster = debug_broadcaster
        self.udp_bridge = udp_bridge

        # Global counter for event IDs (thread-safe via itertools.count)
        self._event_counter = count(1)

        if debug_broadcaster:
            logger.info("🔍 Debug event broadcasting enabled")

        if udp_bridge:
            logger.info("📡 UDP event broadcasting enabled")

    def subscribe_async(
        self,
        signal: str,
        handler: Callable,
        payload_match: dict[str, Any] | None = None,
    ) -> Subscription:
        """
        Subscribe to event signal with optional payload filtering.

        Args:
            signal: Event signal name (e.g., "TaskCompleted")
            handler: Async function to call when event is published (MUST be async)
            payload_match: Optional dict of payload keys/values that must match.
                          If provided, handler only called for events where
                          event.payload[key] == value for all keys in dict.
                          If None (default), no filtering applied (backward compatible).

        Returns:
            Subscription handle for unsubscribing.

        Note: Handler must be async. The method name makes this requirement clear.

        Example:
            # No filtering (existing behavior)
            event_bus.subscribe_async("ModelLoaded", handler)

            # Single key filtering
            event_bus.subscribe_async(
                "WorkerCrashDetected",
                handler,
                payload_match={"model_id": "llama-3-8b"}
            )

            # Multiple key filtering (AND logic)
            event_bus.subscribe_async(
                "InferenceCompleted",
                handler,
                payload_match={
                    "model_id": "llama-3-8b",
                    "status": "success"
                }
            )
        """
        # Create wrapped handler if filtering needed
        if payload_match is not None:
            # Make a copy to prevent mutation after subscription
            match_criteria = payload_match.copy()

            async def filtered_handler(event: Event):
                # Check all payload_match conditions
                if isinstance(event.payload, dict):
                    for key, expected_value in match_criteria.items():
                        if event.payload.get(key) != expected_value:
                            # Filter failed - skip handler
                            return
                    # All filters passed - call original handler
                    return await handler(event)
                else:
                    # Payload is not dict-like - skip handler
                    return

            filtered_handler.__name__ = handler.__name__  # Preserve name for logging
            wrapped_handler = filtered_handler
        else:
            # No filtering - use handler directly
            wrapped_handler = handler

        # Register handler (existing logic)
        if signal not in self._async_subscribers:
            self._async_subscribers[signal] = []
        self._async_subscribers[signal].append(wrapped_handler)

        filter_info = f" with filter {payload_match}" if payload_match else ""

        # Include class name in debug message for clarity
        if hasattr(handler, "__self__") and handler.__self__ is not None:
            class_name = handler.__self__.__class__.__name__
            handler_info = f"{class_name}.{handler.__name__}"
        else:
            handler_info = handler.__name__

        logger.debug(
            f"Subscribed async {handler_info} to signal '{signal}'{filter_info}"
        )

        return Subscription(self, signal, wrapped_handler)

    def subscribe(
        self,
        signal: str,
        handler: Callable,
        payload_match: dict[str, Any] | None = None,
    ):
        """
        DEPRECATED: Use subscribe_async() instead.

        This method has a misleading name - handlers must be async despite the
        synchronous-sounding method name. Use subscribe_async() for clarity.

        Args:
            signal: Event signal name (e.g., "TaskCompleted")
            handler: Async function to call when event is published
            payload_match: Optional dict of payload keys/values that must match

        Raises:
            AttributeError: Always raised to force migration to subscribe_async()
        """
        raise AttributeError(
            "EventBus.subscribe() is deprecated. Use subscribe_async() instead. "
            "Handler must be an async function. "
            "The new name makes this requirement clear."
        )

    def set_debug_broadcaster(self, debug_broadcaster):
        """Set debug broadcaster for event monitoring."""
        self.debug_broadcaster = debug_broadcaster
        if debug_broadcaster:
            logger.info("🔍 Debug event broadcasting enabled")
        else:
            logger.info("🔍 Debug event broadcasting disabled")

    async def publish(self, event: Event) -> None:
        """
        Publish event and wait for all subscribers to complete.

        Use this when you need delivery guarantees or ordering constraints.
        For request-path events, use publish_nowait instead.
        For sync callers, use publish_from_sync instead.

        Automatically injects timestamp and global ID into the event.

        Args:
            event: Event instance to publish

        Raises:
            TypeError: If event is not an Event instance
        """
        if not isinstance(event, Event):
            raise TypeError(f"Expected Event instance, got {type(event).__name__}")

        # Inject timestamp and ID
        event.id = next(self._event_counter)
        event.timestamp = create_timestamp()

        # Call asynchronous handlers and wait for completion
        # Iterate over snapshot to allow safe unsubscribe during publish
        if event.signal in self._async_subscribers:
            tasks = []
            for handler in list(self._async_subscribers[event.signal]):
                try:
                    task = asyncio.create_task(handler(event))
                    tasks.append(task)
                except Exception as e:
                    logger.error(
                        f"Error creating async task for {handler.__name__}: {e}"
                    )

            # Wait for all handlers to complete
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        # Broadcast to debug clients (fire-and-forget)
        if self.debug_broadcaster:
            try:
                asyncio.create_task(self.debug_broadcaster.broadcast_event(event))
            except Exception as e:
                logger.debug(f"Debug broadcast failed: {e}")

        # Forward to UDP bridge (fire-and-forget)
        if self.udp_bridge:
            try:
                self.udp_bridge.forward_event(event)
            except Exception as e:
                logger.debug(f"UDP bridge forward failed: {e}")

    async def publish_nowait(self, event: Event) -> None:
        """
        Publish event without waiting for subscribers (fire-and-forget).

        Use this for request-path events where handler latency should not
        block the caller. Handler errors are logged but not propagated.

        This method is async (returns a coroutine) — callers must `await`.
        Sync callers must use `publish_from_sync` instead.

        Automatically injects timestamp and global ID into the event.

        Args:
            event: Event instance to publish

        Raises:
            TypeError: If event is not an Event instance
        """
        if not isinstance(event, Event):
            raise TypeError(f"Expected Event instance, got {type(event).__name__}")

        # Inject timestamp and ID
        event.id = next(self._event_counter)
        event.timestamp = create_timestamp()

        # Call asynchronous handlers (fire-and-forget with error logging)
        # Iterate over snapshot to allow safe unsubscribe during publish
        if event.signal in self._async_subscribers:
            for handler in list(self._async_subscribers[event.signal]):
                try:
                    task = asyncio.create_task(handler(event))
                    # Add done callback for error logging
                    task.add_done_callback(
                        lambda t, h=handler: self._log_handler_error(t, h)
                    )
                except Exception as e:
                    logger.error(
                        f"Error creating async task for {handler.__name__}: {e}"
                    )

        # Broadcast to debug clients (fire-and-forget)
        if self.debug_broadcaster:
            try:
                asyncio.create_task(self.debug_broadcaster.broadcast_event(event))
            except Exception as e:
                logger.debug(f"Debug broadcast failed: {e}")

        # Forward to UDP bridge (fire-and-forget)
        if self.udp_bridge:
            try:
                self.udp_bridge.forward_event(event)
            except Exception as e:
                logger.debug(f"UDP bridge forward failed: {e}")

    def publish_from_sync(self, event: Event) -> asyncio.Task:
        """
        Publish event from synchronous code (fire-and-forget).

        Schedules `publish_nowait(event)` on the running event loop and returns
        immediately. This is the only safe entrypoint for sync callers — the
        async `publish_nowait` would be silently dropped if called bare from
        sync code.

        Args:
            event: Event instance to publish

        Returns:
            The asyncio.Task scheduling the publish (usually ignored).

        Raises:
            RuntimeError: If no event loop is running in the current thread.
                Sync callers must be inside a thread that has a loop (e.g.
                a `run_in_executor` worker is NOT such a thread — use the
                loop's `call_soon_threadsafe` pattern instead).
            TypeError: If event is not an Event instance (raised when the
                scheduled coroutine runs, not at schedule time).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as e:
            raise RuntimeError(
                f"publish_from_sync({event.signal!r}) called with no running "
                f"event loop; sync callers must run inside a thread with an "
                f"active asyncio loop"
            ) from e
        return loop.create_task(self.publish_nowait(event))

    def _log_handler_error(self, task: asyncio.Task, handler: Callable) -> None:
        """Log errors from background event handlers."""
        try:
            task.result()
        except asyncio.CancelledError:
            pass  # Task was cancelled, normal shutdown
        except Exception as e:
            logger.error(
                f"Background event handler {handler.__name__} failed: {e}",
                exc_info=True,
            )

    def get_subscriber_count(self, signal: str) -> int:
        """
        Get number of subscribers for event signal.

        Args:
            signal: Event signal name

        Returns:
            Number of async subscribers
        """
        return len(self._async_subscribers.get(signal, []))

    def get_all_subscribers(self) -> dict[str, int]:
        """
        Get count of subscribers for all event signals.

        Returns:
            Dictionary mapping signal names to subscriber counts
        """
        return {
            signal: len(handlers)
            for signal, handlers in self._async_subscribers.items()
        }

    def get_debug_status(self) -> dict[str, Any]:
        """Get debug broadcasting status."""
        if not self.debug_broadcaster:
            return {"enabled": False, "client_count": 0}

        return {
            "enabled": True,
            "client_count": len(self.debug_broadcaster.debug_clients),
            "socket_path": self.debug_broadcaster.socket_path,
        }
