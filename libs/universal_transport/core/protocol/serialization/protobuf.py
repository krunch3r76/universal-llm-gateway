"""
Protocol Buffers serialization implementation.

Provides strongly-typed, efficient binary serialization.
"""

from typing import Any

from .base import DeserializeError, SerializeError, Serializer


class ProtobufSerializer(Serializer):
    """
    Protocol Buffers serialization.

    Provides strongly-typed, efficient binary serialization.
    Compatible with process_ipc Protobuf format.

    Features:
    - Strongly typed
    - Very compact binary format
    - Schema evolution support
    - Cross-language compatibility
    - process_ipc compatibility

    Note: Requires 'protobuf' package and message classes to be defined.
    """

    def __init__(self, message_class: type | None = None):
        """
        Initialize Protobuf serializer.

        Args:
            message_class: Protobuf message class for serialization/deserialization.
                         If None, expects data to already be protobuf message instances.
        """
        super().__init__("Protocol Buffers", "application/x-protobuf")
        self.message_class = message_class
        self._protobuf_message = None  # Lazy-loaded

    def _lazy_load_protobuf(self):
        """Lazy-load protobuf module to avoid hard dependency."""
        if self._protobuf_message is None:
            try:
                from google.protobuf import message

                self._protobuf_message = message
            except ImportError:
                raise ImportError(
                    "Protobuf serialization requires the 'protobuf' package. "
                    "Install it with: pip install protobuf"
                )
        return self._protobuf_message

    def serialize(self, data: Any) -> bytes:
        """Serialize protobuf message to binary format."""
        self._lazy_load_protobuf()
        try:
            # If data is already a protobuf message, serialize it directly
            if hasattr(data, "SerializeToString"):
                return data.SerializeToString()

            # If message_class is provided, create instance and populate it
            if self.message_class:
                if isinstance(data, dict):
                    # Create message instance and populate from dict
                    message = self.message_class()
                    # This is a simplified approach - in practice, you'd need
                    # proper dict-to-protobuf conversion logic based on your schema
                    for key, value in data.items():
                        if hasattr(message, key):
                            setattr(message, key, value)
                    return message.SerializeToString()
                else:
                    raise SerializeError(
                        f"Expected dict or protobuf message, got {type(data)}"
                    )
            else:
                raise SerializeError(
                    "No message_class provided and data is not a protobuf message"
                )

        except Exception as e:
            raise SerializeError(f"Failed to serialize data as Protobuf: {e}")

    def deserialize(self, data: bytes) -> Any:
        """Deserialize protobuf binary data to Python object."""
        self._lazy_load_protobuf()
        try:
            if not self.message_class:
                raise DeserializeError("No message_class provided for deserialization")

            message = self.message_class()
            message.ParseFromString(data)
            return message

        except Exception as e:
            raise DeserializeError(f"Failed to deserialize Protobuf data: {e}")
