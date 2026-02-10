"""
Base serializer interface.

This module provides the abstract base class and exceptions
for all serialization format implementations.
"""

from abc import ABC, abstractmethod
from typing import Any


class SerializationError(Exception):
    """Base exception for serialization-related errors."""

    pass


class SerializeError(SerializationError):
    """Raised when serialization fails."""

    pass


class DeserializeError(SerializationError):
    """Raised when deserialization fails."""

    pass


class Serializer(ABC):
    """
    Abstract base class for all serialization formats.

    Provides a consistent interface for encoding Python objects to bytes
    and decoding bytes back to Python objects. This abstraction allows
    the transport layer to work with different serialization formats
    without being tied to any specific implementation.

    Attributes:
        name: Human-readable name of the serialization format
        content_type: MIME-like content type identifier
    """

    def __init__(self, name: str, content_type: str):
        """Initialize serializer with metadata."""
        self.name = name
        self.content_type = content_type

    @abstractmethod
    def serialize(self, data: Any) -> bytes:
        """
        Serialize data to bytes.

        Args:
            data: Python object to serialize

        Returns:
            Serialized bytes ready for transmission

        Raises:
            SerializeError: If serialization fails
        """
        pass

    @abstractmethod
    def deserialize(self, data: bytes) -> Any:
        """
        Deserialize bytes to Python object.

        Args:
            data: Serialized bytes from transport

        Returns:
            Deserialized Python object

        Raises:
            DeserializeError: If deserialization fails
        """
        pass

    def __str__(self) -> str:
        return f"{self.name} ({self.content_type})"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', content_type='{self.content_type}')"
