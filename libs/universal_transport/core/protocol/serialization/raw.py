"""
Raw binary serialization implementation.

Provides pass-through serialization for raw binary data.
"""

from typing import Any

from .base import SerializeError, Serializer


class RawBinarySerializer(Serializer):
    """
    Raw binary serialization (pass-through).

    Provides pass-through serialization for raw binary data.
    No transformation is performed - bytes are passed through unchanged.

    Features:
    - Zero-copy serialization
    - No encoding/decoding overhead
    - Direct binary data support
    - Useful for file transfers, binary protocols

    Use cases:
    - File transfers
    - Image/media data
    - Custom binary formats
    - Pre-serialized data
    """

    def __init__(self):
        """Initialize raw binary serializer."""
        super().__init__("Raw Binary", "application/octet-stream")

    def serialize(self, data: Any) -> bytes:
        """Pass through binary data unchanged."""
        if isinstance(data, bytes):
            return data
        elif isinstance(data, bytearray):
            return bytes(data)
        elif isinstance(data, memoryview):
            return data.tobytes()
        else:
            raise SerializeError(
                f"RawBinarySerializer requires bytes-like data, got {type(data)}. "
                f"Use JSONSerializer or MessagePackSerializer for other data types."
            )

    def deserialize(self, data: bytes) -> bytes:
        """Pass through binary data unchanged."""
        return data
