"""
UDP transport for event broadcasting.

Provides UDP sender and receiver for real-time event monitoring.
"""

import json
import queue
import socket
import threading
from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class UDPTransport:
    """
    UDP transport for sending and receiving events.

    Features:
    - Async queue-based sending (non-blocking)
    - Background worker thread for queue processing
    - Dual-threaded receiver (fast UDP receive + async processing)
    - Thread-safe callback scheduling
    - Graceful error handling (silent failures)
    - Enable/disable toggle

    Example:
        # Sender
        transport = UDPTransport(host='127.0.0.1', port=9999)
        transport.send_event('MyEvent', {'key': 'value'})

        # Receiver
        transport = UDPTransport(port=9999)
        transport.start_receiving(lambda msg: print(msg))
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9999,
        enabled: bool = True,
        max_queue_size: int = 1000,
        max_message_size: int = 1500,
    ):
        """
        Initialize UDP transport.

        Args:
            host: Target host for sending (default: localhost)
            port: UDP port (default: 9999)
            enabled: Enable/disable transport (default: True)
            max_queue_size: Maximum send queue size (default: 1000)
            max_message_size: Maximum message size in bytes (default: 1500)
        """
        self.host = host
        self.port = port
        self.enabled = enabled
        self.max_queue_size = max_queue_size
        self.max_message_size = max_message_size

        # Send queue and worker
        self._send_queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._send_worker_thread: threading.Thread | None = None
        self._send_running = False

        # Receive threads
        self._receive_socket: socket.socket | None = None
        self._receive_thread: threading.Thread | None = None
        self._process_thread: threading.Thread | None = None
        self._receive_queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._receive_running = False
        self._callback: Callable[[dict], None] | None = None

        # Socket for sending
        self._send_socket: socket.socket | None = None

        if self.enabled:
            self._start_send_worker()

    def _start_send_worker(self):
        """Start background worker thread for sending."""
        if self._send_running:
            return

        self._send_running = True
        self._send_worker_thread = threading.Thread(
            target=self._send_worker, daemon=True, name="udp-send-worker"
        )
        self._send_worker_thread.start()
        logger.debug(f"UDP send worker started (port={self.port})")

    def _send_worker(self):
        """Background worker that processes send queue."""
        while self._send_running:
            try:
                # Get message from queue (blocking with timeout)
                try:
                    message = self._send_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                # Send message
                self._send_message(message)

            except Exception as e:
                logger.debug(f"UDP send worker error: {e}")

    def _send_message(self, message: str):
        """
        Send message via UDP.

        Args:
            message: JSON string to send
        """
        if not self.enabled:
            return

        try:
            # Create socket if needed
            if self._send_socket is None:
                self._send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            # Truncate if too large
            message_bytes = message.encode("utf-8")
            if len(message_bytes) > self.max_message_size:
                logger.debug(
                    f"Message truncated: {len(message_bytes)} -> "
                    f"{self.max_message_size} bytes"
                )
                message_bytes = message_bytes[: self.max_message_size]

            # Send
            self._send_socket.sendto(message_bytes, (self.host, self.port))

        except Exception as e:
            # Silent failure - monitoring should never break the application
            logger.debug(f"UDP send failed: {e}")

    def send_event(
        self, event_type: str, data: dict[str, Any], event_id: str | None = None
    ):
        """
        Send event via UDP (non-blocking).

        Args:
            event_type: Event type name
            data: Event data dictionary
            event_id: Optional event ID
        """
        if not self.enabled:
            return

        try:
            # Build message
            message = {
                "id": event_id or "",
                "timestamp": "",
                "type": event_type,
                "data": data,
            }

            # Serialize to JSON (compact format)
            json_str = json.dumps(message, separators=(",", ":"))

            # Add to queue (non-blocking)
            try:
                self._send_queue.put_nowait(json_str)
            except queue.Full:
                logger.debug("UDP send queue full, dropping message")

        except Exception as e:
            logger.debug(f"Failed to queue UDP message: {e}")

    def send_message(self, message_dict: dict[str, Any]):
        """
        Send raw message dictionary via UDP.

        Args:
            message_dict: Message dictionary to send
        """
        if not self.enabled:
            return

        try:
            json_str = json.dumps(message_dict, separators=(",", ":"))
            try:
                self._send_queue.put_nowait(json_str)
            except queue.Full:
                logger.debug("UDP send queue full, dropping message")
        except Exception as e:
            logger.debug(f"Failed to queue UDP message: {e}")

    def start_receiving(self, callback: Callable[[dict], None]):
        """
        Start receiving UDP messages.

        Uses dual-threaded architecture:
        - Thread 1: Fast UDP packet reception
        - Thread 2: Message processing with callbacks

        Args:
            callback: Function to call with received messages
        """
        if self._receive_running:
            logger.warning("UDP receiver already running")
            return

        self._callback = callback
        self._receive_running = True

        # Create receive socket
        try:
            self._receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._receive_socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024
            )  # 1MB buffer
            self._receive_socket.bind(("0.0.0.0", self.port))
            self._receive_socket.settimeout(0.5)  # Timeout for graceful shutdown
        except Exception as e:
            logger.error(f"Failed to create UDP receive socket: {e}")
            self._receive_running = False
            return

        # Start receive thread
        self._receive_thread = threading.Thread(
            target=self._receive_worker, daemon=True, name="udp-receive-worker"
        )
        self._receive_thread.start()

        # Start processing thread
        self._process_thread = threading.Thread(
            target=self._process_worker, daemon=True, name="udp-process-worker"
        )
        self._process_thread.start()

        logger.info(f"UDP receiver started on port {self.port}")

    def _receive_worker(self):
        """Worker thread for receiving UDP packets."""
        while self._receive_running:
            try:
                # Receive data
                try:
                    data, addr = self._receive_socket.recvfrom(self.max_message_size)
                except TimeoutError:
                    continue
                except Exception as e:
                    if self._receive_running:
                        logger.debug(f"UDP receive error: {e}")
                    break

                # Decode and queue for processing
                try:
                    message_str = data.decode("utf-8")
                    self._receive_queue.put_nowait(message_str)
                except queue.Full:
                    logger.debug("UDP receive queue full, dropping message")
                except Exception as e:
                    logger.debug(f"Failed to decode UDP message: {e}")

            except Exception as e:
                if self._receive_running:
                    logger.debug(f"UDP receive worker error: {e}")

    def _process_worker(self):
        """Worker thread for processing received messages."""
        while self._receive_running:
            try:
                # Get message from queue
                try:
                    message_str = self._receive_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                # Parse JSON
                try:
                    message_dict = json.loads(message_str)
                except json.JSONDecodeError as e:
                    logger.debug(f"Failed to parse JSON: {e}")
                    continue

                # Call callback
                if self._callback:
                    try:
                        self._callback(message_dict)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")

            except Exception as e:
                if self._receive_running:
                    logger.debug(f"UDP process worker error: {e}")

    def stop(self):
        """Stop all UDP operations gracefully."""
        # Stop receiving
        if self._receive_running:
            self._receive_running = False

            if self._receive_socket:
                try:
                    self._receive_socket.close()
                except Exception:
                    pass

            if self._receive_thread and self._receive_thread.is_alive():
                self._receive_thread.join(timeout=2.0)

            if self._process_thread and self._process_thread.is_alive():
                self._process_thread.join(timeout=2.0)

            logger.info("UDP receiver stopped")

        # Stop sending
        if self._send_running:
            self._send_running = False

            if self._send_worker_thread and self._send_worker_thread.is_alive():
                self._send_worker_thread.join(timeout=2.0)

            if self._send_socket:
                try:
                    self._send_socket.close()
                except Exception:
                    pass

            logger.debug("UDP sender stopped")

    def enable(self):
        """Enable UDP transport."""
        if not self.enabled:
            self.enabled = True
            self._start_send_worker()
            logger.info("UDP transport enabled")

    def disable(self):
        """Disable UDP transport."""
        if self.enabled:
            self.enabled = False
            logger.info("UDP transport disabled")

    def get_queue_size(self) -> int:
        """Get current send queue size."""
        return self._send_queue.qsize()

    def is_running(self) -> bool:
        """Check if transport is running."""
        return self._send_running or self._receive_running
