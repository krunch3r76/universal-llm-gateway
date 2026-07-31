#!/usr/bin/env python3
"""
Test script for middleware UDP monitoring system.

This script sends test events to the middleware viewer to verify
the UDP communication is working correctly.

Usage:
    python tools/test_monitor.py
    python tools/test_monitor.py --port 9999
    python tools/test_monitor.py --count 5
"""

import argparse
import json
import socket
import time
import uuid
from datetime import datetime
from typing import Any


def create_test_event(event_type: str = "chat_completion") -> dict[str, Any]:
    """Create a test monitoring event"""

    if event_type == "chat_completion":
        return {
            "id": str(uuid.uuid4())[:8],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "type": "chat_completion",
            "original_request": {
                "messages": [
                    {"role": "user", "content": "Hello, can you help me with Python?"}
                ],
                "model": "test-model",
                "temperature": None,
                "max_tokens": None,
            },
            "modified_request": {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a helpful Python programming assistant.",
                    },
                    {"role": "user", "content": "Hello, can you help me with Python?"},
                ],
                "model": "test-model",
                "temperature": 0.7,
                "max_tokens": 2048,
                "top_p": 0.95,
                "stream": False,
            },
            "middleware_actions": [
                "model_from_request: test-model",
                "intelligent_defaults_applied",
                "parameters_modified: temperature=0.7, max_tokens=2048, top_p=0.95",
                "forwarding_non_streaming_request",
            ],
            "processing_time_ms": 12.5,
            "gateway_endpoint": "http://localhost:9998",
        }

    elif event_type == "error":
        return {
            "id": str(uuid.uuid4())[:8],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "type": "middleware_error",
            "error_message": "Model 'invalid-model' not found",
            "original_request": {
                "messages": [{"role": "user", "content": "Test message"}],
                "model": "invalid-model",
            },
            "processing_time_ms": 5.2,
            "middleware_actions": ["ERROR: Model 'invalid-model' not found"],
        }

    elif event_type == "complex":
        return {
            "id": str(uuid.uuid4())[:8],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "type": "chat_completion",
            "original_request": {
                "messages": [
                    {"role": "system", "content": "You are a creative writer."},
                    {"role": "user", "content": "Write a short story about a robot."},
                    {
                        "role": "assistant",
                        "content": "Once upon a time, there was a robot named Zyx...",
                    },
                    {"role": "user", "content": "Make it more dramatic!"},
                ],
                "model": "wizard-vicuna-13b",
                "temperature": 0.8,
                "max_tokens": 1000,
                "top_p": 0.9,
                "stream": True,
            },
            "modified_request": {
                "messages": [
                    {"role": "system", "content": "You are a creative writer."},
                    {"role": "user", "content": "Write a short story about a robot."},
                    {
                        "role": "assistant",
                        "content": "Once upon a time, there was a robot named Zyx...",
                    },
                    {"role": "user", "content": "Make it more dramatic!"},
                ],
                "model": "wizard-vicuna-13b",
                "temperature": 0.8,
                "max_tokens": 1000,
                "top_p": 0.9,
                "top_k": 50,
                "repeat_penalty": 1.1,
                "stream": True,
            },
            "middleware_actions": [
                "model_from_request: wizard-vicuna-13b",
                "intelligent_defaults_applied",
                "parameters_modified: top_k=50, repeat_penalty=1.1",
                "chat_template_applied: wizard-vicuna",
                "personality_applied: creative",
                "forwarding_streaming_request",
            ],
            "processing_time_ms": 23.7,
            "gateway_endpoint": "http://localhost:9998",
        }

    else:
        raise ValueError(f"Unknown event type: {event_type}")


def send_test_event(event_data: dict[str, Any], port: int = 9999):
    """Send a test event via UDP"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        message = json.dumps(event_data, indent=None, separators=(",", ":"))
        sock.sendto(message.encode("utf-8"), ("127.0.0.1", port))
        sock.close()

        print(f"✓ Sent test event {event_data['id']} ({event_data['type']})")
        return True

    except Exception as e:
        print(f"✗ Failed to send test event: {e}")
        return False


def main():
    """Main test function"""
    parser = argparse.ArgumentParser(
        description="Test UDP monitoring system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python tools/test_monitor.py                # Send one test event
    python tools/test_monitor.py --count 5      # Send 5 test events
    python tools/test_monitor.py --port 8888    # Send to custom port
    python tools/test_monitor.py --type error   # Send error event
        """,
    )

    parser.add_argument(
        "--port", type=int, default=9999, help="UDP port to send to (default: 9999)"
    )

    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of test events to send (default: 1)",
    )

    parser.add_argument(
        "--type",
        choices=["chat_completion", "error", "complex", "all"],
        default="chat_completion",
        help="Type of test event to send (default: chat_completion)",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Interval between events in seconds (default: 1.0)",
    )

    args = parser.parse_args()

    print("=" * 50)
    print("Middleware UDP Monitor Test")
    print("=" * 50)
    print(f"Target port: {args.port}")
    print(f"Event count: {args.count}")
    print(f"Event type: {args.type}")
    print(f"Interval: {args.interval}s")
    print("=" * 50)

    # Test UDP connection
    print("Testing UDP connection...")

    success_count = 0
    total_count = 0

    try:
        for i in range(args.count):
            if args.type == "all":
                # Send all types of events
                event_types = ["chat_completion", "error", "complex"]
                for event_type in event_types:
                    event_data = create_test_event(event_type)
                    if send_test_event(event_data, args.port):
                        success_count += 1
                    total_count += 1

                    if i < args.count - 1 or event_type != event_types[-1]:
                        time.sleep(args.interval)
            else:
                # Send specific type of event
                event_data = create_test_event(args.type)
                if send_test_event(event_data, args.port):
                    success_count += 1
                total_count += 1

                if i < args.count - 1:
                    time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")

    print("\n" + "=" * 50)
    print("Test Summary:")
    print(f"  Total events sent: {total_count}")
    print(f"  Successful: {success_count}")
    print(f"  Failed: {total_count - success_count}")
    print("=" * 50)

    if success_count == total_count:
        print("✓ All test events sent successfully!")
        print("\nIf you have the viewer running, you should see the events appear.")
    else:
        print("✗ Some test events failed to send.")
        print("\nMake sure:")
        print("  - The viewer is running on the target port")
        print("  - No firewall is blocking UDP traffic")
        print("  - The port is not already in use")

    return 0 if success_count == total_count else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
