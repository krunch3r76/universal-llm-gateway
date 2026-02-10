"""
Debug utilities for inspecting length-prefixed protocol streams.

This module provides tools to decode, inspect, and analyze length-prefixed
protocol data to replace the human-readable debugging capabilities that
were available with JSONL framing.

Key features:
- Decode length-prefixed streams from files or sockets
- Real-time monitoring of socket communication
- Pretty-print messages in various formats
- Support for all serialization formats
- Capture and replay protocol sessions
"""

import asyncio
import json
from universal_logging import get_logger
import struct
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from ..core.protocol.serializers import (
    JSONSerializer,
    RawBinarySerializer,
    Serializer,
    get_serializer_by_name,
)

logger = get_logger(__name__)


class ProtocolInspectorError(Exception):
    """Base exception for protocol inspection errors."""

    pass


class ProtocolDecodeError(ProtocolInspectorError):
    """Raised when protocol decoding fails."""

    pass


class MessageInfo:
    """Information about a decoded message."""

    def __init__(
        self,
        sequence: int,
        timestamp: float,
        payload_length: int,
        payload_bytes: bytes,
        decoded_message: Any,
        serializer_name: str,
    ):
        """Initialize message info."""
        self.sequence = sequence
        self.timestamp = timestamp
        self.payload_length = payload_length
        self.payload_bytes = payload_bytes
        self.decoded_message = decoded_message
        self.serializer_name = serializer_name
        self.frame_length = 4 + payload_length  # Length prefix + payload

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "datetime": datetime.fromtimestamp(self.timestamp).isoformat(),
            "payload_length": self.payload_length,
            "frame_length": self.frame_length,
            "serializer": self.serializer_name,
            "message": self.decoded_message,
        }


class ProtocolInspector:
    """
    Inspector for length-prefixed protocol streams.

    Provides tools to decode and analyze protocol data with support
    for different serialization formats.
    """

    def __init__(self, serializer: Serializer | None = None):
        """
        Initialize protocol inspector.

        Args:
            serializer: Serializer to use for decoding (default: try JSON first)
        """
        self.serializer = serializer or JSONSerializer()
        self.message_count = 0

    def decode_stream_from_file(self, file_path: Path) -> Iterator[MessageInfo]:
        """
        Decode length-prefixed messages from a file.

        Args:
            file_path: Path to file containing protocol data

        Yields:
            MessageInfo objects for each decoded message

        Raises:
            ProtocolDecodeError: If decoding fails
        """
        try:
            with open(file_path, "rb") as f:
                sequence = 0

                while True:
                    # Read length prefix (4 bytes)
                    length_bytes = f.read(4)
                    if len(length_bytes) < 4:
                        if len(length_bytes) == 0:
                            break  # End of file
                        else:
                            raise ProtocolDecodeError(
                                f"Incomplete length prefix: {len(length_bytes)} bytes"
                            )

                    # Decode payload length
                    payload_length = struct.unpack("!I", length_bytes)[0]

                    # Read payload
                    payload_bytes = f.read(payload_length)
                    if len(payload_bytes) != payload_length:
                        raise ProtocolDecodeError(
                            f"Incomplete payload: expected {payload_length}, "
                            f"got {len(payload_bytes)} bytes"
                        )

                    # Try to decode payload
                    try:
                        decoded_message = self.serializer.deserialize(payload_bytes)
                    except Exception as e:
                        # Try with raw binary if configured serializer fails
                        if not isinstance(self.serializer, RawBinarySerializer):
                            try:
                                raw_serializer = RawBinarySerializer()
                                decoded_message = raw_serializer.deserialize(
                                    payload_bytes
                                )
                                serializer_name = "raw (fallback)"
                            except Exception:
                                decoded_message = f"<decode error: {e}>"
                                serializer_name = f"{self.serializer.name} (failed)"
                        else:
                            decoded_message = f"<decode error: {e}>"
                            serializer_name = f"{self.serializer.name} (failed)"
                    else:
                        serializer_name = self.serializer.name

                    # Create message info
                    message_info = MessageInfo(
                        sequence=sequence,
                        timestamp=time.time(),  # File doesn't have timestamp, use current
                        payload_length=payload_length,
                        payload_bytes=payload_bytes,
                        decoded_message=decoded_message,
                        serializer_name=serializer_name,
                    )

                    yield message_info
                    sequence += 1

        except Exception as e:
            raise ProtocolDecodeError(f"Failed to decode stream from {file_path}: {e}")

    def decode_stream_from_bytes(self, data: bytes) -> Iterator[MessageInfo]:
        """
        Decode length-prefixed messages from raw bytes.

        Args:
            data: Raw protocol data

        Yields:
            MessageInfo objects for each decoded message
        """
        offset = 0
        sequence = 0

        while offset < len(data):
            # Check if we have enough bytes for length prefix
            if offset + 4 > len(data):
                logger.warning(f"Incomplete length prefix at offset {offset}")
                break

            # Read length prefix
            length_bytes = data[offset : offset + 4]
            payload_length = struct.unpack("!I", length_bytes)[0]
            offset += 4

            # Check if we have enough bytes for payload
            if offset + payload_length > len(data):
                logger.warning(
                    f"Incomplete payload at offset {offset}: "
                    f"expected {payload_length}, have {len(data) - offset}"
                )
                break

            # Read payload
            payload_bytes = data[offset : offset + payload_length]
            offset += payload_length

            # Try to decode payload
            try:
                decoded_message = self.serializer.deserialize(payload_bytes)
                serializer_name = self.serializer.name
            except Exception as e:
                decoded_message = f"<decode error: {e}>"
                serializer_name = f"{self.serializer.name} (failed)"

            # Create message info
            message_info = MessageInfo(
                sequence=sequence,
                timestamp=time.time(),
                payload_length=payload_length,
                payload_bytes=payload_bytes,
                decoded_message=decoded_message,
                serializer_name=serializer_name,
            )

            yield message_info
            sequence += 1

    def pretty_print_message(
        self,
        message_info: MessageInfo,
        output: TextIO,
        show_hex: bool = False,
        show_raw: bool = False,
        indent: int = 2,
    ) -> None:
        """
        Pretty-print a decoded message.

        Args:
            message_info: Message information to print
            output: Output stream (e.g., sys.stdout)
            show_hex: Whether to show hex dump of payload
            show_raw: Whether to show raw bytes
            indent: Indentation level for JSON pretty-printing
        """
        print(f"Message {message_info.sequence}:", file=output)
        print(
            f"  Timestamp: {message_info.timestamp} ({datetime.fromtimestamp(message_info.timestamp)})",
            file=output,
        )
        print(f"  Frame Length: {message_info.frame_length} bytes", file=output)
        print(f"  Payload Length: {message_info.payload_length} bytes", file=output)
        print(f"  Serializer: {message_info.serializer_name}", file=output)

        # Pretty-print message content
        print("  Message:", file=output)
        if isinstance(message_info.decoded_message, (dict, list)):
            # Pretty-print JSON-like structures
            json_str = json.dumps(
                message_info.decoded_message, indent=indent, ensure_ascii=False
            )
            for line in json_str.split("\n"):
                print(f"    {line}", file=output)
        elif isinstance(message_info.decoded_message, str):
            # String content
            if len(message_info.decoded_message) < 200:
                print(f"    {message_info.decoded_message!r}", file=output)
            else:
                print(
                    f"    {message_info.decoded_message[:200]!r}... (truncated)",
                    file=output,
                )
        else:
            # Other types
            print(f"    {message_info.decoded_message}", file=output)

        # Show hex dump if requested
        if show_hex:
            print("  Hex Dump:", file=output)
            for i in range(0, len(message_info.payload_bytes), 16):
                chunk = message_info.payload_bytes[i : i + 16]
                hex_str = " ".join(f"{b:02x}" for b in chunk)
                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                print(f"    {i:08x}: {hex_str:<48} |{ascii_str}|", file=output)

        # Show raw bytes if requested
        if show_raw:
            print(f"  Raw Bytes: {message_info.payload_bytes!r}", file=output)

        print(file=output)  # Blank line separator


class SocketMonitor:
    """
    Real-time monitor for Unix socket communication.

    Connects to a Unix socket and displays decoded messages in real-time.
    """

    def __init__(self, socket_path: str, serializer: Serializer | None = None):
        """
        Initialize socket monitor.

        Args:
            socket_path: Path to Unix socket to monitor
            serializer: Serializer for decoding messages
        """
        self.socket_path = socket_path
        self.inspector = ProtocolInspector(serializer)
        self.running = False

    async def start_monitoring(
        self, output: TextIO, show_hex: bool = False, show_raw: bool = False
    ) -> None:
        """
        Start monitoring socket in real-time.

        Args:
            output: Output stream for messages
            show_hex: Whether to show hex dumps
            show_raw: Whether to show raw bytes
        """
        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
            self.running = True

            print(
                f"Connected to {self.socket_path}, monitoring messages...", file=output
            )
            print("Press Ctrl+C to stop", file=output)
            print("=" * 60, file=output)

            sequence = 0

            while self.running:
                try:
                    # Read length prefix
                    length_bytes = await asyncio.wait_for(
                        reader.readexactly(4), timeout=1.0
                    )
                    payload_length = struct.unpack("!I", length_bytes)[0]

                    # Read payload
                    payload_bytes = await reader.readexactly(payload_length)

                    # Create message info
                    try:
                        decoded_message = self.inspector.serializer.deserialize(
                            payload_bytes
                        )
                        serializer_name = self.inspector.serializer.name
                    except Exception as e:
                        decoded_message = f"<decode error: {e}>"
                        serializer_name = f"{self.inspector.serializer.name} (failed)"

                    message_info = MessageInfo(
                        sequence=sequence,
                        timestamp=time.time(),
                        payload_length=payload_length,
                        payload_bytes=payload_bytes,
                        decoded_message=decoded_message,
                        serializer_name=serializer_name,
                    )

                    # Pretty-print message
                    self.inspector.pretty_print_message(
                        message_info, output, show_hex, show_raw
                    )
                    output.flush()
                    sequence += 1

                except TimeoutError:
                    continue  # Check if still running
                except asyncio.IncompleteReadError:
                    print("Connection closed by peer", file=output)
                    break

        except KeyboardInterrupt:
            print("\nMonitoring stopped by user", file=output)
        except Exception as e:
            print(f"Error monitoring socket: {e}", file=output)
        finally:
            self.running = False
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


def inspect_file(
    file_path: str,
    serializer_name: str = "json",
    show_hex: bool = False,
    show_raw: bool = False,
    max_messages: int | None = None,
) -> None:
    """
    Inspect protocol data from a file.

    Args:
        file_path: Path to file containing protocol data
        serializer_name: Name of serializer to use
        show_hex: Whether to show hex dumps
        show_raw: Whether to show raw bytes
        max_messages: Maximum number of messages to display
    """
    import sys

    try:
        serializer = get_serializer_by_name(serializer_name)
        inspector = ProtocolInspector(serializer)

        message_count = 0
        for message_info in inspector.decode_stream_from_file(Path(file_path)):
            inspector.pretty_print_message(message_info, sys.stdout, show_hex, show_raw)
            message_count += 1

            if max_messages and message_count >= max_messages:
                print(f"... (truncated after {max_messages} messages)")
                break

        print(f"Total messages: {message_count}")

    except Exception as e:
        print(f"Error inspecting file: {e}", file=sys.stderr)


async def monitor_socket(
    socket_path: str,
    serializer_name: str = "json",
    show_hex: bool = False,
    show_raw: bool = False,
) -> None:
    """
    Monitor Unix socket in real-time.

    Args:
        socket_path: Path to Unix socket
        serializer_name: Name of serializer to use
        show_hex: Whether to show hex dumps
        show_raw: Whether to show raw bytes
    """
    import sys

    try:
        serializer = get_serializer_by_name(serializer_name)
        monitor = SocketMonitor(socket_path, serializer)

        await monitor.start_monitoring(sys.stdout, show_hex, show_raw)

    except Exception as e:
        print(f"Error monitoring socket: {e}", file=sys.stderr)


def capture_to_file(
    socket_path: str,
    output_file: str,
    duration: float | None = None,
    max_messages: int | None = None,
) -> None:
    """
    Capture protocol data from socket to file.

    Args:
        socket_path: Path to Unix socket
        output_file: Path to output file for captured data
        duration: Maximum capture duration in seconds
        max_messages: Maximum number of messages to capture
    """

    async def capture():
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path)

            print(f"Connected to {socket_path}, capturing to {output_file}...")
            if duration:
                print(f"Duration: {duration}s")
            if max_messages:
                print(f"Max messages: {max_messages}")
            print("Press Ctrl+C to stop early")

            message_count = 0
            start_time = time.time()

            with open(output_file, "wb") as f:
                while True:
                    # Check time limit
                    if duration and (time.time() - start_time) > duration:
                        print(f"Duration limit reached ({duration}s)")
                        break

                    # Check message limit
                    if max_messages and message_count >= max_messages:
                        print(f"Message limit reached ({max_messages})")
                        break

                    try:
                        # Read length prefix
                        length_bytes = await asyncio.wait_for(
                            reader.readexactly(4), timeout=1.0
                        )
                        payload_length = struct.unpack("!I", length_bytes)[0]

                        # Read payload
                        payload_bytes = await reader.readexactly(payload_length)

                        # Write complete frame to file
                        f.write(length_bytes + payload_bytes)
                        f.flush()

                        message_count += 1
                        if message_count % 100 == 0:
                            print(f"Captured {message_count} messages...")

                    except TimeoutError:
                        continue
                    except asyncio.IncompleteReadError:
                        print("Connection closed by peer")
                        break

            print(f"Captured {message_count} messages to {output_file}")

        except KeyboardInterrupt:
            print(f"\nCapture stopped by user. Captured {message_count} messages.")
        except Exception as e:
            print(f"Error capturing data: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    asyncio.run(capture())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Protocol debugging utilities")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Inspect file command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect protocol file")
    inspect_parser.add_argument("file", help="Protocol file to inspect")
    inspect_parser.add_argument("--serializer", default="json", help="Serializer name")
    inspect_parser.add_argument("--hex", action="store_true", help="Show hex dumps")
    inspect_parser.add_argument("--raw", action="store_true", help="Show raw bytes")
    inspect_parser.add_argument("--max", type=int, help="Maximum messages to show")

    # Monitor socket command
    monitor_parser = subparsers.add_parser("monitor", help="Monitor Unix socket")
    monitor_parser.add_argument("socket", help="Unix socket path")
    monitor_parser.add_argument("--serializer", default="json", help="Serializer name")
    monitor_parser.add_argument("--hex", action="store_true", help="Show hex dumps")
    monitor_parser.add_argument("--raw", action="store_true", help="Show raw bytes")

    # Capture command
    capture_parser = subparsers.add_parser(
        "capture", help="Capture socket data to file"
    )
    capture_parser.add_argument("socket", help="Unix socket path")
    capture_parser.add_argument("output", help="Output file path")
    capture_parser.add_argument(
        "--duration", type=float, help="Capture duration in seconds"
    )
    capture_parser.add_argument("--max", type=int, help="Maximum messages to capture")

    args = parser.parse_args()

    if args.command == "inspect":
        inspect_file(args.file, args.serializer, args.hex, args.raw, args.max)
    elif args.command == "monitor":
        asyncio.run(monitor_socket(args.socket, args.serializer, args.hex, args.raw))
    elif args.command == "capture":
        capture_to_file(args.socket, args.output, args.duration, args.max)
    else:
        parser.print_help()
