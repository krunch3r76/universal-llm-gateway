#!/usr/bin/env python
"""
Simple Worker Demo - Demonstrates v2.1.1 fix

This example shows basic worker-manager communication working correctly
after the MessagePump fix.
"""

import asyncio
import os
import sys

# Add parent to path for running directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from process_ipc import (
    ProcessSupervisor,
    SupervisorConfig,
    WorkerProcess,
)
from process_ipc.core import signals


class SimpleWorker(WorkerProcess):
    """A simple worker that processes commands."""

    def __init__(self, worker_id: str, socket_path: str):
        super().__init__(worker_id, socket_path)
        self.command_count = 0
        print(f"[Worker {worker_id}] Initialized", flush=True)

    async def process_command(self, command):
        """Process incoming command."""
        self.command_count += 1

        print(f"\n[Worker] 📨 Received command #{self.command_count}", flush=True)
        print(f"[Worker] Command: {command}", flush=True)

        command_type = command.get("command_type")

        if command_type == "echo":
            result = {
                "success": True,
                "message": f"Echo: {command.get('message', '')}",
                "command_number": self.command_count,
            }
        elif command_type == "add":
            a = command.get("a", 0)
            b = command.get("b", 0)
            result = {
                "success": True,
                "result": a + b,
                "message": f"{a} + {b} = {a + b}",
            }
        elif command_type == "status":
            result = {
                "success": True,
                "commands_processed": self.command_count,
                "status": "healthy",
            }
        else:
            result = {
                "success": False,
                "error": f"Unknown command type: {command_type}",
            }

        print("[Worker] ✅ Processing complete", flush=True)
        return result


async def run_worker(worker_id: str, socket_path: str):
    """Run the worker process."""
    worker = SimpleWorker(worker_id, socket_path)
    await worker.initialize(socket_path)
    print(f"[Worker] Ready and listening on {socket_path}", flush=True)
    await worker.run()


async def run_manager():
    """Run the manager process."""
    print("\n" + "=" * 80)
    print("Simple Worker Demo - v2.1.1")
    print("=" * 80 + "\n")

    socket_path = "/tmp/simple_worker_demo.sock"
    worker_id = "simple-worker"

    # Create supervisor
    print("Step 1: Creating ProcessSupervisor...", flush=True)
    config = SupervisorConfig.from_socket_path(socket_path)
    supervisor = ProcessSupervisor(config)
    print("✓ Supervisor created\n", flush=True)

    # Spawn worker
    print("Step 2: Spawning worker subprocess...", flush=True)
    worker_script = __file__  # This script can run as both manager and worker
    success = await supervisor.spawn(
        worker_id=worker_id,
        command=[sys.executable, worker_script, "--worker", worker_id, socket_path],
        startup_timeout=30.0,
    )

    if not success:
        print("✗ Failed to spawn worker", flush=True)
        return

    print(f"✓ Worker spawned (PID: {supervisor._worker_pid})\n", flush=True)

    # Wait for worker to initialize
    await asyncio.sleep(2)

    # Get handle
    print("Step 3: Getting worker handle...", flush=True)
    handle = supervisor.handle()
    print("✓ Handle obtained\n", flush=True)

    # Send commands
    print("Step 4: Sending commands...\n", flush=True)

    commands = [
        {"command_type": "echo", "message": "Hello, Worker!"},
        {"command_type": "add", "a": 5, "b": 3},
        {"command_type": "add", "a": 10, "b": 20},
        {"command_type": "status"},
    ]

    for i, command in enumerate(commands, 1):
        print(f"[Manager] Sending command {i}/{len(commands)}: {command}", flush=True)

        try:
            response = await handle.send_and_wait(
                command=command, timeout=10.0, response_signal=signals.COMMAND_COMPLETE
            )

            payload = response.get("payload", {})
            result = payload.get("result", {})

            print(f"[Manager] ✓ Response: {result}", flush=True)
            print()

        except Exception as e:
            print(f"[Manager] ✗ Error: {e}", flush=True)
            break

    # Stop worker
    print("\nStep 5: Stopping worker...", flush=True)
    await supervisor.stop()
    print("✓ Worker stopped\n", flush=True)

    print("=" * 80)
    print("Demo complete - All commands processed successfully!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        # Run as worker
        worker_id = sys.argv[2]
        socket_path = sys.argv[3]
        asyncio.run(run_worker(worker_id, socket_path))
    else:
        # Run as manager
        try:
            asyncio.run(run_manager())
        except KeyboardInterrupt:
            print("\nDemo interrupted")
        except Exception as e:
            print(f"\nDemo failed: {e}")
            import traceback

            traceback.print_exc()
