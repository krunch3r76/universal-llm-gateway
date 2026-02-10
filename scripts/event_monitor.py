#!/usr/bin/env python3
"""
Real-time event monitor for Universal Stargate.

Connects to debug event socket and displays events as they arrive.
Uses JSONL framing (JSON + newline) - compatible with MinimalEventDebugBroadcaster.

Usage:
    python scripts/event_monitor.py /tmp/universal-sockets/events.sock
    python scripts/event_monitor.py /tmp/events.sock --filter model.
    python scripts/event_monitor.py /tmp/events.sock --json
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


async def monitor_events(
    socket_path: str,
    signal_filter: str | None = None,
    json_output: bool = False,
) -> None:
    """Connect to event socket and stream events."""
    path = Path(socket_path)
    if not path.exists():
        print(f"Error: Socket not found: {socket_path}", file=sys.stderr)
        print("Hint: Is Stargate running with DEBUG_EVENT_SOCKET set?", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to {socket_path}...", file=sys.stderr)

    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
        print("Connected. Waiting for events... (Ctrl+C to stop)", file=sys.stderr)
        print("", file=sys.stderr)

        while True:
            line = await reader.readline()
            if not line:
                print("Connection closed by server", file=sys.stderr)
                break

            try:
                event: dict[str, Any] = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as e:
                print(f"Invalid JSON: {e}", file=sys.stderr)
                continue

            # Apply filter
            signal = event.get("signal", "")
            if signal_filter and signal_filter not in signal:
                continue

            # Output
            if json_output:
                print(json.dumps(event))
            else:
                _print_event(event)

    except ConnectionRefusedError:
        print(f"Connection refused: {socket_path}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()


def _print_event(event: dict[str, Any]) -> None:
    """Pretty-print an event."""
    signal = event.get("signal", "unknown")
    event_id = event.get("id", "?")
    timestamp = event.get("timestamp", "")
    payload = event.get("payload", {})

    # Format timestamp if ISO format
    if timestamp and "T" in timestamp:
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            timestamp = dt.strftime("%H:%M:%S.%f")[:-3]
        except ValueError:
            pass

    # Truncate large payloads
    payload_str = json.dumps(payload, separators=(",", ":"))
    if len(payload_str) > 120:
        payload_str = payload_str[:117] + "..."

    print(f"[{timestamp}] #{event_id} {signal}")
    if payload:
        print(f"    {payload_str}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor Universal Stargate events in real-time",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /tmp/events.sock              # Monitor all events
  %(prog)s /tmp/events.sock --filter model.  # Only model.* events
  %(prog)s /tmp/events.sock --json       # Output raw JSON (pipe to jq)
        """,
    )
    parser.add_argument("socket", help="Path to debug event socket")
    parser.add_argument(
        "--filter", "-f",
        help="Filter events by signal substring (e.g., 'model.' or 'request.')",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output raw JSON (one event per line)",
    )

    args = parser.parse_args()
    asyncio.run(monitor_events(args.socket, args.filter, args.json))


if __name__ == "__main__":
    main()
