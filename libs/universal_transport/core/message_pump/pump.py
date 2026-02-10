"""
Message pump implementation.

Provides concurrent I/O, correlation matching, and message queuing for transport-based communication.

Event-Driven Cleanup:
    Each correlation manages its own expiration via asyncio.Task.
    No periodic cleanup loop - expiration tasks fire after inactivity timeout.
"""

import asyncio
from universal_logging import get_logger
import time
from collections.abc import Callable
from typing import Any

from ..exceptions import TransportError
from ..interfaces import Transport
from .interfaces import MessagePumpInterface

logger = get_logger(__name__)

# Default correlation timeout (5 minutes)
CORRELATION_TIMEOUT = 300.0


def default_get_correlation_id(message: dict[str, Any]) -> str | None:
    """
    Default correlation ID extractor.

    Looks for common correlation ID fields in messages.

    Args:
        message: Message dictionary

    Returns:
        Correlation ID if found, None otherwise
    """
    return (
        message.get("correlation_id")
        or message.get("correlationId")
        or message.get("id")
    )


class MessagePump(MessagePumpInterface):
    """
    Message pump for concurrent I/O with correlation matching.

    Provides:
    - Concurrent message sending and receiving
    - Correlation-based request/response matching
    - Message queuing for pending requests
    - Automatic error handling and reconnection support

    Event-Driven Architecture:
        Each correlation has its own expiration task. No cleanup loop.
        Expiration tasks are cancelled on unregister.

    **Single-Reader Semantics**:
    When the message pump is running (via start() or send_request()), the pump's
    receive loop is the EXCLUSIVE reader from the transport. Methods like read_message()
    and receive() are disabled during this time to prevent concurrency errors where
    multiple coroutines attempt to read from the transport simultaneously.

    Always use send_request() for request/response patterns when the pump is running.

    Attributes:
        transport: Underlying transport instance
        get_correlation_id: Function to extract correlation ID from messages
        pending_requests: Dictionary mapping correlation IDs to response futures
        receive_task: Background task for receiving messages
        _running: Whether the pump is running
    """

    def __init__(
        self,
        transport: Transport,
        get_correlation_id: Callable[[dict[str, Any]], str | None] | None = None,
        receive_timeout: float | None = 30.0,
    ):
        """
        Initialize message pump.

        Args:
            transport: Transport instance to use
            get_correlation_id: Optional function to extract correlation ID from messages.
                                Defaults to looking for 'correlation_id', 'correlationId', or 'id'.
            receive_timeout: Timeout for each transport.receive() call in seconds.
                           Prevents indefinite hangs from incomplete socket data.
                           Set to None to disable timeout (not recommended).
                           Default: 30.0 seconds.
        """
        self.transport = transport
        self.get_correlation_id = get_correlation_id or default_get_correlation_id
        self.receive_timeout = receive_timeout

        # Pending requests: correlation_id -> asyncio.Future
        self.pending_requests: dict[str, asyncio.Future] = {}
        self.request_signals: dict[str, str] = {}

        # Message queue for worker consumption
        self.message_queue: asyncio.Queue = asyncio.Queue()

        # Correlation-specific queues for streaming (eliminates republish bottleneck)
        self.correlation_queues: dict[str, asyncio.Queue] = {}

        # Correlation lifecycle tracking with metadata
        self.correlation_metadata: dict[str, dict[str, Any]] = {}

        # Per-correlation expiration tasks (event-driven cleanup)
        self._correlation_expiration_tasks: dict[str, asyncio.Task] = {}

        # Receive task
        self.receive_task: asyncio.Task | None = None
        self._running = False

        # Statistics for monitoring
        self._receive_timeout_count = 0
        self._messages_received_count = 0

        logger.debug(
            f"MessagePump initialized with transport: {transport}, receive_timeout: {receive_timeout}s"
        )

    @property
    def is_running(self) -> bool:
        """
        Check if the message pump is currently running.

        Returns:
            bool: True if the pump is running, False otherwise
        """
        return self._running

    def is_idle(self) -> bool:
        """
        Check if the message pump is in an idle state.

        The pump is considered idle when it has no pending requests and no
        registered correlation queues (i.e., no active streaming operations).

        This is used to determine whether a receive timeout is expected (idle)
        or unexpected (active operations waiting for data).

        Returns:
            bool: True if the pump is idle (no pending work), False otherwise
        """
        return len(self.pending_requests) == 0 and len(self.correlation_queues) == 0

    async def start(self) -> None:
        """
        Start the message pump.

        Begins background receive task for correlation matching.
        """
        if self._running:
            logger.warning("MessagePump already running")
            return

        if not self.transport.is_connected():
            raise TransportError(
                "Transport not connected. Call transport.connect() first."
            )

        self._running = True
        self.receive_task = asyncio.create_task(self._receive_loop())

        logger.info("MessagePump started (per-correlation expiration)")

    async def stop(self) -> None:
        """
        Stop the message pump.

        Stops the receive task and cancels pending requests.
        """
        if not self._running:
            return

        self._running = False

        # Cancel all per-correlation expiration tasks
        for task in self._correlation_expiration_tasks.values():
            if not task.done():
                task.cancel()
        self._correlation_expiration_tasks.clear()

        if self.receive_task:
            self.receive_task.cancel()
            try:
                await self.receive_task
            except asyncio.CancelledError:
                pass

        # Cancel all pending requests
        for correlation_id, future in self.pending_requests.items():
            if not future.done():
                future.cancel()

        self.pending_requests.clear()
        self.request_signals.clear()

        # Clear the message queue to prevent stale messages
        while not self.message_queue.empty():
            try:
                self.message_queue.get_nowait()
            except asyncio.QueueEmpty:
                # Should not happen due to the empty() check, but included for safety
                break

        # Clear correlation-specific queues and metadata
        for queue in self.correlation_queues.values():
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self.correlation_queues.clear()
        self.correlation_metadata.clear()

        logger.info("MessagePump stopped")

    async def read_message(self) -> dict[str, Any]:
        """
        Read a message from the transport.

        This method is provided for interface compatibility but is typically
        not called directly. Instead, use send_request() for request/response
        patterns or start() the pump and use send()/receive() methods.

        **IMPORTANT**: This method cannot be used when the message pump is running,
        as the pump's receive loop is the exclusive reader from the transport.
        Use send_request() instead for request/response patterns.

        Returns:
            Dict[str, Any]: Received message

        Raises:
            TransportError: If pump is running (use send_request() instead)
        """
        if self._running:
            raise TransportError(
                "Cannot call read_message() while message pump is running. "
                "The pump's receive loop is the exclusive reader from the transport. "
                "Use send_request() for request/response patterns, or stop() the pump first."
            )
        return await self.transport.receive()

    async def write_message(self, message: dict[str, Any]) -> None:
        """
        Write a message to the transport.

        Args:
            message: Message to send
        """
        await self.transport.send(message)

    async def send(self, message: dict[str, Any]) -> None:
        """
        Send a message without expecting a response.

        Args:
            message: Message to send
        """
        await self.transport.send(message)

    async def receive(self) -> dict[str, Any]:
        """
        Receive a message (for non-correlated communication).

        **IMPORTANT**: This method cannot be used when the message pump is running,
        as the pump's receive loop is the exclusive reader from the transport.
        Use send_request() instead for request/response patterns.

        Returns:
            Dict[str, Any]: Received message

        Raises:
            TransportError: If pump is running (use send_request() instead)
        """
        if self._running:
            raise TransportError(
                "Cannot call receive() while message pump is running. "
                "The pump's receive loop is the exclusive reader from the transport. "
                "Use send_request() for request/response patterns, or stop() the pump first."
            )
        return await self.transport.receive()

    async def get_message(self, timeout: float | None = None) -> dict[str, Any] | None:
        """
        Get next message from the receive loop (for workers).

        This method is safe to call when the pump is running because it reads
        from the internal queue, not directly from the transport.

        The receive loop automatically queues non-correlated messages (messages
        that don't match pending requests) for consumption by workers.

        Args:
            timeout: Maximum time to wait for a message (in seconds). If None, waits indefinitely.

        Returns:
            Message dict or None if timeout

        Raises:
            TransportError: If pump is not running
        """
        if not self._running:
            raise TransportError(
                "Cannot call get_message() when message pump is not running. "
                "Call start() first to begin the receive loop."
            )

        try:
            if timeout is not None:
                return await asyncio.wait_for(self.message_queue.get(), timeout=timeout)
            else:
                return await self.message_queue.get()
        except TimeoutError:
            return None

    def register_correlation(
        self, correlation_id: str, timeout: float = CORRELATION_TIMEOUT
    ) -> None:
        """
        Register a correlation ID for direct message routing.

        Creates a dedicated queue for this correlation ID, eliminating
        the need for message filtering and republishing (O(1) vs O(N)).

        Each correlation has its own expiration task that fires after
        the timeout period of inactivity.

        Args:
            correlation_id: Correlation ID to register
            timeout: Expiration timeout in seconds (default: 5 minutes)
        """
        if correlation_id in self.correlation_queues:
            # Already registered, refresh expiration
            self._refresh_correlation_expiration(correlation_id, timeout)
            return

        now = time.time()
        self.correlation_queues[correlation_id] = asyncio.Queue()
        self.correlation_metadata[correlation_id] = {
            "created_at": now,
            "last_activity": now,
            "message_count": 0,
            "timeout": timeout,
        }

        # Schedule per-correlation expiration task
        self._schedule_correlation_expiration(correlation_id, timeout)

        logger.debug(f"Registered correlation queue: {correlation_id}")

    def _schedule_correlation_expiration(
        self, correlation_id: str, timeout: float
    ) -> None:
        """Schedule expiration task for a correlation."""
        # Cancel existing task if present
        if correlation_id in self._correlation_expiration_tasks:
            task = self._correlation_expiration_tasks[correlation_id]
            if not task.done():
                task.cancel()

        task = asyncio.create_task(
            self._expire_correlation(correlation_id, timeout),
            name=f"correlation-expire-{correlation_id[:8]}",
        )
        self._correlation_expiration_tasks[correlation_id] = task

    async def _expire_correlation(self, correlation_id: str, timeout: float) -> None:
        """Expire a single correlation after timeout."""
        try:
            await asyncio.sleep(timeout)

            # Still exists and no recent activity?
            if correlation_id in self.correlation_metadata:
                metadata = self.correlation_metadata[correlation_id]
                elapsed = time.time() - metadata["last_activity"]

                if elapsed >= timeout:
                    # Expired - clean it up
                    self.unregister_correlation(correlation_id)
                    logger.warning(
                        f"Correlation {correlation_id[:8]} expired after "
                        f"{elapsed:.1f}s inactivity"
                    )

        except asyncio.CancelledError:
            pass  # Cancelled on unregister or activity refresh

    def _refresh_correlation_expiration(
        self, correlation_id: str, timeout: float | None = None
    ) -> None:
        """Refresh correlation expiration (called on activity)."""
        if correlation_id not in self.correlation_metadata:
            return

        self.correlation_metadata[correlation_id]["last_activity"] = time.time()

        # Use provided timeout or existing timeout
        if timeout is None:
            timeout = self.correlation_metadata[correlation_id].get(
                "timeout", CORRELATION_TIMEOUT
            )

        # Re-schedule expiration task
        self._schedule_correlation_expiration(correlation_id, timeout)

    def unregister_correlation(self, correlation_id: str) -> None:
        """
        Unregister a correlation ID and clean up its queue.

        Cancels the expiration task for this correlation.

        Args:
            correlation_id: Correlation ID to unregister
        """
        # Cancel expiration task
        if correlation_id in self._correlation_expiration_tasks:
            task = self._correlation_expiration_tasks.pop(correlation_id)
            if not task.done():
                task.cancel()

        if correlation_id in self.correlation_queues:
            queue = self.correlation_queues.pop(correlation_id)
            # Clear any remaining messages
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            # Remove metadata
            self.correlation_metadata.pop(correlation_id, None)
            logger.debug(f"Unregistered correlation queue: {correlation_id}")

    async def get_message_for_correlation(
        self, correlation_id: str, timeout: float | None = None
    ) -> dict[str, Any] | None:
        """
        Get next message for a specific correlation ID.

        This method provides O(1) message routing by using dedicated queues
        per correlation ID. No filtering or republishing needed.

        Args:
            correlation_id: Correlation ID to get message for
            timeout: Maximum time to wait for a message (in seconds)

        Returns:
            Message dict or None if timeout

        Raises:
            TransportError: If pump is not running or correlation not registered
        """
        if not self._running:
            raise TransportError(
                "Cannot call get_message_for_correlation() when message pump is not running. "
                "Call start() first to begin the receive loop."
            )

        if correlation_id not in self.correlation_queues:
            raise TransportError(
                f"Correlation ID {correlation_id} not registered. "
                f"Call register_correlation() first."
            )

        queue = self.correlation_queues[correlation_id]

        try:
            if timeout is not None:
                return await asyncio.wait_for(queue.get(), timeout=timeout)
            else:
                return await queue.get()
        except TimeoutError:
            return None

    async def publish(self, message: dict[str, Any]) -> None:
        """
        Put a message back into the message queue for later consumption.

        This method is async to prevent blocking when the queue is full.
        Uses a timeout to avoid indefinite waits that could cause deadlocks.

        Args:
            message: Message dictionary to queue

        Raises:
            TransportError: If pump is not running
        """
        if not self._running:
            raise TransportError(
                "Cannot publish message when message pump is not running. "
                "Call start() first to begin the receive loop."
            )

        try:
            # Use async put with timeout to prevent blocking
            # If queue is full for >1s, log and drop message to prevent deadlock
            await asyncio.wait_for(self.message_queue.put(message), timeout=1.0)
            logger.debug(
                f"Published message back to queue: correlation_id={message.get('correlation_id')}"
            )
        except TimeoutError:
            # Queue is full and not draining - drop message to prevent deadlock
            logger.error(
                f"Failed to publish message back to queue (timeout after 1s). "
                f"Queue size: {self.message_queue.qsize()}. "
                f"Dropping message with correlation_id: {message.get('correlation_id')} "
                f"to prevent deadlock."
            )

    async def send_request(
        self, request: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        """
        Send a request and wait for correlated response.

        The request must contain a correlation ID (or one will be generated).
        This method waits for a response with matching correlation ID.

        Args:
            request: Request message (should contain correlation_id field)
            timeout: Maximum time to wait for response

        Returns:
            Dict[str, Any]: Response message

        Raises:
            asyncio.TimeoutError: If no response received within timeout
            TransportError: If transport error occurs
        """
        if not self._running:
            await self.start()

        # Extract or generate correlation ID
        correlation_id = self.get_correlation_id(request)
        if not correlation_id:
            correlation_id = f"req_{id(request)}"
            request = {**request, "correlation_id": correlation_id}
            logger.debug(f"Generated correlation ID: {correlation_id}")

        # Store request signal to filter out echoes
        request_signal = request.get("signal", "")

        # Create future for response
        response_future = asyncio.Future()
        self.pending_requests[correlation_id] = response_future
        self.request_signals[correlation_id] = request_signal

        try:
            # Send request
            await self.transport.send(request)

            # Wait for response
            try:
                response = await asyncio.wait_for(response_future, timeout=timeout)
                return response
            except TimeoutError:
                logger.warning(f"Request {correlation_id} timed out after {timeout}s")
                raise
            finally:
                # Clean up pending request
                if correlation_id in self.pending_requests:
                    del self.pending_requests[correlation_id]
                if correlation_id in self.request_signals:
                    del self.request_signals[correlation_id]

        except Exception as e:
            # Clean up on error
            if correlation_id in self.pending_requests:
                del self.pending_requests[correlation_id]
            if correlation_id in self.request_signals:
                del self.request_signals[correlation_id]
            raise TransportError(f"Failed to send request: {e}") from e

    async def _receive_loop(self) -> None:
        """
        Background loop for receiving messages and matching correlations.

        This task runs continuously, receiving messages and matching them
        to pending requests based on correlation IDs.

        **IMPORTANT**: This is the ONLY code path that reads from the transport
        when the pump is running. All other methods (read_message(), receive())
        are disabled to enforce single-reader semantics and prevent concurrency
        errors from multiple coroutines calling readexactly() simultaneously.

        **TIMEOUT HANDLING**: Uses receive_timeout to prevent indefinite hangs
        from incomplete socket data. If a timeout occurs, the loop logs a warning
        and continues, allowing recovery from transient socket issues.
        """
        logger.debug("Receive loop started")

        while self._running:
            try:
                # Optional: Check connection state before attempting receive
                if (
                    hasattr(self.transport, "is_connected")
                    and not self.transport.is_connected()
                ):
                    logger.warning("Transport not connected, stopping receive loop")
                    break

                # Receive message with timeout to prevent indefinite hangs
                # If receive_timeout is None, no timeout is applied (not recommended)
                if self.receive_timeout is not None:
                    message = await asyncio.wait_for(
                        self.transport.receive(), timeout=self.receive_timeout
                    )
                else:
                    # No timeout (legacy behavior) - can hang forever
                    message = await self.transport.receive()

                # Extract correlation ID
                correlation_id = self.get_correlation_id(message)

                # Update statistics
                self._messages_received_count += 1

                # Log received message for debugging (INFO level for visibility)
                signal = message.get("signal", "unknown")

                # Route message to appropriate destination
                # Priority: 1) Correlation queues, 2) Pending requests, 3) General queue

                if correlation_id and correlation_id in self.correlation_queues:
                    # PRIORITY 1: Direct routing to correlation-specific queue (O(1))
                    # This eliminates the republish bottleneck - no filtering needed!
                    await self.correlation_queues[correlation_id].put(message)

                    # Update activity tracking and refresh expiration
                    if correlation_id in self.correlation_metadata:
                        metadata = self.correlation_metadata[correlation_id]
                        metadata["last_activity"] = time.time()
                        metadata["message_count"] += 1

                        # Refresh expiration on activity
                        self._refresh_correlation_expiration(correlation_id)

                    logger.debug(
                        f"Routed to correlation queue: {correlation_id}, signal: {signal}"
                    )

                elif correlation_id and correlation_id in self.pending_requests:
                    # PRIORITY 2: Match response to pending request/response
                    signal = message.get("signal", "")
                    request_signal = self.request_signals.get(correlation_id)

                    if signal == request_signal:
                        # This is the request echo, ignore it
                        logger.debug(
                            f"Ignoring request echo for correlation_id: {correlation_id}, signal: {signal}"
                        )
                        continue

                    # Match response to pending request
                    future = self.pending_requests[correlation_id]
                    if not future.done():
                        future.set_result(message)
                        logger.debug(
                            f"Matched response for correlation_id: {correlation_id}, signal: {signal}"
                        )

                else:
                    # PRIORITY 3: No specific handler - put in general queue
                    # Other consumers (workers, etc.) can get messages from here
                    if hasattr(self, "message_queue"):
                        await self.message_queue.put(message)
                        logger.debug(
                            f"Queued to general queue: correlation_id={correlation_id}"
                        )
                    else:
                        # No message queue - log and drop message
                        logger.debug(
                            f"Received uncorrelated message (no handler): correlation_id={correlation_id}"
                        )

            except TimeoutError:
                # Timeout waiting for message
                # Check if pump is idle (no pending work) or active (waiting for specific responses)
                self._receive_timeout_count += 1

                if self.is_idle():
                    # Pump is idle (no pending requests or correlations) - timeout is expected
                    # Log at DEBUG level to avoid noise when legitimately idle
                    logger.debug(
                        f"Receive timeout after {self.receive_timeout}s while idle "
                        f"(timeout #{self._receive_timeout_count}, messages received: {self._messages_received_count}). "
                        f"This is normal when no operations are active."
                    )
                else:
                    # Pump is active (has pending requests or correlations) - timeout is unexpected
                    # Log at WARNING level as this may indicate a problem
                    logger.warning(
                        f"Receive timeout after {self.receive_timeout}s while active "
                        f"(timeout #{self._receive_timeout_count}, messages received: {self._messages_received_count}, "
                        f"pending_requests: {len(self.pending_requests)}, correlation_queues: {len(self.correlation_queues)}). "
                        f"This may indicate incomplete data on the socket or a slow/stuck connection. "
                        f"Continuing to wait for next message..."
                    )

                # Continue loop - don't break, allow recovery
                continue

            except asyncio.CancelledError:
                logger.debug("Receive loop cancelled")
                break

            except Exception as e:
                if not self._running:
                    # Pump stopped, exit loop
                    break

                error_str = str(e).lower()
                error_type = type(e).__name__

                # Check if this is a permanent disconnection error
                is_disconnection = (
                    "connection closed" in error_str
                    or "not connected" in error_str
                    or "session not connected" in error_str
                    or "connection lost" in error_str
                    or "connection reset" in error_str
                    or "broken pipe" in error_str
                    or error_type == "IncompleteReadError"
                    or error_type == "ConnectionResetError"
                    or error_type == "BrokenPipeError"
                )

                if is_disconnection:
                    # Permanent disconnection - stop loop
                    logger.warning(
                        f"Transport connection closed permanently: {e}. "
                        f"Stopping receive loop."
                    )

                    # Mark all pending requests as failed
                    for corr_id, future in list(self.pending_requests.items()):
                        if not future.done():
                            future.set_exception(
                                TransportError(f"Connection closed: {e}")
                            )
                        # Clean up
                        del self.pending_requests[corr_id]
                        if corr_id in self.request_signals:
                            del self.request_signals[corr_id]

                    # Mark pump as not running and stop the loop
                    self._running = False
                    break

                # Check for concurrency error (temporary, should retry)
                is_concurrency = (
                    "another coroutine is already waiting" in error_str
                    or "readexactly" in error_str
                )

                if is_concurrency:
                    logger.error(
                        f"Transport concurrency error detected: {e}. "
                        "This indicates another coroutine is trying to read from the transport "
                        "while the receive loop is active. Ensure all transport reads go through "
                        "the message pump. Error details:",
                        exc_info=True,
                    )
                    # Brief backoff before retry
                    await asyncio.sleep(0.1)
                    continue

                # Other errors - log and continue with backoff
                logger.error(f"Error in receive loop: {e}", exc_info=True)
                await asyncio.sleep(0.1)

        logger.debug("Receive loop ended")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()

    def get_correlation_stats(self) -> dict[str, Any]:
        """
        Get statistics about active correlations for monitoring.

        Returns:
            Dictionary with correlation statistics including:
            - active_correlations: Number of active correlation queues
            - oldest_correlation_age: Age of oldest correlation in seconds
            - total_messages_in_queues: Total messages across all correlation queues
            - correlation_details: List of correlation metadata
        """
        if not self.correlation_metadata:
            return {
                "active_correlations": 0,
                "oldest_correlation_age": 0,
                "total_messages_in_queues": 0,
                "correlation_details": [],
            }

        now = time.time()
        oldest_age = 0
        total_messages = 0
        correlation_details = []

        for corr_id, metadata in self.correlation_metadata.items():
            age = now - metadata["created_at"]
            inactive_time = now - metadata["last_activity"]

            if age > oldest_age:
                oldest_age = age

            queue = self.correlation_queues.get(corr_id)
            queue_size = queue.qsize() if queue else 0
            total_messages += queue_size

            correlation_details.append(
                {
                    "correlation_id": corr_id,
                    "age_seconds": age,
                    "inactive_seconds": inactive_time,
                    "message_count": metadata["message_count"],
                    "queue_size": queue_size,
                }
            )

        return {
            "active_correlations": len(self.correlation_queues),
            "oldest_correlation_age": oldest_age,
            "total_messages_in_queues": total_messages,
            "correlation_details": correlation_details,
        }

    def get_statistics(self) -> dict[str, Any]:
        """
        Get message pump statistics for monitoring and debugging.

        Returns:
            Dictionary with pump statistics including:
            - messages_received: Total messages received
            - receive_timeouts: Number of receive timeouts
            - pending_requests: Number of pending requests
            - queue_size: Current message queue size
            - is_running: Whether pump is running
            - correlation_stats: Correlation-specific statistics
        """
        stats = {
            "messages_received": self._messages_received_count,
            "receive_timeouts": self._receive_timeout_count,
            "pending_requests": len(self.pending_requests),
            "queue_size": self.message_queue.qsize(),
            "is_running": self._running,
            "receive_timeout_setting": self.receive_timeout,
        }

        # Add correlation statistics
        stats["correlation_stats"] = self.get_correlation_stats()

        return stats
