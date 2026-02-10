"""
Serialization abstraction layer for universal_transport.

This module provides pluggable serialization support for different data formats,
allowing the transport layer to work with JSON, MessagePack, Protocol Buffers,
and raw binary data through a consistent interface.

Design principles:
- Abstract serializer interface for pluggability
- Support for process_ipc migration (JSON, Protobuf)
- Efficient binary formats (MessagePack)
- Raw binary pass-through for arbitrary data

This module is a backward-compatibility facade that imports from
the modularized serialization/ subdirectory.
"""

# Import from modularized implementation for backward compatibility
from .serialization import (
    DeserializeError,
    JSONSerializer,
    MessagePackSerializer,
    ProtobufSerializer,
    RawBinarySerializer,
    SerializationError,
    SerializeError,
    Serializer,
    get_serializer_by_name,
    list_available_serializers,
)

# Re-export for backward compatibility
__all__ = [
    "SerializationError",
    "SerializeError",
    "DeserializeError",
    "Serializer",
    "JSONSerializer",
    "MessagePackSerializer",
    "RawBinarySerializer",
    "ProtobufSerializer",
    "get_serializer_by_name",
    "list_available_serializers",
]
