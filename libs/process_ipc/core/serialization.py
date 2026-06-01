"""
Serialization options for IPC messages.

Provides multiple serialization formats including Protocol Buffers (default) and JSON.
"""

import json
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class SerializationFormat(Enum):
    """Available serialization formats."""

    JSON = "json"
    PROTOBUF = "protobuf"


class MessageSerializer(ABC):
    """Abstract base class for message serializers."""

    @abstractmethod
    def serialize(self, message: dict[str, Any]) -> bytes:
        """Serialize a message to bytes."""
        pass

    @abstractmethod
    def deserialize(self, data: bytes) -> dict[str, Any]:
        """Deserialize bytes to a message."""
        pass

    @abstractmethod
    def get_format(self) -> SerializationFormat:
        """Get the serialization format."""
        pass


class JSONSerializer(MessageSerializer):
    """JSON-based message serializer."""

    def serialize(self, message: dict[str, Any]) -> bytes:
        """Serialize a message to JSON bytes."""
        return json.dumps(message, ensure_ascii=False).encode("utf-8")

    def deserialize(self, data: bytes) -> dict[str, Any]:
        """Deserialize JSON bytes to a message."""
        return json.loads(data.decode("utf-8"))

    def get_format(self) -> SerializationFormat:
        """Get the serialization format."""
        return SerializationFormat.JSON


class ProtobufSerializer(MessageSerializer):
    """Protocol Buffer-based message serializer."""

    def __init__(self, max_message_size: int = 1024 * 1024):
        """
        Initialize the protobuf serializer.

        Args:
            max_message_size: Maximum message size in bytes (default 1 MiB)
        """
        self._message_class = None
        self._initialized = False
        self._max_message_size = max_message_size

    def _ensure_initialized(self):
        """Ensure protobuf classes are available and limits are set."""
        import sys

        print(
            f"DEBUG: ProtobufSerializer._ensure_initialized() called, "
            f"max_size={self._max_message_size}",
            file=sys.stderr,
        )

        # DEBUG: Check if protobuf library is installed
        try:
            import google.protobuf

            print(
                f"DEBUG: Protobuf version: {google.protobuf.__version__}",
                file=sys.stderr,
            )
            print(
                f"DEBUG: Protobuf location: {google.protobuf.__file__}", file=sys.stderr
            )
        except ImportError:
            print("DEBUG: Protobuf NOT installed", file=sys.stderr)

        if self._initialized:
            return

        try:
            # Set global protobuf message size limit
            # This affects any real protobuf usage in the process
            try:
                import google.protobuf.message as pb_message

                # Try to set the limit using the internal C++ parser API
                try:
                    # Set the C++ parser limit directly
                    from google.protobuf.internal import decoder

                    if hasattr(decoder, "_SetDefaultSizeLimit"):
                        decoder._SetDefaultSizeLimit(self._max_message_size)
                        import sys

                        print(
                            f"Protobuf C++ parser limit set to "
                            f"{self._max_message_size} bytes",
                            file=sys.stderr,
                        )
                    elif hasattr(pb_message, "SetDefaultSizeLimit"):
                        pb_message.SetDefaultSizeLimit(self._max_message_size)
                        import sys

                        print(
                            f"Protobuf message limit set to {self._max_message_size} "
                            f"bytes",
                            file=sys.stderr,
                        )
                    else:
                        # Fallback: try to set environment variable
                        import os

                        os.environ["PROTOBUF_MESSAGE_SIZE_LIMIT"] = str(
                            self._max_message_size
                        )
                        import sys

                        print(
                            f"Protobuf limit set via environment variable to "
                            f"{self._max_message_size} bytes",
                            file=sys.stderr,
                        )
                except (ImportError, AttributeError) as e2:
                    # Try alternative approaches
                    import sys

                    print(
                        f"Could not set protobuf C++ limit: {e2}, trying alternative",
                        file=sys.stderr,
                    )
                    # Try setting via environment variable as last resort
                    import os

                    os.environ["PROTOBUF_MESSAGE_SIZE_LIMIT"] = str(
                        self._max_message_size
                    )
                    print(
                        f"Set PROTOBUF_MESSAGE_SIZE_LIMIT env var to "
                        f"{self._max_message_size} bytes",
                        file=sys.stderr,
                    )
            except (ImportError, AttributeError) as e:
                # Protobuf not available or API changed, continue with JSON mock
                import sys

                print(f"Could not set protobuf limit: {e}", file=sys.stderr)

            # Try to import protobuf
            import google.protobuf.message as pb_message

            # Create a simple message class dynamically
            # This is a minimal implementation - in production you'd use .proto files
            self._message_class = self._create_message_class()
            self._initialized = True

        except ImportError:
            # Protobuf not available at all, use pure JSON mock
            self._message_class = self._create_message_class()
            self._initialized = True

    def _create_message_class(self):
        """Create a minimal protobuf message class."""
        # This is a simplified implementation
        # In production, you'd use protoc to generate classes from .proto files

        class IPCMessage:
            """Minimal protobuf-like message class."""

            def __init__(self):
                self.signal = ""
                self.payload = ""
                self.id = ""
                self.timestamp = ""
                self.correlation_id = ""
                self.worker_id = ""

            def SerializeToString(self) -> bytes:
                """Serialize to bytes."""
                data = {
                    "signal": self.signal,
                    "payload": self.payload,
                    "id": self.id,
                    "timestamp": self.timestamp,
                    "correlation_id": self.correlation_id,
                    "worker_id": self.worker_id,
                }
                return json.dumps(data).encode("utf-8")

            @classmethod
            def FromString(cls, data: bytes) -> "IPCMessage":
                """Deserialize from bytes."""
                import sys

                print(
                    f"CUSTOM FromString called with data size: {len(data)} bytes",
                    file=sys.stderr,
                )
                # Check size BEFORE deserialization to avoid C++ protobuf parser error
                size_limit = getattr(cls, "_size_limit", 64 * 1024)  # Default 64KB
                if len(data) > size_limit:
                    raise ValueError(
                        f"Message size {len(data)} bytes exceeds limit of {size_limit} "
                        f"bytes"
                    )
                try:
                    obj = cls()
                    message_data = json.loads(data.decode("utf-8"))
                    obj.signal = message_data.get("signal", "")
                    obj.payload = message_data.get("payload", "")
                    obj.id = message_data.get("id", "")
                    obj.timestamp = message_data.get("timestamp", "")
                    obj.correlation_id = message_data.get("correlation_id", "")
                    obj.worker_id = message_data.get("worker_id", "")
                    payload_len = len(obj.payload) if obj.payload else 0
                    print(
                        f"FromString: payload size = {payload_len}",
                        file=sys.stderr,
                    )
                    return obj
                except json.JSONDecodeError as e:
                    print(
                        f"JSON decode error in custom FromString: {e}", file=sys.stderr
                    )
                    raise

        # Set the size limit on the class
        IPCMessage._size_limit = self._max_message_size
        return IPCMessage

    def serialize(self, message: dict[str, Any]) -> bytes:
        """Serialize a message to protobuf bytes."""
        self._ensure_initialized()

        # Convert message to protobuf format
        pb_message = self._message_class()
        pb_message.signal = message.get("signal", "")
        pb_message.payload = json.dumps(message.get("payload", {}))
        pb_message.id = message.get("id", "")
        pb_message.timestamp = message.get("timestamp", "")
        pb_message.correlation_id = message.get("correlation_id", "")
        pb_message.worker_id = message.get("worker_id", "")

        serialized = pb_message.SerializeToString()

        # Check size AFTER serialization
        if len(serialized) > self._max_message_size:
            raise ValueError(
                f"Message size {len(serialized)} bytes exceeds limit of "
                f"{self._max_message_size} bytes"
            )

        return serialized

    def deserialize(self, data: bytes) -> dict[str, Any]:
        """Deserialize protobuf bytes to a message."""
        import sys

        print(
            f"DEBUG: ProtobufSerializer.deserialize() called with {len(data)} bytes",
            file=sys.stderr,
        )
        self._ensure_initialized()

        # Check size BEFORE deserialization
        if len(data) > self._max_message_size:
            raise ValueError(
                f"Message size {len(data)} bytes exceeds limit of "
                f"{self._max_message_size} bytes"
            )

        try:
            pb_message = self._message_class.FromString(data)
        except Exception as e:
            # Log the error with data size for debugging
            print(f"ERROR in FromString: {e}", file=__import__("sys").stderr)
            print(f"Data size: {len(data)} bytes", file=__import__("sys").stderr)
            raise

        # Convert protobuf to message format
        message = {
            "signal": pb_message.signal,
            "payload": json.loads(pb_message.payload) if pb_message.payload else {},
            "id": pb_message.id,
            "timestamp": pb_message.timestamp,
            "correlation_id": pb_message.correlation_id,
            "worker_id": pb_message.worker_id,
        }

        return message

    def get_format(self) -> SerializationFormat:
        """Get the serialization format."""
        return SerializationFormat.PROTOBUF


class SerializationManager:
    """Manager for different serialization formats."""

    def __init__(
        self,
        # Changed from PROTOBUF to JSON to avoid format mismatch
        default_format: SerializationFormat = SerializationFormat.JSON,
        max_message_size: int = 1024 * 1024,
    ):
        """
        Initialize the serialization manager.

        Args:
            default_format: Default serialization format
            max_message_size: Maximum message size for protobuf (bytes)
        """
        self._default_format = default_format
        self._max_message_size = max_message_size
        self._serializers: dict[SerializationFormat, MessageSerializer] = {
            SerializationFormat.JSON: JSONSerializer(),
            SerializationFormat.PROTOBUF: ProtobufSerializer(
                max_message_size=max_message_size
            ),
        }

    def get_serializer(
        self, format: SerializationFormat | None = None
    ) -> MessageSerializer:
        """
        Get a serializer for the specified format.

        Args:
            format: Serialization format (uses default if None)

        Returns:
            MessageSerializer: Serializer instance
        """
        if format is None:
            format = self._default_format

        return self._serializers[format]

    def serialize(
        self, message: dict[str, Any], format: SerializationFormat | None = None
    ) -> bytes:
        """
        Serialize a message using the specified format.

        Args:
            message: Message to serialize
            format: Serialization format (uses default if None)

        Returns:
            bytes: Serialized message
        """
        serializer = self.get_serializer(format)
        return serializer.serialize(message)

    def deserialize(
        self, data: bytes, format: SerializationFormat | None = None
    ) -> dict[str, Any]:
        """
        Deserialize data using the specified format.

        Args:
            data: Data to deserialize
            format: Serialization format (uses default if None)

        Returns:
            Dict: Deserialized message
        """
        serializer = self.get_serializer(format)

        # print(
        #     f"DEBUG: Using serializer: {type(serializer).__name__}",
        #     file=sys.stderr,
        # )
        return serializer.deserialize(data)

    def set_default_format(self, format: SerializationFormat) -> None:
        """
        Set the default serialization format.

        Args:
            format: New default format
        """
        self._default_format = format

    def get_default_format(self) -> SerializationFormat:
        """Get the current default format."""
        return self._default_format

    def get_available_formats(self) -> list[SerializationFormat]:
        """Get list of available serialization formats."""
        return list(self._serializers.keys())


# Global serialization manager instance with default limit
_serialization_manager = SerializationManager(max_message_size=1024 * 1024)


def get_serialization_manager() -> SerializationManager:
    """Get the global serialization manager."""
    return _serialization_manager


def configure_serialization_manager(max_message_size: int) -> None:
    """
    Configure the global serialization manager with a new message size limit.

    Args:
        max_message_size: Maximum message size in bytes
    """
    global _serialization_manager
    _serialization_manager = SerializationManager(
        default_format=_serialization_manager.get_default_format(),
        max_message_size=max_message_size,
    )


def serialize_message(
    message: dict[str, Any], format: SerializationFormat | None = None
) -> bytes:
    """
    Serialize a message using the global serialization manager.

    Args:
        message: Message to serialize
        format: Serialization format (uses default if None)

    Returns:
        bytes: Serialized message
    """
    return _serialization_manager.serialize(message, format)


def deserialize_message(
    data: bytes, format: SerializationFormat | None = None
) -> dict[str, Any]:
    """
    Deserialize data using the global serialization manager.

    Args:
        data: Data to deserialize
        format: Serialization format (uses default if None)

    Returns:
        Dict: Deserialized message
    """
    return _serialization_manager.deserialize(data, format)


def set_default_serialization_format(format: SerializationFormat) -> None:
    """
    Set the default serialization format globally.

    Args:
        format: New default format
    """
    _serialization_manager.set_default_format(format)
