"""
Abstract base class for all protocol implementations.

This module defines the Protocol interface for encoding and decoding messages
to/from byte streams.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class ProtocolError(Exception):
    """Base exception for protocol-related errors."""

    pass


class EncodeError(ProtocolError):
    """Raised when encoding a message fails."""

    pass


class DecodeError(ProtocolError):
    """Raised when decoding a message fails."""

    pass


class Protocol(ABC):
    """
    Abstract base class for all protocol implementations.

    Protocols handle the encoding of messages to bytes and decoding of bytes
    back to messages. They support streaming/incremental decoding to handle
    partial messages and message boundaries.

    The protocol layer is independent of the transport layer, allowing any
    protocol to be used with any transport.
    """

    def __init__(self):
        """Initialize the protocol."""
        self._decode_buffer = bytearray()

    @abstractmethod
    def encode(self, message: Any) -> bytes:
        """
        Encode a message to bytes.

        This method should convert a message (dict, object, etc.) into bytes
        that can be sent over a transport. The encoding should include any
        necessary framing or delimiters.

        Args:
            message: The message to encode

        Returns:
            Encoded bytes ready for transmission

        Raises:
            EncodeError: If encoding fails
        """
        pass

    @abstractmethod
    def decode_stream(self, data: bytes) -> Iterator[Any]:
        """
        Decode messages from a byte stream.

        This method should handle partial messages by maintaining an internal
        buffer. It yields complete messages as they become available.

        Args:
            data: New bytes received from transport

        Yields:
            Complete decoded messages

        Raises:
            DecodeError: If decoding fails
        """
        pass

    def reset_decoder(self) -> None:
        """
        Reset the decoder state.

        This clears any buffered partial messages. Useful when reconnecting
        or recovering from errors.
        """
        self._decode_buffer.clear()
        logger.debug("Decoder state reset")

    def get_buffer_size(self) -> int:
        """Get the current size of the decode buffer."""
        return len(self._decode_buffer)

    def validate_message(self, message: Any) -> bool:
        """
        Validate a message before encoding.

        Subclasses can override this to provide message validation.

        Args:
            message: The message to validate

        Returns:
            True if message is valid, False otherwise
        """
        return True
