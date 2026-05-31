"""
Network receiver for universal_stargate monitoring data.

Supports Unix socket transport via universal_transport.AsyncMonitoringClient for reliable event reception.
"""

import asyncio
import json
import queue
import socket
import threading
import time
from collections.abc import Callable

from universal_logging import get_logger

logger = get_logger(__name__)

# Import async universal_transport classes
try:
    from universal_transport.specialized import AsyncMonitoringClient

    UNIVERSAL_TRANSPORT_AVAILABLE = True
    logger.info("✅ Async universal_transport client imported successfully")
except ImportError as e:
    logger.warning(f"universal_transport async not available: {e}")
    UNIVERSAL_TRANSPORT_AVAILABLE = False
    AsyncMonitoringClient = None


class NetworkReceiver:
    """
    Network receiver for Unix socket transport.

    Connects to proxy monitoring events via universal_transport.AsyncMonitoringClient.
    """

    def __init__(
        self,
        callback: Callable | None = None,
        root_window=None,
        config: dict | None = None,
    ):
        """
        Initialize network receiver.

        Args:
            callback: Callback function to handle received data
            root_window: Tkinter root window for thread-safe GUI updates
            config: Transport configuration dict
        """
        self.callback = callback
        self.root_window = root_window
        self.config = config or {}

        # Transport settings
        self.transport_type = self.config.get("transport", "unix")
        self.unix_socket_path = self.config.get(
            "unix_socket_path", "/tmp/stargate_events.sock"
        )
        self.host = self.config.get("host", "localhost")
        self.port = self.config.get("port", 9997)

        # Transport components
        self.monitoring_client = None
        self.sock = None  # Legacy socket fallback
        self.running = False
        self.thread = None
        self.processing_queue = queue.Queue()
        self.processing_thread = None

        # Use universal_transport by default (now that fixes are implemented)
        self.use_universal_transport = (
            UNIVERSAL_TRANSPORT_AVAILABLE
            and self.config.get("use_universal_transport", True)
        )
        logger.info(
            f"NetworkReceiver config: use_universal_transport={self.use_universal_transport}, transport_type={self.transport_type}"
        )

    def start(self):
        """Start network listener based on configured transport"""
        # Set running flag first
        self.running = True

        try:
            # Use universal_transport AsyncMonitoringClient
            if self.use_universal_transport and self.transport_type in ("unix", "tcp"):
                self._start_monitoring_client()
            # Fallback: Use legacy Unix stream socket
            elif self.transport_type == "unix":
                self._start_unix_stream()
            else:
                raise ValueError(
                    f"Unsupported transport type: {self.transport_type}. Use 'unix' or 'tcp'."
                )

        except Exception as e:
            logger.error(f"Failed to start network receiver: {e}")
            self.running = False  # Reset running flag on failure
            # No fallback - fail fast with explicit error
            raise ConnectionError(
                f"Failed to establish {self.transport_type} transport connection. No fallback allowed."
            ) from e

    def _start_monitoring_client(self):
        """Start async universal_transport AsyncMonitoringClient"""
        if not UNIVERSAL_TRANSPORT_AVAILABLE:
            raise RuntimeError("universal_transport async client not available")

        # Start async reception in a separate thread (socket waiting happens in thread)
        threading.Thread(
            target=self._run_async_client_thread,
            daemon=True,
            name="AsyncMonitoringClient",
        ).start()

        logger.info("✅ AsyncMonitoringClient thread started")

        # Monitor thread will handle reconnection logic

    async def _run_async_client(self):
        """Run the async client in an asyncio event loop"""
        logger.info("Starting async monitoring client")

        # Define max message size (100MB as in examples)
        max_message_size = 100 * 1024 * 1024

        retry_count = 0
        max_retries = 10
        backoff_time = 1.0

        while self.running and retry_count < max_retries:
            try:
                # Create and use AsyncMonitoringClient with context manager
                if self.transport_type == "unix":
                    client_ctx = AsyncMonitoringClient(
                        unix_socket=self.unix_socket_path,
                        max_message_size=max_message_size,
                    )
                    conn_info = f"unix socket: {self.unix_socket_path}"
                else:  # tcp
                    client_ctx = AsyncMonitoringClient(
                        host=self.host,
                        port=self.port,
                        max_message_size=max_message_size,
                    )
                    conn_info = f"tcp: {self.host}:{self.port}"

                async with client_ctx as client:
                    logger.info(f"✅ Connected to async monitoring {conn_info}")

                    # Reset retry count on successful connection
                    retry_count = 0
                    backoff_time = 1.0

                    # Process events while running
                    while self.running:
                        try:
                            # Receive event with 2 second timeout
                            event = await client.receive_event(timeout=2.0)

                            if event:
                                # Process event through handler
                                self._handle_monitoring_event(event)
                        except TimeoutError:
                            # Timeout is normal, just continue
                            continue
                        except Exception as e:
                            logger.error(f"Error receiving event: {e}", exc_info=True)
                            # Break inner loop to reconnect
                            break

            except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
                # Socket doesn't exist yet or connection refused, wait and retry
                retry_count += 1
                if retry_count <= 3:
                    logger.info(
                        f"Socket not available (attempt {retry_count}/{max_retries}): {e}"
                    )
                else:
                    logger.debug(
                        f"Socket not available (attempt {retry_count}/{max_retries}): {e}"
                    )
                await asyncio.sleep(backoff_time)
                backoff_time = min(30.0, backoff_time * 1.5)  # Exponential backoff

            except Exception as e:
                # Other connection errors
                retry_count += 1
                logger.error(
                    f"Connection error (attempt {retry_count}/{max_retries}): {e}",
                    exc_info=True,
                )
                await asyncio.sleep(backoff_time)
                backoff_time = min(30.0, backoff_time * 1.5)  # Exponential backoff

        if retry_count >= max_retries:
            logger.error(
                f"Failed to connect after {max_retries} attempts - will continue retrying in background"
            )
            # Continue retrying indefinitely rather than giving up
            while self.running:
                try:
                    await asyncio.sleep(5.0)
                    if self.transport_type == "unix":
                        logger.debug(
                            f"Retrying connection to {self.unix_socket_path}..."
                        )
                        client_ctx = AsyncMonitoringClient(
                            unix_socket=self.unix_socket_path,
                            max_message_size=max_message_size,
                        )
                    else:  # tcp
                        logger.debug(
                            f"Retrying connection to {self.host}:{self.port}..."
                        )
                        client_ctx = AsyncMonitoringClient(
                            host=self.host,
                            port=self.port,
                            max_message_size=max_message_size,
                        )

                    # Retry connection
                    async with client_ctx as client:
                        logger.info(
                            "✅ Reconnected to async monitoring server after failure"
                        )
                        retry_count = 0
                        while self.running:
                            try:
                                event = await client.receive_event(timeout=2.0)
                                if event:
                                    self._handle_monitoring_event(event)
                            except TimeoutError:
                                continue
                            except Exception as e:
                                logger.error(
                                    f"Error receiving event: {e}", exc_info=True
                                )
                                break
                except Exception as e:
                    logger.debug(f"Connection retry failed: {e}")
                    await asyncio.sleep(5.0)

    def _run_async_client_thread(self):
        """Run the async client in a separate thread"""
        try:
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Run the async client - it will handle connection retries internally
            loop.run_until_complete(self._run_async_client())
        except Exception as e:
            logger.error(f"Async client thread error: {e}")
            import traceback

            logger.error(traceback.format_exc())
        finally:
            logger.info("Async client thread exiting")

    def _handle_monitoring_event(self, event):
        """
        Event handler for AsyncMonitoringClient

        Args:
            event: MonitoringEvent object from AsyncMonitoringClient
        """
        try:
            logger.info(
                "✅ EVENT HANDLER: Received MonitoringEvent from AsyncMonitoringClient"
            )
            logger.info(f"   Type: {type(event)}")
            logger.info(f"   Has model_dump: {hasattr(event, 'model_dump')}")
            logger.info(f"   Has 'type' attr: {hasattr(event, 'type')}")

            if hasattr(event, "type"):
                logger.info(f"   Event type value: {event.type}")

            # Log available attributes
            logger.debug(
                f"   Available attrs: {[a for a in dir(event) if not a.startswith('_')]}"
            )

            # Extract event data
            # AsyncMonitoringClient provides MonitoringEvent with these fields:
            # - type: str
            # - data: Dict[str, Any]
            # - app_name: str
            # - timestamp: float
            event_data = {
                "type": getattr(event, "type", "unknown"),
                "data": getattr(event, "data", {}),
                "app_name": getattr(event, "app_name", "unknown"),
                "timestamp": getattr(event, "timestamp", time.time()),
            }

            # For compatibility, if event.data contains
            # original_request/modified_request, merge them up
            if isinstance(event_data["data"], dict):
                # Process all fields from data
                for key, value in event_data["data"].items():
                    # Only copy fields not already in the event_data
                    if key not in ("type", "app_name", "timestamp"):
                        event_data[key] = value

            logger.info(f"   PROCESSING: Event with {len(event_data)} fields")

            # Schedule callback on main thread
            logger.info("   Scheduling callback on main thread...")
            if self.root_window and hasattr(self.root_window, "after"):
                self.root_window.after(
                    0, lambda event=event_data: self._callback_wrapper(event)
                )
            else:
                logger.warning("   No root window, calling callback directly")
                self._callback_wrapper(event_data)

        except Exception as e:
            logger.error(
                f"❌ EVENT HANDLER: Error processing event: {e}", exc_info=True
            )

    def _callback_wrapper(self, event_data):
        """Wrapper to add logging around callback"""
        try:
            event_type = (
                event_data.get("type", "unknown")
                if isinstance(event_data, dict)
                else getattr(event_data, "type", "unknown")
            )
            logger.info(
                f"🎯 CALLBACK: About to call callback with event type: {event_type}"
            )

            # Log what we're passing to the controller
            if isinstance(event_data, dict):
                has_original = "original_request" in event_data
                has_modified = "modified_request" in event_data
                logger.info(f"🎯 CALLBACK: Passing dict with {len(event_data)} fields")
                logger.info(
                    f"🎯 CALLBACK: Has original_request: {has_original}, Has modified_request: {has_modified}"
                )

            self.callback(event_data)
            logger.info("🎯 CALLBACK: Successfully called callback")
        except Exception as e:
            logger.error(f"❌ CALLBACK: Exception in callback: {e}", exc_info=True)

    def _monitoring_client_loop(self):
        """Event receiving loop for MonitoringClient"""
        event_count = 0

        while self.running:
            try:
                # Receive event with timeout
                monitoring_event = self.monitoring_client.receive_event(timeout=1.0)

                if monitoring_event:
                    event_count += 1
                    # Convert MonitoringEvent to raw JSON format expected by GUI
                    raw_json = {
                        "id": monitoring_event.data.get("id", str(event_count)),
                        "timestamp": monitoring_event.timestamp,
                        "type": monitoring_event.type,
                        **monitoring_event.data,
                    }
                    self.processing_queue.put(raw_json)

            except Exception as e:
                if self.running:
                    logger.error(f"MonitoringClient receive error: {e}")
                    # Handle different types of errors
                    if "Bad file descriptor" in str(e) or "Connection reset" in str(e):
                        logger.warning("⚠️ Connection lost, likely due to proxy restart")
                        # Wait for socket to be recreated
                        try:
                            self.monitoring_client.disconnect()
                            self._wait_for_socket_availability(max_wait_time=10.0)
                            self.monitoring_client.connect()
                            logger.info(
                                "✅ MonitoringClient reconnected after proxy restart"
                            )
                        except Exception as reconnect_error:
                            logger.error(
                                f"❌ Failed to reconnect after proxy restart: {reconnect_error}"
                            )
                            time.sleep(5.0)  # Wait before retry
                    else:
                        # Try standard reconnection
                        try:
                            self.monitoring_client.disconnect()
                            time.sleep(1.0)  # Brief delay before reconnect
                            self.monitoring_client.connect()
                            logger.info("✅ MonitoringClient reconnected successfully")
                        except Exception as reconnect_error:
                            logger.error(
                                f"❌ Failed to reconnect MonitoringClient: {reconnect_error}"
                            )
                            time.sleep(5.0)  # Wait before retry

    def _start_unix_stream(self):
        """Start Unix stream socket client"""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.unix_socket_path)
        self.sock.setblocking(False)

        self.running = True

        self.thread = threading.Thread(
            target=self._listen_loop_stream, daemon=True, name="UnixStreamListener"
        )
        self.thread.start()

        self.processing_thread = threading.Thread(
            target=self._processing_loop, daemon=True, name="EventProcessor"
        )
        self.processing_thread.start()

        logger.info(f"✅ Unix stream receiver started on {self.unix_socket_path}")

    def stop(self):
        """Stop network listener"""
        self.running = False

        # Close MonitoringClient
        if self.monitoring_client:
            try:
                self.monitoring_client.disconnect()
                logger.info("MonitoringClient stopped")
            except Exception as e:
                logger.warning(f"Error stopping MonitoringClient: {e}")

        # Close legacy socket
        if self.sock:
            self.sock.close()
            logger.info(f"Network receiver stopped ({self.transport_type})")

        # Wait for threads to finish
        threads = [self.thread, self.processing_thread]
        for thread in threads:
            if thread and thread.is_alive():
                thread.join(timeout=2.0)

    def set_callback(self, callback: Callable):
        """Set callback function for received data"""
        self.callback = callback

    def _listen_loop_stream(self):
        """
        Listening loop for Unix stream sockets.

        Handles newline-delimited JSON messages.
        """
        buffer = b""
        packet_count = 0

        while self.running:
            try:
                # Receive data with timeout via select
                import select

                readable, _, _ = select.select([self.sock], [], [], 1.0)

                if not readable:
                    continue

                chunk = self.sock.recv(65536)
                if not chunk:
                    # Connection closed
                    logger.warning("Stream connection closed by server")
                    break

                buffer += chunk

                # Process complete messages (newline-delimited JSON)
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line:
                        try:
                            packet_count += 1
                            raw_json = json.loads(line.decode("utf-8"))
                            self.processing_queue.put(raw_json)
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse JSON: {e}")
                        except Exception as e:
                            logger.error(f"Error processing message: {e}")

            except Exception as e:
                if self.running:
                    logger.error(f"Stream receive error: {e}")
                break

    def _processing_loop(self):
        """Async processing loop for events"""
        while self.running:
            try:
                # Get next event from queue (blocks until available)
                raw_json = self.processing_queue.get(timeout=1.0)

                # Process event asynchronously
                if self.callback:
                    # Schedule callback on main thread using Tkinter's after method
                    self._schedule_callback(raw_json)

                # Mark task as done
                self.processing_queue.task_done()

            except queue.Empty:
                # Timeout - continue loop
                continue
            except Exception as e:
                logger.error(f"Error processing event: {e}")

    def _schedule_callback(self, raw_json):
        """Schedule callback on main thread using Tkinter's after method"""
        try:
            if self.root_window and hasattr(self.root_window, "after"):
                # Use the provided root window to schedule callback on main thread
                self.root_window.after(0, lambda: self.callback(raw_json))
            else:
                # Fallback: call directly (may cause threading issues)
                logger.warning("No root window provided, calling callback directly")
                self.callback(raw_json)
        except Exception as e:
            logger.error(f"Error scheduling callback: {e}")
            # Fallback: call directly
            try:
                self.callback(raw_json)
            except Exception as e2:
                logger.error(f"Error in direct callback: {e2}")
