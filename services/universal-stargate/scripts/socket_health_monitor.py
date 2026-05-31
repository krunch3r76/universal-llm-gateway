#!/usr/bin/env python3
"""
Socket Health Monitor

Monitors Unix socket health and handles proxy restarts gracefully.
This script can be used to test socket connectivity and restart resilience.
"""

import os
import socket
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def test_socket_connectivity(socket_path: str, duration: int = 60):
    """Test socket connectivity over time to detect proxy restarts"""
    print(f"🔍 Testing socket connectivity: {socket_path}")
    print(f"⏱️  Duration: {duration} seconds")
    print("=" * 60)

    start_time = time.time()
    connection_count = 0
    failure_count = 0

    while time.time() - start_time < duration:
        try:
            # Test connection
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(socket_path)
            sock.close()

            connection_count += 1
            elapsed = time.time() - start_time
            print(f"✅ Connection #{connection_count} successful ({elapsed:.1f}s)")

        except Exception as e:
            failure_count += 1
            elapsed = time.time() - start_time
            print(f"❌ Connection #{failure_count} failed ({elapsed:.1f}s): {e}")

            # Check if socket file exists
            if not os.path.exists(socket_path):
                print("⚠️  Socket file missing - proxy likely restarted")
            else:
                print("⚠️  Socket file exists but not connectable")

        time.sleep(1.0)

    print("=" * 60)
    print("📊 Results:")
    print(f"   Successful connections: {connection_count}")
    print(f"   Failed connections: {failure_count}")
    print(
        f"   Success rate: {connection_count / (connection_count + failure_count) * 100:.1f}%"
    )


def monitor_socket_file(socket_path: str, duration: int = 60):
    """Monitor socket file existence and properties"""
    print(f"📁 Monitoring socket file: {socket_path}")
    print(f"⏱️  Duration: {duration} seconds")
    print("=" * 60)

    start_time = time.time()
    check_count = 0

    while time.time() - start_time < duration:
        check_count += 1
        elapsed = time.time() - start_time

        if os.path.exists(socket_path):
            try:
                stat = os.stat(socket_path)
                print(
                    f"✅ Check #{check_count} ({elapsed:.1f}s): File exists, mode={oct(stat.st_mode)}, size={stat.st_size}"
                )
            except Exception as e:
                print(
                    f"⚠️  Check #{check_count} ({elapsed:.1f}s): File exists but stat failed: {e}"
                )
        else:
            print(f"❌ Check #{check_count} ({elapsed:.1f}s): File missing")

        time.sleep(2.0)

    print("=" * 60)
    print(f"📊 Completed {check_count} checks")


def test_robust_gui_connection():
    """Test the robust GUI connection with restart simulation"""
    print("🧪 Testing robust GUI connection...")

    try:
        from gui.model.network_receiver import NetworkReceiver

        def dummy_callback(data):
            print(f"📨 Received event: {data.get('type', 'unknown')}")

        receiver = NetworkReceiver(
            callback=dummy_callback,
            config={
                "transport": "unix",
                "unix_socket_path": "/tmp/stargate_enhanced.sock",
                "use_universal_transport": True,
                "auto_reconnect": True,
                "reconnect_attempts": 5,
                "reconnect_delay": 1.0,
            },
        )

        print("🚀 Starting robust receiver...")
        receiver.start()

        print("⏳ Running for 30 seconds to test resilience...")
        time.sleep(30)

        print("🛑 Stopping receiver...")
        receiver.stop()

        print("✅ Test completed successfully")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Socket Health Monitor")
    parser.add_argument(
        "--test-connectivity", action="store_true", help="Test socket connectivity"
    )
    parser.add_argument(
        "--monitor-file", action="store_true", help="Monitor socket file"
    )
    parser.add_argument(
        "--test-gui", action="store_true", help="Test robust GUI connection"
    )
    parser.add_argument(
        "--duration", type=int, default=60, help="Test duration in seconds"
    )
    parser.add_argument(
        "--socket", default="/tmp/stargate_enhanced.sock", help="Socket path to test"
    )

    args = parser.parse_args()

    if args.test_connectivity:
        test_socket_connectivity(args.socket, args.duration)
    elif args.monitor_file:
        monitor_socket_file(args.socket, args.duration)
    elif args.test_gui:
        test_robust_gui_connection()
    else:
        print(
            "Please specify a test mode: --test-connectivity, --monitor-file, or"
            "--test-gui"
        )


if __name__ == "__main__":
    main()
