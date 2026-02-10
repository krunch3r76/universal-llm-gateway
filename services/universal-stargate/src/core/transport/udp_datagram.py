"""
UDP datagram transport for lightweight streaming events.

Wraps existing UDP logic - suitable for frequent small events like streaming chunks.
Pure async implementation for consistency with other transports.
"""

import json
import socket
from typing import Any

from universal_logging import get_logger

from .base import EventTransport

logger = get_logger(__name__)


class UDPDatagramTransport(EventTransport):
    """
    UDP datagram transport.

    Features:
    - Fast, connectionless
    - Suitable for frequent small events (streaming chunks)
    - No delivery guarantee (fire-and-forget)
    - Size limited to ~65KB per packet

    Limitations:
    - Large payloads will fail with "Message too long"
    - No ordering guarantee
    - Packets can be lost

    Thread Safety: Not needed. All access from single async context.

    Use for: streaming_chunk events (small, frequent, lossy OK)
    Don't use for: request events (large, critical data)
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.host = self.config.get("host", "127.0.0.1")
        self.port = self.config.get("port", 9999)
        self.sock: socket.socket | None = None
        self.addr = (self.host, self.port)

    async def start(self):
        """Initialize UDP socket (async for interface consistency)."""
        if self._started:
            logger.warning(f"UDP transport already started on {self.host}:{self.port}")
            return

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            # Set socket options for better reliability
            self.sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024
            )  # 1MB send buffer

            self._started = True
            logger.info(f"✅ UDP transport started, sending to {self.host}:{self.port}")

        except Exception as e:
            logger.error(f"❌ Failed to start UDP transport: {e}")
            self.enabled = False
            raise

    async def stop(self):
        """Close UDP socket (async for interface consistency)."""
        if not self._started:
            return

        self._started = False

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        logger.info("UDP transport stopped")

    async def send_event(self, event_data: dict[str, Any]) -> bool:
        """
        Send event via UDP (async for interface consistency).

        Returns False if message is too large or send fails.
        Note: UDP sendto is non-blocking for small messages, so no async needed.
        """
        if not self.enabled or not self._started or not self.sock:
            return False

        try:
            # Serialize to JSON
            message = json.dumps(event_data, separators=(",", ":"))
            message_bytes = message.encode("utf-8")

            # Check size limit (UDP max is 65507 bytes, use 64KB as safe limit)
            if len(message_bytes) > 64 * 1024:
                logger.warning(
                    f"UDP message too large ({len(message_bytes)} bytes), "
                    f"event type: {event_data.get('type', 'unknown')} - skipping"
                )
                return False

            # Send datagram (non-blocking for small messages)
            self.sock.sendto(message_bytes, self.addr)
            return True

        except OSError as e:
            if e.errno == 90:  # Message too long
                event_type = event_data.get("type", "unknown")
                logger.warning(f"UDP message too long for event type: {event_type}")
            else:
                logger.debug(f"UDP send error: {e}")
            return False

        except Exception as e:
            logger.debug(f"Error in UDP send_event: {e}")
            return False
