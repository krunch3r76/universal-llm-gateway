"""WebSocket connection lifecycle management.

Handles connect, disconnect, reconnect, and message receive loop.

Invariant: ∀ disconnect, ∃! reconnect_task (no duplicate reconnect loops)
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

import websockets
from universal_logging import get_logger
from websockets.client import WebSocketClientProtocol

from ..messages import MessageType

logger = get_logger(__name__)


class ConnectionState(str, Enum):
    """WebSocket connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class ConnectionManager:
    """
    Manages WebSocket connection lifecycle.

    Responsibilities:
    - Connect/disconnect/reconnect logic
    - Message receive loop + JSON parsing
    - Sparse failure logging

    Event-driven: emits connection events via callbacks.
    """

    def __init__(
        self,
        ws_url: str,
        gateway_name: str,
        reconnect_interval: float = 5.0,
        max_reconnect_attempts: int = 0,  # 0 = infinite
        connect_timeout: float = 10.0,
        ping_interval: float = 20.0,  # WebSocket ping every 20s
        ping_timeout: float = 30.0,  # Consider dead if no pong in 30s
        socket_path: str | None = None,  # NEW: Unix socket path
    ) -> None:
        self._ws_url = ws_url
        self._gateway_name = gateway_name
        self._reconnect_interval = reconnect_interval
        self._max_reconnect_attempts = max_reconnect_attempts
        self._connect_timeout = connect_timeout
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._socket_path = socket_path  # NEW: Store socket path

        # Diagnostic logging
        logger.info(
            f"ConnectionManager.__init__(): gateway_name={gateway_name}, "
            f"socket_path={socket_path}, ws_url={ws_url}"
        )

        # Connection state
        self._ws: WebSocketClientProtocol | None = None
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_task: asyncio.Task | None = None
        self._message_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        self._ready = asyncio.Event()

        # Failure tracking for sparse logging
        self._failure_count = 0
        self._message_loop_error_count = 0  # Track message loop errors separately

        # Telemetry: reconnect metrics
        self._reconnect_started_at: float | None = None
        self._total_reconnect_attempts = 0
        self._last_message_time: float | None = None

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def ws(self) -> WebSocketClientProtocol | None:
        """Current WebSocket connection (None if disconnected)."""
        return self._ws

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """True if WebSocket is connected and INIT received."""
        return self._state == ConnectionState.CONNECTED and self._ready.is_set()

    @property
    def reconnect_metrics(self) -> dict[str, Any]:
        """Telemetry: reconnect attempt metrics for monitoring."""
        return {
            "total_attempts": self._total_reconnect_attempts,
            "failure_count": self._failure_count,
            "is_reconnecting": self._reconnect_started_at is not None,
            "reconnect_duration_s": (
                time.monotonic() - self._reconnect_started_at
                if self._reconnect_started_at
                else None
            ),
        }

    @property
    def seconds_since_last_message(self) -> float | None:
        """Seconds since last message received (None if never received)."""
        if self._last_message_time is None:
            return None
        return time.monotonic() - self._last_message_time

    @property
    def uses_unix_socket(self) -> bool:
        """True if using Unix socket transport."""
        return self._socket_path is not None

    # =========================================================================
    # Connection Lifecycle
    # =========================================================================

    async def connect(
        self,
        on_init: Callable[[dict[str, Any]], None],
        on_connected: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        """
        Connect to Gateway WebSocket.

        Args:
            on_init: Callback to process INIT message data
            on_connected: Optional callback when connection established

        Returns:
            True if connected and INIT received, False otherwise.
        """
        if self._state == ConnectionState.CONNECTED:
            logger.debug(f"{self._gateway_name}: Already connected, returning True")
            return True

        self._state = ConnectionState.CONNECTING
        self._shutdown_event.clear()

        # CRITICAL: Close any existing WebSocket to prevent CLOSE-WAIT accumulation
        # This handles edge cases where _message_loop didn't clean up properly
        if self._ws:
            logger.debug(
                f"{self._gateway_name}: Closing stale WebSocket before reconnect"
            )
            try:
                await self._ws.close()
            except Exception:
                pass  # Best-effort close
            self._ws = None

        # Reset last message timestamp on new connection
        self._last_message_time = None

        # Diagnostic logging
        logger.info(
            f"ConnectionManager.connect(): gateway_name={self._gateway_name}, "
            f"socket_path={self._socket_path}, ws_url={self._ws_url}"
        )

        # CRITICAL: Fail fast if socket_path was expected but is None
        # Check if ws_url looks like it expects Unix socket (no port in URL)
        if self._socket_path is None and ":999" not in self._ws_url:
            # URL has no port but socket_path is None - this is a configuration error
            logger.error(
                f"CRITICAL: socket_path is None but ws_url={self._ws_url} suggests Unix socket config. "
                f"This would cause TCP fallback. Failing fast to prevent silent misconfiguration."
            )
            raise ValueError(
                f"Configuration error: socket_path is None but gateway appears to be configured "
                f"for Unix socket (ws_url={self._ws_url}). Cannot fall back to TCP."
            )

        try:
            # Connect using appropriate transport
            if self._socket_path:
                # Unix socket transport
                from urllib.parse import urlparse

                parsed = urlparse(self._ws_url)
                uri = f"ws://localhost{parsed.path}"

                logger.debug(
                    f"{self._gateway_name}: Connecting via Unix socket: "
                    f"{self._socket_path} (uri={uri})"
                )

                self._ws = await asyncio.wait_for(
                    websockets.unix_connect(
                        path=self._socket_path,
                        uri=uri,
                        ping_interval=self._ping_interval,
                        ping_timeout=self._ping_timeout,
                        close_timeout=5.0,
                    ),
                    timeout=self._connect_timeout,
                )
            else:
                # TCP transport (legacy - only for non-Unix socket configs)
                logger.warning(
                    f"Using TCP transport for {self._gateway_name}: {self._ws_url}. "
                    f"If Unix socket was intended, check socket_path configuration."
                )
                self._ws = await asyncio.wait_for(
                    websockets.connect(
                        self._ws_url,
                        ping_interval=self._ping_interval,  # Enable ping
                        ping_timeout=self._ping_timeout,  # Timeout waiting for pong
                        close_timeout=5.0,
                    ),
                    timeout=self._connect_timeout,
                )

            # Wait for INIT message
            raw_init = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._connect_timeout,
            )

            init_msg = json.loads(raw_init)
            if init_msg.get("type") != MessageType.INIT.value:
                logger.error(f"Expected INIT message, got: {init_msg.get('type')}")
                await self._ws.close()
                self._state = ConnectionState.DISCONNECTED
                return False

            # Process INIT message via callback
            on_init(init_msg["data"])

            self._state = ConnectionState.CONNECTED
            self._ready.set()
            self._failure_count = 0  # Reset on success

            transport_info = (
                f"Unix socket: {self._socket_path}"
                if self._socket_path
                else f"TCP: {self._ws_url}"
            )
            logger.info(
                f"✅ Connected to Gateway '{self._gateway_name}' ({transport_info})"
            )

            # Notify callback and WAIT for it to complete
            # CRITICAL: Must await to ensure message loop is started before returning
            if on_connected:
                try:
                    logger.debug(
                        f"{self._gateway_name}: Calling on_connected callback and waiting for completion..."
                    )
                    await on_connected()
                    logger.debug(
                        f"{self._gateway_name}: on_connected callback completed"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to notify on_connected callback: {e}", exc_info=True
                    )
                    # Continue anyway - connection is established

            logger.debug(f"{self._gateway_name}: Connection successful, returning True")
            return True

        except (TimeoutError, Exception) as e:
            # Connection failed - track for sparse logging
            self._failure_count += 1
            self._state = ConnectionState.DISCONNECTED

            # Log first failure, then every 60 attempts (~5 min at 5s interval)
            if self._failure_count == 1:
                transport_info = (
                    f"socket={self._socket_path}"
                    if self._socket_path
                    else f"url={self._ws_url}"
                )
                logger.warning(
                    f"⚠️ {self._gateway_name} unreachable ({transport_info}), "
                    f"retrying in background... (error: {type(e).__name__})"
                )
            elif self._failure_count % 60 == 0:
                logger.warning(
                    f"⚠️ {self._gateway_name} still unreachable after "
                    f"{self._failure_count} attempts"
                )

            return False

    async def disconnect(self) -> None:
        """Disconnect from Gateway WebSocket."""
        self._shutdown_event.set()

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self._message_task:
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
            self._message_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._state = ConnectionState.DISCONNECTED
        self._ready.clear()

        logger.info(f"Disconnected from Gateway '{self._gateway_name}'")

    async def wait_ready(self, timeout: float | None = None) -> bool:
        """
        Wait for connection to be ready (INIT received).

        Args:
            timeout: Maximum seconds to wait (None = wait forever)

        Returns:
            True if ready, False if timeout
        """
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    # =========================================================================
    # Message Loop
    # =========================================================================

    def start_message_loop(
        self,
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        on_disconnected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """
        Start message receive loop.

        Args:
            on_message: Callback for each received message
            on_disconnected: Optional callback when connection is lost

        Non-blocking: Schedules background task.

        Connection health is managed by websockets library's
        ping_interval/ping_timeout. When pings fail, the connection will
        raise ConnectionClosed, triggering reconnection.
        """
        logger.info(
            f"🔍 {self._gateway_name}: start_message_loop() called (ws={self._ws}, state={self._state})"
        )

        # Cancel existing message loop if running
        if self._message_task and not self._message_task.done():
            logger.warning(
                f"🔍 {self._gateway_name}: Cancelling existing message loop before starting new one"
            )
            self._message_task.cancel()

        try:
            logger.info(f"🔍 {self._gateway_name}: Creating message loop task...")
            self._message_task = asyncio.create_task(
                self._message_loop(on_message, on_disconnected)
            )
            logger.info(
                f"🔍 {self._gateway_name}: Message loop task created successfully: {self._message_task}"
            )
        except Exception as e:
            logger.error(f"🔍 Failed to start message loop: {e}", exc_info=True)

    async def _message_loop(
        self,
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        on_disconnected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """
        Handle incoming WebSocket messages.

        Connection health monitoring is handled by websockets library:
        - Sends WebSocket PING frames every ping_interval seconds
        - If no PONG received within ping_timeout, raises ConnectionClosed
        - This is TCP-level, event-driven detection (no polling)

        Close frame handling:
        - When Gateway sends CLOSE, we immediately respond with CLOSE (proper handshake)
        - When we detect an error, we immediately send CLOSE
        - Prevents CLOSE-WAIT accumulation from delayed close handshakes

        Args:
            on_message: Callback for each received message
            on_disconnected: Optional callback when connection is lost
        """
        ws_before = self._ws
        logger.info(
            f"🔍 {self._gateway_name}: ===== MESSAGE LOOP STARTING ===== (ws={ws_before}, state={self._state})"
        )

        # Check if we're already shutting down
        if self._shutdown_event.is_set():
            logger.error(
                f"🔍 {self._gateway_name}: Message loop started but shutdown already requested!"
            )
            return

        try:
            ws_after = self._ws
            if ws_after is None:
                logger.error(
                    f"🔍 {self._gateway_name}: Message loop started but websocket is None! (was {ws_before}, now {ws_after})"
                )
                return
            if ws_before != ws_after:
                logger.error(
                    f"🔍 {self._gateway_name}: Websocket changed between log and check! {ws_before} -> {ws_after}"
                )

            # Check websocket state
            logger.info(
                f"🔍 {self._gateway_name}: WebSocket state before loop: close_code={self._ws.close_code}, open={self._ws.close_code is None}"
            )
            logger.info(
                f"🔍 {self._gateway_name}: Entering message receive loop now..."
            )

            message_count = 0
            logger.info(f"🔍 {self._gateway_name}: About to start 'async for' loop")

            try:
                async for raw_message in self._ws:
                    logger.info(
                        f"🔍 {self._gateway_name}: INSIDE async for loop - iteration {message_count + 1}"
                    )
                    message_count += 1
                    logger.debug(
                        f"{self._gateway_name}: Received message #{message_count}"
                    )
                    self._last_message_time = time.monotonic()  # Track last message
                    if self._shutdown_event.is_set():
                        break

                    try:
                        message = json.loads(raw_message)
                        # DEBUG: Log all messages for diagnosis
                        msg_type = message.get("type", "unknown")
                        if msg_type == MessageType.MODEL_LOADED.value:
                            logger.info(
                                f"🔍 DEBUG: Received MODEL_LOADED message: {message}"
                            )
                        await on_message(message)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON from Gateway: {e}")
                    except Exception as e:
                        # Sparse logging for message handling errors
                        self._message_loop_error_count += 1
                        if self._message_loop_error_count == 1:
                            logger.error(
                                f"Message handling error (will log every 10th): {e}",
                                exc_info=True,
                            )
                        elif self._message_loop_error_count % 10 == 0:
                            logger.error(
                                f"Message handling error "
                                f"({self._message_loop_error_count} total): {e}",
                                exc_info=True,
                            )

                logger.info(
                    f"🔍 {self._gateway_name}: async for loop completed normally after {message_count} messages"
                )

            except StopAsyncIteration:
                logger.warning(
                    f"🔍 {self._gateway_name}: StopAsyncIteration raised in async for loop!"
                )
                raise
            except asyncio.CancelledError:
                logger.warning(
                    f"🔍 {self._gateway_name}: async for loop was cancelled!"
                )
                raise
            except Exception as e:
                logger.error(
                    f"🔍 {self._gateway_name}: Exception in async for loop: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                raise

        except websockets.ConnectionClosed as e:
            # Gateway sent CLOSE frame - immediately respond with our CLOSE
            # This completes the WebSocket close handshake properly
            logger.warning(
                f"{self._gateway_name} ConnectionClosed exception: "
                f"code={e.code}, reason={e.reason or 'none'}, rcvd={e.rcvd}, sent={e.sent}"
            )
            if self._ws and not self._shutdown_event.is_set():
                try:
                    await self._ws.close(code=e.code or 1000, reason=e.reason)
                except Exception:
                    pass  # Best-effort - socket may already be closed
                logger.info(
                    f"{self._gateway_name} connection closed: "
                    f"code={e.code}, reason={e.reason or 'none'}"
                )

        except Exception as e:
            # Unexpected error - immediately send CLOSE frame
            logger.error(
                f"{self._gateway_name} Unexpected exception in message loop: {e}",
                exc_info=True,
            )
            if self._ws and not self._shutdown_event.is_set():
                try:
                    await self._ws.close(code=1011, reason="Internal error")
                except Exception:
                    pass  # Best-effort
                logger.error(
                    f"{self._gateway_name} WebSocket error: {e}", exc_info=True
                )

        finally:
            logger.info(
                f"🔍 {self._gateway_name}: ===== MESSAGE LOOP FINALLY BLOCK ===== (shutdown={self._shutdown_event.is_set()}, ws={self._ws}, close_code={self._ws.close_code if self._ws else 'N/A'})"
            )
            # Defensive: close if still open (e.g., shutdown or missed exception path)
            if self._ws:
                try:
                    # Only close if not already closed
                    if self._ws.close_code is None:
                        logger.info(
                            f"🔍 {self._gateway_name}: Closing websocket in finally block (was open)"
                        )
                        await self._ws.close()
                    else:
                        logger.info(
                            f"🔍 {self._gateway_name}: WebSocket already closed in finally block (code={self._ws.close_code})"
                        )
                except Exception as e:
                    logger.warning(
                        f"🔍 {self._gateway_name}: Exception closing websocket: {e}"
                    )
                    pass  # Best-effort
                # Don't set self._ws = None here; let connect() handle it

            if not self._shutdown_event.is_set():
                # Unexpected disconnect - trigger reconnection
                logger.warning(
                    f"🔍 {self._gateway_name}: Unexpected disconnect detected in finally block - will trigger reconnection"
                )
                self._state = ConnectionState.DISCONNECTED
                self._ready.clear()

                # Notify callback (fire-and-forget)
                if on_disconnected:
                    logger.info(
                        f"🔍 {self._gateway_name}: Calling on_disconnected callback"
                    )
                    asyncio.create_task(on_disconnected())
                else:
                    logger.warning(
                        f"🔍 {self._gateway_name}: No on_disconnected callback to call!"
                    )

    # =========================================================================
    # Reconnection
    # =========================================================================

    def start_reconnect_loop(
        self,
        on_init: Callable[[dict[str, Any]], None],
        on_connected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """
        Start reconnection loop (non-blocking).

        Args:
            on_init: Callback to process INIT message data
            on_connected: Optional callback when connection re-established
        """
        if not self._shutdown_event.is_set() and not self._reconnect_task:
            self._reconnect_task = asyncio.create_task(
                self._reconnect_loop(on_init, on_connected)
            )

    async def _reconnect_loop(
        self,
        on_init: Callable[[dict[str, Any]], None],
        on_connected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """
        Attempt to reconnect to Gateway in background.

        Invariant: ∀ disconnect, ∃! reconnect_task (enforced by start_reconnect_loop)

        Args:
            on_init: Callback to process INIT message data
            on_connected: Optional callback when connection re-established
        """
        attempt = 0
        self._reconnect_started_at = time.monotonic()
        try:
            while not self._shutdown_event.is_set():
                attempt += 1
                self._total_reconnect_attempts += 1
                self._state = ConnectionState.RECONNECTING

                if (
                    self._max_reconnect_attempts > 0
                    and attempt > self._max_reconnect_attempts
                ):
                    logger.warning(
                        f"⛔ {self._gateway_name}: max reconnect attempts "
                        f"({self._max_reconnect_attempts}) exceeded"
                    )
                    self._state = ConnectionState.DISCONNECTED
                    return

                if await self.connect(on_init, on_connected):
                    # Log reconnect success with duration
                    duration_s = time.monotonic() - self._reconnect_started_at
                    logger.info(
                        f"✅ {self._gateway_name}: reconnected after "
                        f"{attempt} attempt(s) in {duration_s:.1f}s"
                    )
                    self._reconnect_started_at = None
                    # Clear reconnect task to allow future reconnect loops
                    self._reconnect_task = None
                    return

                # Wait before next attempt
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self._reconnect_interval,
                    )
                    # Shutdown requested during wait
                    return
                except TimeoutError:
                    # Continue reconnecting
                    pass
        finally:
            # Important: the reconnect task must be restartable across multiple
            # disconnects. A completed task object is still truthy, so leaving it
            # set would block future reconnect loops.
            if self._reconnect_task is asyncio.current_task():
                self._reconnect_task = None
            self._reconnect_started_at = None
