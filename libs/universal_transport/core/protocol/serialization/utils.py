"""
Serialization utility functions.

Provides helper functions for working with serializers.
"""

from .base import Serializer
from .json import JSONSerializer
from .messagepack import MessagePackSerializer
from .protobuf import ProtobufSerializer
from .raw import RawBinarySerializer


def get_serializer_by_name(name: str) -> Serializer:
    """
    Get a serializer instance by name.

    Args:
        name: Serializer name ('json', 'messagepack', 'protobuf', 'raw')

    Returns:
        Serializer instance

    Raises:
        ValueError: If serializer name is not recognized
    """
    name_lower = name.lower()

    if name_lower in ("json", "application/json"):
        return JSONSerializer()
    elif name_lower in ("messagepack", "msgpack", "application/msgpack"):
        return MessagePackSerializer()
    elif name_lower in ("protobuf", "protobuf", "application/x-protobuf"):
        return ProtobufSerializer()
    elif name_lower in ("raw", "binary", "application/octet-stream"):
        return RawBinarySerializer()
    else:
        raise ValueError(
            f"Unknown serializer name: {name}. "
            f"Available: json, messagepack, protobuf, raw"
        )


def list_available_serializers() -> dict[str, str]:
    """
    List all available serializers.

    Returns:
        Dictionary mapping serializer names to descriptions
    """
    serializers = {
        "json": "JSON (UTF-8, human-readable)",
        "raw": "Raw binary (pass-through)",
    }

    # Check for optional dependencies
    try:
        import msgpack

        serializers["messagepack"] = "MessagePack (compact binary)"
    except ImportError:
        pass

    try:
        from google.protobuf import message

        serializers["protobuf"] = "Protocol Buffers (typed binary)"
    except ImportError:
        pass

    return serializers
