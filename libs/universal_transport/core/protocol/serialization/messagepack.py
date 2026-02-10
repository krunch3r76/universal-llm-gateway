"""
MessagePack serialization implementation.

Provides compact binary serialization with good performance.
"""

from typing import Any

from .base import DeserializeError, SerializeError, Serializer


class MessagePackSerializer(Serializer):
    """
    MessagePack serialization (binary format).

    Provides compact binary serialization with good performance.
    More efficient than JSON for large or complex data structures.

    Features:
    - Compact binary format
    - Faster than JSON
    - Preserves type information
    - Cross-language compatibility

    Note: Requires 'msgpack' package to be installed.
    """

    def __init__(self):
        """Initialize MessagePack serializer."""
        super().__init__("MessagePack", "application/msgpack")

        # Import msgpack with helpful error message
        try:
            import msgpack

            self._msgpack = msgpack
        except ImportError:
            raise ImportError(
                "MessagePack serialization requires the 'msgpack' package. "
                "Install it with: pip install msgpack"
            )

    def serialize(self, data: Any) -> bytes:
        """Serialize data to MessagePack binary format."""
        try:
            return self._msgpack.packb(data)
        except Exception as e:
            raise SerializeError(f"Failed to serialize data as MessagePack: {e}")

    def deserialize(self, data: bytes) -> Any:
        """Deserialize MessagePack binary data to Python object."""
        try:
            return self._msgpack.unpackb(data, raw=False)
        except Exception as e:
            raise DeserializeError(f"Failed to deserialize MessagePack data: {e}")
