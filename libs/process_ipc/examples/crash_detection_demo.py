"""
Demonstration of event-driven process crash detection.

Shows how to:
- Configure crash detection with custom callbacks
- Integrate with event bus for crash notifications
- Handle different types of process exits
- Capture and process crash events
"""

import asyncio
import os
import sys
from typing import Any

try:
    from universal_event_bus import Event
except ImportError:
    # Fallback when universal_event_bus is not available
    Event = dict[str, Any]

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from process_ipc import (
    ProcessHealthConfig,
    ProcessSupervisor,
    SupervisorConfig,
    UnixSocketConfig,
)


class DemoEventBus:
    """
    Simple event bus implementation for demonstration.

    In production, you would integrate with your actual event bus system
    (Redis Pub/Sub, RabbitMQ, Kafka, etc.)
    """

    def __init__(self):
        self.subscribers = []
        self.event_count = 0

    def publish(self, event: Event) -> None:
        """Publish Event instance to all subscribers."""
        self.event_count += 1
        print(f"\n📢 EVENT BUS: Publishing event #{self.event_count}")

        # Handle both Event instances and Dict fallback
        if hasattr(event, "signal"):
            # Event instance
            print(f"   Signal: {event.signal}")
            print(f"   Process ID: {event.payload['process_id']}")
            print(f"   Error: {event.payload['error_message']}")
            print(f"   Exit Code: {event.payload['exit_code']}")
            if event.payload.get("stderr"):
                print(f"   Stderr: {event.payload['stderr'][:100]}...")
        else:
            # Dict fallback
            print(f"   Signal: {event['signal']}")
            print(f"   Process ID: {event['payload']['process_id']}")
            print(f"   Error: {event['payload']['error_message']}")
            print(f"   Exit Code: {event['payload']['exit_code']}")
            if event["payload"].get("stderr"):
                print(f"   Stderr: {event['payload']['stderr'][:100]}...")

        # Notify subscribers
        for callback in self.subscribers:
            try:
                callback(event)
            except Exception as e:
                print(f"   ⚠️  Subscriber error: {e}")

    def subscribe(self, callback):
        """Subscribe to crash events."""
        self.subscribers.append(callback)


def on_process_crash(process_id: str, exit_code: int, error_message: str):
    """
    Callback for process crashes.

    This is called immediately when a crash is detected, before event publishing.
    Use this for immediate actions like alerts, logging, or notifications.
    """
    print(f"\n💥 CRASH DETECTED: {process_id}")
    print(f"   Exit Code: {exit_code}")
    print(f"   Error: {error_message}")

    # Example immediate actions:
    # - Send alert to monitoring system
    # - Log to crash database
    # - Trigger incident response

    if exit_code < 0:
        signal_name = f"Signal {-exit_code}"
        print(f"   🔥 Process killed by {signal_name}")
    else:
        print(f"   💀 Process exited with error code {exit_code}")


def on_process_exit(process_id: str, exit_code: int):
    """
    Callback for any process exit (clean or crash).

    Use this to track all process terminations.
    """
    if exit_code == 0:
        print(f"\n✅ CLEAN EXIT: {process_id} exited normally")
    else:
        print(f"\n⚠️  EXIT: {process_id} exited with code {exit_code}")


def crash_event_handler(event: Event):
    """
    Event bus subscriber for crash events.

    This handles events after they're published to the event bus.
    Use this for downstream processing, analytics, etc.
    """
    # Handle both Event instances and Dict fallback
    if hasattr(event, "payload"):
        payload = event.payload
    else:
        payload = event["payload"]

    print("\n🎯 CRASH EVENT HANDLER:")
    print(f"   Process: {payload['process_id']}")
    print(f"   PID: {payload['pid']}")
    print(f"   Socket: {payload['socket_path']}")
    print(f"   Signal Termination: {payload['is_signal_termination']}")
    if payload.get("signal_name"):
        print(f"   Signal: {payload['signal_name']}")

    # Example event processing:
    # - Update process status in database
    # - Trigger analytics pipeline
    # - Send to external monitoring systems


async def demo_basic_crash_detection():
    """Demonstrate basic crash detection setup."""
    print("\n=== BASIC CRASH DETECTION DEMO ===")

    # Create event bus
    event_bus = DemoEventBus()
    event_bus.subscribe_async(crash_event_handler)

    # Configure crash detection
    health_config = ProcessHealthConfig(
        health_check_interval=2.0,  # Check every 2 seconds
        detect_crashes=True,
        publish_crash_events=True,
        event_bus=event_bus,
        on_process_crash=on_process_crash,
        on_process_exit=on_process_exit,
        capture_stderr_on_crash=True,
        crash_callback_timeout=5.0,
    )

    # Create supervisor
    config = SupervisorConfig(
        transport=UnixSocketConfig(socket_path="/tmp/crash_demo.sock"),
        health=health_config,
    )

    supervisor = ProcessSupervisor(config)

    print("✅ Crash detection configured with:")
    print(f"   - Health check interval: {health_config.health_check_interval}s")
    print(f"   - Event bus: {type(event_bus).__name__}")
    print("   - Crash callbacks: Yes")
    print(f"   - Stderr capture: {health_config.capture_stderr_on_crash}")

    # Start supervisor
    await supervisor.start()

    # Simulate spawning a worker that will crash
    print("\n📋 Spawning worker process (will crash in 5 seconds)...")
    await supervisor.spawn(
        "crash_demo_worker",
        [
            "python",
            "-c",
            "import time; time.sleep(5); exit(1)",
        ],  # Crashes with exit code 1
    )

    # Monitor for crashes
    print("🔍 Monitoring for crashes...")
    await asyncio.sleep(8)  # Wait for crash to occur and be detected

    print("\n🛑 Shutting down...")
    await supervisor.shutdown()


async def demo_custom_crash_codes():
    """Demonstrate custom crash exit codes."""
    print("\n=== CUSTOM CRASH CODES DEMO ===")

    event_bus = DemoEventBus()

    # Configure custom crash detection
    health_config = ProcessHealthConfig(
        health_check_interval=1.0,
        detect_crashes=True,
        crash_exit_codes=[1, 2, 139, 134],  # Only these are crashes
        expected_exit_codes=[0, 3],  # 0 = success, 3 = graceful shutdown
        event_bus=event_bus,
        publish_crash_events=True,
    )

    print("✅ Custom crash codes configured:")
    print(f"   - Crash codes: {health_config.crash_exit_codes}")
    print(f"   - Expected codes: {health_config.expected_exit_codes}")

    # Test different exit codes
    test_codes = [0, 1, 3, 5, -11]
    for code in test_codes:
        is_crash = health_config.is_crash_exit_code(code)
        status = "🔥 CRASH" if is_crash else "✅ OK"
        print(f"   - Exit code {code}: {status}")


async def demo_async_callbacks():
    """Demonstrate async crash callbacks."""
    print("\n=== ASYNC CALLBACKS DEMO ===")

    async def async_crash_handler(process_id: str, exit_code: int, error_msg: str):
        """Async crash handler with database logging simulation."""
        print(f"\n🔄 ASYNC HANDLER: Processing crash for {process_id}")

        # Simulate async database operations
        await asyncio.sleep(1)
        print("   💾 Logged crash to database")

        # Simulate async alert system
        await asyncio.sleep(0.5)
        print("   📱 Sent alert notification")

        print("   ✅ Async processing complete")

    health_config = ProcessHealthConfig(
        detect_crashes=True,
        on_process_crash=async_crash_handler,
        crash_callback_timeout=10.0,  # Allow more time for async operations
    )

    print("✅ Async callbacks configured with 10s timeout")

    # Create monitor for testing
    from core.simple_health_monitor import SimpleHealthMonitor

    monitor = SimpleHealthMonitor(health_config)

    # Mock supervisor
    class MockSupervisor:
        _log_file = None
        _transport_config = type("obj", (object,), {"socket_path": "/tmp/test.sock"})

    monitor._supervisor = MockSupervisor()

    # Simulate crash
    print("🚨 Simulating crash with async callback...")
    await monitor._handle_process_crash("async_worker", 1, 12345)
    print("✅ Async callback completed")


async def main():
    """Run all demonstrations."""
    print("🚀 Process IPC Crash Detection Demo")
    print("=" * 50)

    try:
        await demo_basic_crash_detection()
        await demo_custom_crash_codes()
        await demo_async_callbacks()

        print("\n🎉 All demos completed successfully!")
        print("\nKey Features Demonstrated:")
        print("✅ Automatic crash detection")
        print("✅ Event bus integration")
        print("✅ Custom crash callbacks")
        print("✅ Configurable exit codes")
        print("✅ Async callback support")
        print("✅ Error handling and timeouts")

    except KeyboardInterrupt:
        print("\n⏹️  Demo interrupted by user")
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
