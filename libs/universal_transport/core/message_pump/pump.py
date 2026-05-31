"""
Message pump implementation.

Provides concurrent I/O, correlation matching, and message queuing for transport-based communication.

Event-Driven Cleanup:
    Each correlation manages its own expiration via asyncio.Task.
    No periodic cleanup loop - expiration tasks fire after inactivity timeout.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

from ..exceptions import TransportError
from ..interfaces import Transport
from .correlation_registry import CORRELATION_TIMEOUT, CorrelationRegistry
from .interfaces import MessagePumpInterface
from .message_identification import default_get_correlation_id
from .pump_receive_loop import PumpReceiveLoop

logger = get_logger(__name__)


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
                                Defaults to looking for 'correlation_id','
                                    ''correlationId', or 'id'.
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

        # Receive task
        self.receive_task: asyncio.Task | None = None
        self._running = False

        # Correlation lifecycle now delegated
        self._correlations = CorrelationRegistry()

        # Receive loop now delegated (injected with callbacks defined below)
        self._receiver = PumpReceiveLoop(
            self.transport,
            self.get_correlation_id,
            self.receive_timeout,
            self.message_queue,
            self._correlations,
            self.pending_requests,
            self.request_signals,
            self._is_running,
            self._is_idle,
            self._mark_stopped,
        )

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
        return len(self.pending_requests) == 0 and self._correlations.active_count == 0

    def _is_running(self) -> bool:
        """Callback injected into PumpReceiveLoop."""
        return self._running

    def _is_idle(self) -> bool:
        """Callback injected into PumpReceiveLoop (delegates for consistency)."""
        return self.is_idle()

    def _mark_stopped(self) -> None:
        """Callback injected into PumpReceiveLoop on permanent disconnect."""
        self._running = False

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

        # Correlation state (tasks + queues + metadata) cleaned via registry
        self._correlations.clear()

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
                "Use send_request() for request/response patterns, or stop() the pump"
                "first."
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
                "Use send_request() for request/response patterns, or stop() the pump"
                "first."
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

        Delegates to CorrelationRegistry which owns the per-correlation queues,
        metadata and expiration tasks.
        """
        self._correlations.register(correlation_id, timeout)

    def unregister_correlation(self, correlation_id: str) -> None:
        """
        Unregister a correlation ID and clean up its queue.

        Delegates to CorrelationRegistry.
        """
        self._correlations.unregister(correlation_id)

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
                "Cannot call get_message_for_correlation() when message pump is not"
                "running."
                "Call start() first to begin the receive loop."
            )

        if not self._correlations.is_registered(correlation_id):
            raise TransportError(
                f"Correlation ID {correlation_id} not registered. "
                f"Call register_correlation() first."
            )

        return await self._correlations.get_message(correlation_id, timeout)

    def get_correlation_stats(self) -> dict[str, Any]:
        """Return correlation statistics (delegated to registry)."""
        return self._correlations.get_stats()

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
        """Façade delegate to the extracted PumpReceiveLoop.run()."""
        await self._receiver.run()

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()

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
            "messages_received": self._receiver.messages_received_count,
            "receive_timeouts": self._receiver.receive_timeout_count,
            "pending_requests": len(self.pending_requests),
            "queue_size": self.message_queue.qsize(),
            "is_running": self._running,
            "receive_timeout_setting": self.receive_timeout,
        }

        # Add correlation statistics
        stats["correlation_stats"] = self._correlations.get_stats()

        return stats
