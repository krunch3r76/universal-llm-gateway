"""
Transport server module - handles AsyncMonitoringServer lifecycle management.

This module is responsible for:
- Managing AsyncMonitoringServer lifecycle
- Handling background thread coordination
- Providing direct event broadcasting API
"""

import asyncio
import threading
from pathlib import Path
from typing import Any

from universal_logging import get_logger

# Import async universal_transport classes
try:
    from universal_transport.specialized import AsyncMonitoringServer

    UNIVERSAL_TRANSPORT_AVAILABLE = True
    logger = get_logger(__name__)
    logger.info("✅ Async universal_transport imported successfully")
except ImportError as e:
    logger = get_logger(__name__)
    logger.warning(f"universal_transport async not available: {e}")
    UNIVERSAL_TRANSPORT_AVAILABLE = False
    AsyncMonitoringServer = None


class TransportServerManager:
    """Manages AsyncMonitoringServer lifecycle and direct event broadcasting"""

    def __init__(
        self, enabled: bool, transport_config: dict | None = None, event_bus=None
    ):
        """
        Initialize TransportServerManager.

        Args:
            enabled: Enable monitoring
            transport_config: Configuration dict for transports
            event_bus: EventBus instance to subscribe to (optional)
        """
        self.enabled = enabled
        self.transport_config = transport_config or {}
        self.event_bus = event_bus
        self.servers = []  # Support multiple servers (Unix + TCP)
        self.server_thread = None
        self.server_loop = None  # Store reference to server's event loop

    async def start_async_server(self):
        """
        Start AsyncMonitoringServer with configured transport(s).

        Supports Unix socket (local), TCP (remote), or both simultaneously.
        Configuration determines which transports are enabled.
        """
        if not UNIVERSAL_TRANSPORT_AVAILABLE or not self.enabled:
            return

        if AsyncMonitoringServer is None:
            raise RuntimeError("AsyncMonitoringServer is not available")

        try:
            # Get transport configuration (can be string or list)
            transports = self.transport_config.get("transports", ["unix"])
            if isinstance(transports, str):
                transports = [transports]

            # Start servers for each configured transport
            for transport in transports:
                if transport == "unix":
                    unix_socket = self.transport_config.get(
                        "unix_socket_path", "/tmp/stargate_events.sock"
                    )

                    # Clean up existing socket
                    socket_file = Path(unix_socket)
                    if socket_file.exists():
                        socket_file.unlink()

                    server = AsyncMonitoringServer(
                        app_name="stargate",
                        unix_socket=unix_socket,
                        max_message_size=100 * 1024 * 1024,
                    )
                    await server.start()
                    self.servers.append(server)
                    logger.info(
                        f"✅ AsyncMonitoringServer started (Unix): {unix_socket}"
                    )

                elif transport == "tcp":
                    host = self.transport_config.get("host", "0.0.0.0")
                    port = self.transport_config.get("port", 9997)

                    server = AsyncMonitoringServer(
                        app_name="stargate",
                        host=host,
                        port=port,
                        max_message_size=100 * 1024 * 1024,
                    )
                    await server.start()
                    self.servers.append(server)
                    logger.info(
                        f"✅ AsyncMonitoringServer started (TCP): {host}:{port}"
                    )
                else:
                    logger.warning(f"Unknown transport type: {transport}")

            if not self.servers:
                raise ValueError("No valid transports configured")

            # Keep servers alive
            while True:
                await asyncio.sleep(1.0)

        except Exception as e:
            logger.error(
                f"❌ Failed to start AsyncMonitoringServer: {e}", exc_info=True
            )
            # Clean up any started servers
            for server in self.servers:
                try:
                    await server.stop()
                except Exception:
                    pass
            self.servers = []
            raise

    def start_server_in_background(self):
        """
        Start async server in background thread with its own event loop
        """
        if not UNIVERSAL_TRANSPORT_AVAILABLE or not self.enabled:
            return

        def run_async_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.server_loop = loop  # Store reference for send_event_nonblocking

            try:
                loop.run_until_complete(self.start_async_server())
                loop.run_forever()
            except Exception as e:
                logger.error(f"Async server thread error: {e}")
            finally:
                self.server_loop = None  # Clear reference when loop closes
                loop.close()
                logger.info("Async server thread exited")

        # Start the background thread
        self.server_thread = threading.Thread(
            target=run_async_server, daemon=True, name="AsyncMonitoringServer"
        )
        self.server_thread.start()
        logger.info("Started AsyncMonitoringServer in background thread")

        # Wait a moment to check if server started successfully
        import time

        time.sleep(0.5)
        if self.server_thread.is_alive():
            logger.debug("AsyncMonitoringServer thread is running")
        else:
            logger.warning("⚠️ AsyncMonitoringServer thread may have exited immediately")

    def subscribe_to_eventbus(self):
        """Subscribe to EventBus events and forward them to AsyncMonitoringServer"""
        if not self.event_bus:
            logger.warning("⚠️ EventBus not provided, cannot subscribe")
            return

        # Import signal definitions
        from src.core.transport.server import (
            MONITORING_CHAT_COMPLETION,
            MONITORING_ERROR,
            MONITORING_PARAMETER_COMPARISON,
            MONITORING_PRE_PROCESSING,
            MONITORING_REQUEST_INFO,
            MONITORING_STREAMING_CHUNK,
        )

        # Subscribe to all monitoring event types
        self.event_bus.subscribe_async(
            MONITORING_CHAT_COMPLETION, self._handle_eventbus_event
        )
        self.event_bus.subscribe_async(
            MONITORING_STREAMING_CHUNK, self._handle_eventbus_event
        )
        self.event_bus.subscribe_async(
            MONITORING_REQUEST_INFO, self._handle_eventbus_event
        )
        self.event_bus.subscribe_async(
            MONITORING_PRE_PROCESSING, self._handle_eventbus_event
        )
        self.event_bus.subscribe_async(
            MONITORING_PARAMETER_COMPARISON, self._handle_eventbus_event
        )
        self.event_bus.subscribe_async(MONITORING_ERROR, self._handle_eventbus_event)

        logger.info("✅ TransportServerManager subscribed to EventBus events")

    async def _handle_eventbus_event(self, event):
        """
        Handle EventBus event and forward to AsyncMonitoringServer(s).

        This wrapper handles async execution in the server's event loop.

        Args:
            event: Event instance from EventBus
        """
        if not self.enabled or not self.servers:
            return

        try:
            # Extract event type from signal (e.g., "monitoring.chat_completion" -> "chat_completion")
            signal = event.signal
            if signal.startswith("monitoring."):
                event_type = signal.replace("monitoring.", "")
            else:
                event_type = signal

            # Use payload as event_data
            event_data = event.payload if event.payload else {}

            # Await the async send
            await self.async_send_event(event_type, event_data)

        except Exception as e:
            logger.error(f"Error handling EventBus event: {e}", exc_info=True)

    async def async_send_event(
        self, event_type: str, event_data: dict[str, Any]
    ) -> bool:
        """
        Send event through all active AsyncMonitoringServer(s).

        This broadcasts to all configured transports (Unix + TCP).

        Args:
            event_type: The type of event (e.g. 'chat_completion')
            event_data: Event data dictionary

        Returns:
            bool: True if event was sent to at least one client
        """
        if not self.enabled or not self.servers:
            return False

        success = False
        for server in self.servers:
            try:
                # Send event through this server
                if await server.send_event(event_type, event_data):
                    success = True
            except Exception as e:
                # Handle client disconnection gracefully - this is expected behavior
                error_type = type(e).__name__
                if (
                    "SendError" not in error_type
                    and "connection closed" not in str(e).lower()
                ):
                    # Other errors should be logged as errors
                    logger.error(f"Failed to send event via server: {e}")

        return success

    def send_event_nonblocking(
        self, event_type: str, event_data: dict[str, Any]
    ) -> bool:
        """
        Non-blocking wrapper for async_send_event.

        Creates a background task to send the event asynchronously.

        Args:
            event_type: The type of event (e.g. 'chat_completion')
            event_data: Event data dictionary

        Returns:
            bool: True if the event was queued for sending (not necessarily sent)
        """
        if not self.enabled or not self.servers:
            return False

        # Get the running event loop from the server thread to schedule the task
        loop = self.server_loop

        if loop and loop.is_running():
            try:
                # Schedule task creation within the event loop
                # async_send_event already handles all exceptions internally
                def schedule_task():
                    task = loop.create_task(
                        self.async_send_event(event_type, event_data)
                    )

                    # Add done callback to catch any unretrieved task exceptions
                    def handle_task_done(task):
                        try:
                            task.result()  # Will raise if task had unhandled exception
                        except Exception:
                            # Exceptions should already be handled by async_send_event,
                            # but this prevents "unretrieved task exception" warnings
                            pass

                    task.add_done_callback(handle_task_done)

                loop.call_soon_threadsafe(schedule_task)
                return True
            except Exception as e:
                logger.error(f"Failed to schedule event send: {e}")
                return False
        else:
            # Fallback to creating a new thread if the loop isn't available
            # This is less efficient and should be avoided if possible
            thread = threading.Thread(
                target=lambda: asyncio.run(
                    self.async_send_event(event_type, event_data)
                ),
                daemon=True,
            )
            thread.start()
            return True

    async def close_async(self):
        """Close all async servers gracefully"""
        for server in self.servers:
            try:
                # Use context manager exit point which calls stop() internally
                await server.__aexit__(None, None, None)
                logger.info("AsyncMonitoringServer closed gracefully")
            except Exception as e:
                logger.error(f"Error closing AsyncMonitoringServer: {e}")
        self.servers = []

    def close(self):
        """Close transport server"""
        logger.info("TransportServerManager closed")

        # Close async servers if running
        if self.servers:
            # Create a new thread to close the servers
            def close_server_thread():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self.close_async())
                except Exception as e:
                    logger.error(f"Error closing servers: {e}")
                finally:
                    loop.close()

            # Start a thread to close the servers
            close_thread = threading.Thread(target=close_server_thread, daemon=True)
            close_thread.start()
            close_thread.join(timeout=2.0)  # Wait up to 2 seconds for clean shutdown
