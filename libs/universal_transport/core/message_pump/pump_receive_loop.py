"""
Pump receive loop for the universal transport message pump.

Encapsulates the exclusive transport reader that runs while the MessagePump
is active. The loop is the single reader from the underlying transport
(enforced by disabling read_message/receive on the façade while running).

Responsibilities:
- Receive with optional timeout (prevents indefinite hangs on partial frames)
- Route by priority: 1) correlation-specific queues (via CorrelationRegistry),
  2) pending request/response futures, 3) general message_queue for workers
- Classify errors into permanent disconnection vs transient concurrency vs other
- Update receive statistics (messages, timeouts)
- Cooperate with pump via injected callbacks for running/idle state and shutdown

This separation keeps the complex receive/error logic out of the public façade
and out of the correlation registry.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

from ..exceptions import TransportError
from ..interfaces import Transport
from .correlation_registry import CorrelationRegistry

logger = get_logger(__name__)


class PumpReceiveLoop:
    """
    Background receive loop extracted from the original MessagePump._receive_loop.

    The loop owns the two counters (messages_received_count, receive_timeout_count)
    that appear in get_statistics. All other state (queues, pending futures,
    correlation registry) is injected so the loop has no knowledge of MessagePump.
    """

    def __init__(
        self,
        transport: Transport,
        get_correlation_id: Callable[[dict[str, Any]], str | None],
        receive_timeout: float | None,
        message_queue: asyncio.Queue,
        correlation_registry: CorrelationRegistry,
        pending_requests: dict[str, asyncio.Future],
        request_signals: dict[str, str],
        is_running: Callable[[], bool],
        is_idle: Callable[[], bool],
        mark_stopped: Callable[[], None],
    ) -> None:
        self.transport = transport
        self.get_correlation_id = get_correlation_id
        self.receive_timeout = receive_timeout
        self.message_queue = message_queue
        self.correlation_registry = correlation_registry
        self.pending_requests = pending_requests
        self.request_signals = request_signals
        self._is_running = is_running
        self._is_idle = is_idle
        self._mark_stopped = mark_stopped

        self._messages_received_count = 0
        self._receive_timeout_count = 0

    @property
    def messages_received_count(self) -> int:
        """Total messages successfully received from the transport."""
        return self._messages_received_count

    @property
    def receive_timeout_count(self) -> int:
        """Number of receive timeouts observed while the loop was active."""
        return self._receive_timeout_count

    def _is_disconnection_error(self, exc: Exception) -> bool:
        """Return True for errors that indicate a permanent transport close."""
        error_str = str(exc).lower()
        error_type = type(exc).__name__
        return (
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

    def _is_concurrency_error(self, exc: Exception) -> bool:
        """Return True for transient 'another coroutine is already reading' errors."""
        error_str = str(exc).lower()
        return (
            "another coroutine is already waiting" in error_str
            or "readexactly" in error_str
        )

    async def run(self) -> None:
        """
        The exclusive receive loop body (formerly MessagePump._receive_loop).

        Runs until mark_stopped() is called or the pump transitions to not-running.
        On permanent disconnection, fails all pending request futures and stops.
        Timeouts are logged at debug (idle) or warning (active) but never stop the loop.
        """
        logger.debug("Receive loop started")

        while self._is_running():
            try:
                # Optional: Check connection state before attempting receive
                if (
                    hasattr(self.transport, "is_connected")
                    and not self.transport.is_connected()
                ):
                    logger.warning("Transport not connected, stopping receive loop")
                    break

                # Receive message with timeout to prevent indefinite hangs
                if self.receive_timeout is not None:
                    message = await asyncio.wait_for(
                        self.transport.receive(), timeout=self.receive_timeout
                    )
                else:
                    message = await self.transport.receive()

                correlation_id = self.get_correlation_id(message)
                self._messages_received_count += 1

                signal = message.get("signal", "unknown")

                # Priority: 1) corr queues, 2) pending requests, 3) general queue
                if correlation_id and self.correlation_registry.is_registered(
                    correlation_id
                ):
                    # PRIORITY 1: Direct O(1) routing via registry
                    await self.correlation_registry.route_message(
                        correlation_id, message
                    )
                    logger.debug(
                        f"Routed to correlation queue: {correlation_id}, "
                        f"signal: {signal}"
                    )

                elif correlation_id and correlation_id in self.pending_requests:
                    # PRIORITY 2: Match response to pending request/response
                    signal = message.get("signal", "")
                    request_signal = self.request_signals.get(correlation_id)

                    if signal == request_signal:
                        logger.debug(
                            f"Ignoring request echo for correlation_id: {correlation_id}, "  # noqa: E501
                            f"signal: {signal}"
                        )
                        continue

                    future = self.pending_requests[correlation_id]
                    if not future.done():
                        future.set_result(message)
                        logger.debug(
                            f"Matched response for correlation_id: {correlation_id}, "
                            f"signal: {signal}"
                        )

                else:
                    # PRIORITY 3: General queue for workers / uncorrelated messages
                    await self.message_queue.put(message)
                    logger.debug(
                        f"Queued to general queue: correlation_id={correlation_id}"
                    )

            except TimeoutError:
                self._receive_timeout_count += 1

                if self._is_idle():
                    logger.debug(
                        f"Receive timeout after {self.receive_timeout}s while idle "
                        f"(timeout #{self._receive_timeout_count}, "
                        f"messages: {self._messages_received_count}). "
                        f"This is normal when no operations are active."
                    )
                else:
                    logger.warning(
                        f"Receive timeout after {self.receive_timeout}s while active "
                        f"(timeout #{self._receive_timeout_count}, messages received: {self._messages_received_count}, "
                        f"pending_requests: {len(self.pending_requests)}, correlation_queues: {self.correlation_registry.active_count}). "  # noqa: E501
                        f"This may indicate incomplete data on the socket or a slow/stuck connection. "
                        f"Continuing to wait for next message..."  # noqa: E501
                    )
                continue

            except asyncio.CancelledError:
                logger.debug("Receive loop cancelled")
                break

            except Exception as e:
                if not self._is_running():
                    break

                if self._is_disconnection_error(e):
                    logger.warning(
                        f"Transport connection closed permanently: {e}. "
                        f"Stopping receive loop."
                    )

                    # Fail all pending requests
                    for corr_id, future in list(self.pending_requests.items()):
                        if not future.done():
                            future.set_exception(
                                TransportError(f"Connection closed: {e}")
                            )
                        del self.pending_requests[corr_id]
                        if corr_id in self.request_signals:
                            del self.request_signals[corr_id]

                    self._mark_stopped()
                    break

                if self._is_concurrency_error(e):
                    logger.error(
                        f"Transport concurrency error detected: {e}. "
                        "This indicates another coroutine is trying to read from the"
                        "transport"
                        "while the receive loop is active. Ensure all transport reads"
                        "go through"
                        "the message pump. Error details:",  # noqa: E501
                        exc_info=True,
                    )
                    await asyncio.sleep(0.1)
                    continue

                logger.error(f"Error in receive loop: {e}", exc_info=True)
                await asyncio.sleep(0.1)

        logger.debug("Receive loop ended")
