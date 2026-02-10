"""
Message pump interfaces.

This module defines interfaces for message reading, writing, and pump functionality.
"""

from abc import ABC, abstractmethod
from typing import Any


class MessageReader(ABC):
    """
    Interface for reading messages from a transport.
    """

    @abstractmethod
    async def read_message(self) -> dict[str, Any]:
        """
        Read a message from the transport.

        Returns:
            Dict[str, Any]: Received message
        """
        pass


class MessageWriter(ABC):
    """
    Interface for writing messages to a transport.
    """

    @abstractmethod
    async def write_message(self, message: dict[str, Any]) -> None:
        """
        Write a message to the transport.

        Args:
            message: Message to send
        """
        pass


class MessagePumpInterface(MessageReader, MessageWriter):
    """
    Combined interface for message reading and writing.

    Provides both read and write capabilities for bidirectional communication.
    """

    pass
