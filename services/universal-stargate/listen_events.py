#!/usr/bin/env python3
"""
Simple UDP listener to capture monitoring events.
"""

import json
import socket
from datetime import datetime


def listen_for_events():
    """Listen for UDP monitoring events"""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 9999))

    print("🎧 Listening for UDP monitoring events on port 9999...")
    print("Press Ctrl+C to stop")

    event_count = 0

    try:
        while True:
            data, addr = sock.recvfrom(65536)
            event_count += 1

            try:
                event = json.loads(data.decode("utf-8"))

                print(
                    f"\n📡 Event #{event_count} received at"
                    f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}"
                )
                print(f"   ID: {event.get('id', 'N/A')}")
                print(f"   Type: {event.get('type', 'N/A')}")
                print(f"   Timestamp: {event.get('timestamp', 'N/A')}")
                print(
                    f"   Processing Time: {event.get('processing_time_ms', 'N/A')} ms"
                )

                # Show request info
                if "original_request" in event:
                    orig = event["original_request"]
                    print(f"   Original Request: {len(orig)} fields")
                    if "messages" in orig:
                        print(f"     Messages: {len(orig['messages'])} messages")
                    if "model" in orig:
                        print(f"     Model: {orig['model']}")

                if "modified_request" in event:
                    mod = event["modified_request"]
                    print(f"   Modified Request: {len(mod)} fields")
                    if "messages" in mod:
                        print(f"     Messages: {len(mod['messages'])} messages")
                    if "model" in mod:
                        print(f"     Model: {mod['model']}")

                # Show actions
                if "stargate_actions" in event:
                    actions = event["stargate_actions"]
                    print(f"   Actions: {len(actions)} actions")
                    for action in actions[:3]:  # Show first 3 actions
                        print(f"     - {action}")
                    if len(actions) > 3:
                        print(f"     ... and {len(actions) - 3} more")

                # Show response info
                if "response" in event:
                    response = event["response"]
                    if response is None:
                        print("   Response: ⏳ Pre-processing (no response yet)")
                    else:
                        print("   Response: ✅ Received")
                        if isinstance(response, dict):
                            print(f"     Type: {response.get('type', 'unknown')}")

                print("-" * 60)

            except json.JSONDecodeError as e:
                print(f"❌ Failed to parse JSON: {e}")
                print(f"Raw data: {data[:200]}...")
            except Exception as e:
                print(f"❌ Error processing event: {e}")

    except KeyboardInterrupt:
        print(f"\n🛑 Stopped listening. Received {event_count} events.")
    finally:
        sock.close()


if __name__ == "__main__":
    listen_for_events()
