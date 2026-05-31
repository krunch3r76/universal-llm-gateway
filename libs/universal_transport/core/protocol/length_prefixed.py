"""
Length-prefixed protocol implementation for universal_transport.

This module provides the primary framing protocol that eliminates delimiter-scanning
issues and asyncio readline buffer limits. It uses a simple 4-byte length prefix
followed by the payload data.

Design principles:
- Binary-safe framing (no delimiter conflicts)
- No buffer scanning (avoids readline() pitfalls)
- Pluggable serialization support
- Efficient for multi-MB payloads
- Explicit state machine for stream processing

Frame format: [4-byte length (big-endian)][payload bytes]
"""

import struct
from collections.abc import Iterator
from enum import Enum
from typing import Any

from universal_logging import get_logger

from .base import DecodeError, EncodeError, Protocol
from .serializers import JSONSerializer, Serializer

logger = get_logger(__name__)


class DecodingState(Enum):
    """State machine for length-prefixed decoding."""

    READING_LENGTH = "reading_length"  # Need 4 bytes for length prefix
    READING_PAYLOAD = "reading_payload"  # Need N bytes for payload


class LengthPrefixedProtocol(Protocol):
    """
    Length-prefixed framing protocol with pluggable serialization.

    This protocol eliminates the entire class of delimiter-scanning issues
    that plague JSONL and similar text-based protocols. By using a fixed
    4-byte length prefix, we know exactly how many bytes to read for each
    message, eliminating buffer scanning and avoiding asyncio readline
    buffer limits.

    Frame format:
        [4-byte length (big-endian)][payload bytes]

    Key benefits:
    - Binary-safe (no delimiter conflicts)
    - No buffer scanning required
    - Works with asyncio.readexactly() (no readline buffer limits)
    - Supports any serialization format
    - Efficient for large payloads (1MB, 10MB, 100MB+)
    - Deterministic framing (exact byte counts)

    Attributes:
        serializer: Pluggable serializer for payload encoding/decoding
        max_message_size: Maximum allowed message size (security limit)
        _state: Current decoder state machine state
        _expected_length: Expected payload length when reading payload
    """

    # Protocol constants
    LENGTH_PREFIX_SIZE = 4  # 4 bytes for uint32 length prefix
    DEFAULT_MAX_MESSAGE_SIZE = (
        4 * 1024 * 1024
    )  # 4MB default limit (appropriate for inference IPC)
    MAX_THEORETICAL_SIZE = 2**32 - 1  # ~4GB with 4-byte length prefix

    def __init__(
        self,
        serializer: Serializer | None = None,
        max_message_size: int = DEFAULT_MAX_MESSAGE_SIZE,
    ):
        """
        Initialize length-prefixed protocol.

        Args:
            serializer: Serializer for payload encoding/decoding (default: JSON)
            max_message_size: Maximum allowed message size in bytes (default: 100MB)
        """
        super().__init__()

        # Use JSON serializer by default for human-readable debugging
        self.serializer = serializer or JSONSerializer()

        # Validate max message size
        if max_message_size <= 0:
            raise ValueError("max_message_size must be positive")
        if max_message_size > self.MAX_THEORETICAL_SIZE:
            raise ValueError(
                f"max_message_size cannot exceed {self.MAX_THEORETICAL_SIZE} bytes"
            )

        self.max_message_size = max_message_size

        # Decoder state machine
        self._state = DecodingState.READING_LENGTH
        self._expected_length = 0

        logger.debug(
            f"Length-prefixed protocol initialized: "
            f"serializer={self.serializer}, max_size={max_message_size}"
        )

    def encode(self, message: Any) -> bytes:
        """
        Encode a message with length prefix.

        Process:
        1. Serialize message to bytes using configured serializer
        2. Prepend 4-byte length prefix (big-endian uint32)
        3. Return complete frame ready for transmission

        Args:
            message: Message to encode (format depends on serializer)

        Returns:
            Length-prefixed frame: [4-byte length][payload]

        Raises:
            EncodeError: If encoding fails or message exceeds size limit
        """
        try:
            # Validate message if supported by base protocol
            if not self.validate_message(message):
                raise EncodeError(f"Message validation failed: {message}")

            # Serialize payload using configured serializer
            payload_bytes = self.serializer.serialize(message)
            payload_length = len(payload_bytes)

            # Check size limits
            if payload_length > self.max_message_size:
                raise EncodeError(
                    f"Message payload exceeds maximum size: "
                    f"{payload_length} > {self.max_message_size} bytes"
                )

            if payload_length == 0:
                logger.warning("Encoding zero-length message")

            # Create length prefix (4-byte big-endian unsigned integer)
            length_prefix = struct.pack("!I", payload_length)

            # Combine length prefix + payload
            frame = length_prefix + payload_bytes

            logger.debug(
                f"Encoded message: length={payload_length} bytes, "
                f"serializer={self.serializer.name}, "
                f"total_frame={len(frame)} bytes"
            )

            return frame

        except EncodeError:
            # Re-raise encode errors without wrapping
            raise
        except Exception as e:
            raise EncodeError(f"Failed to encode length-prefixed message: {e}")

    def decode_stream(self, data: bytes) -> Iterator[Any]:
        """
        Decode messages from a byte stream using explicit state machine.

        This method implements a robust state machine that processes incoming
        bytes without any delimiter scanning. It explicitly tracks whether
        we're reading a length prefix or payload data.

        State machine:
        READING_LENGTH: Need 4 bytes to read uint32 length prefix
        READING_PAYLOAD: Need N bytes (from length prefix) to read payload

        This approach eliminates:
        - Buffer scanning for delimiters
        - asyncio readline() buffer limits
        - Delimiter conflicts with payload data
        - Ambiguous message boundaries

        Args:
            data: New bytes received from transport

        Yields:
            Decoded messages (format depends on serializer)

        Raises:
            DecodeError: If decoding fails or protocol violations occur
        """
        # Add new data to decode buffer
        self._decode_buffer.extend(data)

        # Process complete messages using state machine
        while True:
            if self._state == DecodingState.READING_LENGTH:
                # Need 4 bytes for length prefix
                if len(self._decode_buffer) < self.LENGTH_PREFIX_SIZE:
                    break  # Need more data

                # Extract length prefix (4-byte big-endian uint32)
                length_bytes = bytes(self._decode_buffer[: self.LENGTH_PREFIX_SIZE])
                payload_length = struct.unpack("!I", length_bytes)[0]

                # Validate payload length
                if payload_length > self.max_message_size:
                    # Clear buffer to prevent memory exhaustion
                    self._decode_buffer.clear()
                    raise DecodeError(
                        f"Message payload length {payload_length} exceeds maximum "
                        f"{self.max_message_size} bytes. This may indicate "
                        f"corrupted data or malicious input."
                    )

                if payload_length == 0:
                    logger.warning("Received zero-length message")

                # Transition to reading payload
                self._expected_length = payload_length
                self._state = DecodingState.READING_PAYLOAD

                # Remove length prefix from buffer
                del self._decode_buffer[: self.LENGTH_PREFIX_SIZE]

                logger.debug(f"Decoded length prefix: expecting {payload_length} bytes")

            elif self._state == DecodingState.READING_PAYLOAD:
                # Need _expected_length bytes for payload
                if len(self._decode_buffer) < self._expected_length:
                    break  # Need more data

                # Extract complete payload
                payload_bytes = bytes(self._decode_buffer[: self._expected_length])

                # Remove payload from buffer
                del self._decode_buffer[: self._expected_length]

                try:
                    # Deserialize payload using configured serializer
                    message = self.serializer.deserialize(payload_bytes)

                    logger.debug(
                        f"Decoded message: length={self._expected_length} bytes, "
                        f"serializer={self.serializer.name}"
                    )

                    yield message

                except Exception as e:
                    logger.error(f"Failed to deserialize payload: {e}")
                    raise DecodeError(f"Payload deserialization failed: {e}")

                # Transition back to reading length prefix
                self._state = DecodingState.READING_LENGTH
                self._expected_length = 0

            else:
                # Should never happen, but defensive programming
                raise DecodeError(f"Invalid decoder state: {self._state}")

    def reset_decoder(self) -> None:
        """
        Reset the decoder state machine.

        Clears the decode buffer and resets the state machine to initial state.
        Useful when reconnecting or recovering from protocol errors.
        """
        super().reset_decoder()
        self._state = DecodingState.READING_LENGTH
        self._expected_length = 0
        logger.debug("Length-prefixed decoder state reset")

    def get_decoder_state(self) -> dict:
        """
        Get current decoder state for debugging.

        Returns:
            Dictionary with decoder state information
        """
        return {
            "state": self._state.value,
            "buffer_size": len(self._decode_buffer),
            "expected_length": self._expected_length,
            "max_message_size": self.max_message_size,
            "serializer": str(self.serializer),
        }

    def validate_message(self, message: Any) -> bool:
        """
        Validate a message before encoding.

        This validation is delegated to the configured serializer, as different
        serializers have different requirements for valid input data.

        Args:
            message: The message to validate

        Returns:
            True if message can be serialized, False otherwise
        """
        try:
            # Test serialization (without storing the result)
            self.serializer.serialize(message)
            return True
        except Exception:
            return False

    def estimate_encoded_size(self, message: Any) -> int:
        """
        Estimate the encoded size of a message.

        This performs actual serialization to get the exact size, which
        includes the 4-byte length prefix overhead.

        Args:
            message: Message to estimate size for

        Returns:
            Estimated encoded size in bytes (including length prefix)

        Raises:
            EncodeError: If message cannot be serialized
        """
        payload_bytes = self.serializer.serialize(message)
        return self.LENGTH_PREFIX_SIZE + len(payload_bytes)

    def set_serializer(self, serializer: Serializer) -> None:
        """
        Change the serializer used for encoding/decoding.

        Note: This will affect all future encode/decode operations.
        Existing buffered data may become incompatible.

        Args:
            serializer: New serializer to use
        """
        old_serializer = self.serializer
        self.serializer = serializer
        logger.info(f"Changed serializer: {old_serializer} -> {serializer}")

    def __str__(self) -> str:
        return f"LengthPrefixedProtocol(serializer={self.serializer}, max_size={self.max_message_size})"

    def __repr__(self) -> str:
        return (
            f"LengthPrefixedProtocol(serializer={self.serializer!r}, "
            f"max_message_size={self.max_message_size})"
        )


# Convenience factory functions for common configurations


def create_json_protocol(
    max_message_size: int = LengthPrefixedProtocol.DEFAULT_MAX_MESSAGE_SIZE,
) -> LengthPrefixedProtocol:
    """Create length-prefixed protocol with JSON serializer."""
    from .serializers import JSONSerializer

    return LengthPrefixedProtocol(JSONSerializer(), max_message_size)


def create_messagepack_protocol(
    max_message_size: int = LengthPrefixedProtocol.DEFAULT_MAX_MESSAGE_SIZE,
) -> LengthPrefixedProtocol:
    """Create length-prefixed protocol with MessagePack serializer."""
    from .serializers import MessagePackSerializer

    return LengthPrefixedProtocol(MessagePackSerializer(), max_message_size)


def create_raw_protocol(
    max_message_size: int = LengthPrefixedProtocol.DEFAULT_MAX_MESSAGE_SIZE,
) -> LengthPrefixedProtocol:
    """Create length-prefixed protocol with raw binary serializer."""
    from .serializers import RawBinarySerializer

    return LengthPrefixedProtocol(RawBinarySerializer(), max_message_size)


def create_protobuf_protocol(
    message_class: type,
    max_message_size: int = LengthPrefixedProtocol.DEFAULT_MAX_MESSAGE_SIZE,
) -> LengthPrefixedProtocol:
    """Create length-prefixed protocol with Protobuf serializer."""
    from .serializers import ProtobufSerializer

    return LengthPrefixedProtocol(ProtobufSerializer(message_class), max_message_size)
