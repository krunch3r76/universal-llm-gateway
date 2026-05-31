#!/usr/bin/env python3
"""
Demo: Parent Death Signal Feature

Demonstrates how PR_SET_PDEATHSIG prevents orphaned worker processes.
This demo spawns a worker process and then kills the parent, showing
that the worker automatically dies.

Usage:
    python examples/parent_death_signal_demo.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from process_ipc import ProcessSupervisor, SupervisorConfig, UnixSocketConfig


async def demo_parent_death_signal():
    """
    Demonstrate parent death signal functionality.

    Steps:
    1. Spawn a worker process with ProcessSupervisor
    2. Verify worker is running
    3. Kill parent (this script) with SIGKILL
    4. External observer verifies worker also died
    """
    print("=" * 70)
    print("Parent Death Signal Demo")
    print("=" * 70)
    print()
    print("This demo shows how workers automatically die when parent dies.")
    print()

    # Create configuration
    worker_id = f"demo_worker_{os.getpid()}"
    socket_path = f"/tmp/process_ipc_{worker_id}.sock"

    # Create supervisor with new API
    print(f"Creating ProcessSupervisor (PID: {os.getpid()})")
    config = SupervisorConfig(
        transport=UnixSocketConfig(
            socket_path=socket_path, max_message_size=1024 * 1024
        )
    )
    supervisor = ProcessSupervisor(config)

    # Create a simple worker script
    worker_script = """
import os
import signal
import time
import sys

print(f"Worker started (PID: {os.getpid()})", flush=True)
print(f"Parent PID: {os.getppid()}", flush=True)

# Write PID to a file so demo can read it
with open('/tmp/worker_demo_pid.txt', 'w') as f:
    f.write(str(os.getpid()))

print("Worker is running... waiting for parent to die", flush=True)

# Keep worker alive
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Worker interrupted", flush=True)
"""

    worker_script_path = "/tmp/demo_worker.py"
    with open(worker_script_path, "w") as f:
        f.write(worker_script)

    try:
        # Spawn worker
        print(f"\nSpawning worker: {worker_id}")
        command = [sys.executable, worker_script_path]

        await supervisor.spawn(
            worker_id=worker_id, command=command, startup_timeout=10.0
        )

        worker_info = supervisor.get_worker_info()
        worker_pid = worker_info.pid if worker_info else None

        print("✅ Worker spawned successfully!")
        print(f"   Worker PID: {worker_pid}")
        print(f"   Parent PID: {os.getpid()}")
        print(f"   Socket: {socket_path}")
        print()

        # Verify worker is running
        if supervisor.is_worker_running():
            print("✅ Worker is confirmed running")
        else:
            print("❌ Worker is not running!")
            return

        print()
        print("-" * 70)
        print("DEMO: Parent Death Signal Test")
        print("-" * 70)
        print()
        print("The worker has PR_SET_PDEATHSIG configured.")
        print("When this parent process dies, the worker will automatically die.")
        print()
        print("To test this:")
        print(f"  1. Note the parent PID: {os.getpid()}")
        print(f"  2. Note the worker PID: {worker_pid}")
        print(f"  3. In another terminal, run: kill -9 {os.getpid()}")
        print(f"  4. Verify worker PID {worker_pid} also dies")
        print()
        print("Waiting for you to kill this process...")
        print("(Press Ctrl+C to stop demo gracefully instead)")
        print()

        # Wait indefinitely
        try:
            while True:
                await asyncio.sleep(1)
                if not supervisor.is_worker_running():
                    print("⚠️  Worker died unexpectedly")
                    break
        except KeyboardInterrupt:
            print("\nDemo interrupted by user")
            print("Stopping worker gracefully...")
            await supervisor.stop(force=True)
            print("✅ Worker stopped")

    finally:
        # Cleanup
        await supervisor.shutdown()

        # Remove temp files
        try:
            os.remove(worker_script_path)
        except Exception:
            pass

        try:
            os.remove("/tmp/worker_demo_pid.txt")
        except Exception:
            pass


async def demo_orphaned_process_comparison():
    """
    Demo showing the difference between with and without parent death signal.

    This creates two scenarios:
    1. Without PR_SET_PDEATHSIG (commented out in code)
    2. With PR_SET_PDEATHSIG (default in ProcessSupervisor)
    """
    print("=" * 70)
    print("Orphaned Process Comparison Demo")
    print("=" * 70)
    print()
    print("This demo shows the difference in behavior:")
    print("  • WITHOUT parent death signal: worker becomes orphaned")
    print("  • WITH parent death signal: worker dies automatically")
    print()

    # Note: ProcessSupervisor now ALWAYS enables parent death signal
    # So this demo just confirms the behavior

    print("ProcessSupervisor now ALWAYS enables PR_SET_PDEATHSIG by default.")
    print("This means all worker processes will automatically die when parent dies.")
    print()
    print("Benefits:")
    print("  ✅ No orphaned processes holding GPU memory")
    print("  ✅ No orphaned processes holding network ports")
    print("  ✅ No orphaned processes holding file handles")
    print("  ✅ Clean system state after crashes")
    print("  ✅ No manual cleanup required")
    print()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Parent Death Signal Demo")
    parser.add_argument(
        "--comparison", action="store_true", help="Show comparison demo (info only)"
    )
    args = parser.parse_args()

    if args.comparison:
        asyncio.run(demo_orphaned_process_comparison())
    else:
        asyncio.run(demo_parent_death_signal())


if __name__ == "__main__":
    main()
