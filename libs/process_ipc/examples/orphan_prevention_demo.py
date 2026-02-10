#!/usr/bin/env python3
"""
Demonstration of enhanced orphan prevention mechanisms.

This script shows how the enhanced process isolation prevents
worker processes from becoming orphans when the parent is killed.
"""

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

# Add libs to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from process_ipc.core.config import SupervisorConfig
from process_ipc.process.supervisor import ProcessSupervisor
from process_ipc.utils.helpers import setup_enhanced_orphan_prevention


class DemoWorker:
    """Simple worker that demonstrates orphan prevention."""

    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.running = True

    async def run(self):
        """Run the worker with periodic status updates."""
        print(f"Worker {self.worker_id} starting (PID: {os.getpid()})")

        # Set up signal handlers
        def shutdown_handler(signum, frame):
            print(f"Worker {self.worker_id} received signal {signum}, shutting down")
            self.running = False

        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)

        # Main worker loop
        counter = 0
        while self.running:
            counter += 1
            print(f"Worker {self.worker_id} heartbeat {counter} (PID: {os.getpid()})")
            await asyncio.sleep(2)

        print(f"Worker {self.worker_id} shutting down gracefully")


async def run_worker_process(worker_id: str):
    """Entry point for worker subprocess."""
    # Apply enhanced orphan prevention
    setup_enhanced_orphan_prevention()

    worker = DemoWorker(worker_id)
    await worker.run()


def create_worker_script() -> str:
    """Create a temporary worker script."""
    script_content = """#!/usr/bin/env python3
import asyncio
import sys
import os
from pathlib import Path

# Add libs to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from process_ipc.examples.orphan_prevention_demo import run_worker_process

if __name__ == "__main__":
    worker_id = sys.argv[1] if len(sys.argv) > 1 else "demo-worker"
    asyncio.run(run_worker_process(worker_id))
"""

    script_path = "/tmp/demo_worker.py"
    with open(script_path, "w") as f:
        f.write(script_content)
    os.chmod(script_path, 0o755)
    return script_path


async def demo_basic_orphan_prevention():
    """Demonstrate basic orphan prevention with parent death signal."""
    print("\n" + "=" * 80)
    print("DEMO 1: Basic Orphan Prevention (PR_SET_PDEATHSIG)")
    print("=" * 80)

    script_path = create_worker_script()

    print("Starting worker subprocess...")

    # Start worker with enhanced orphan prevention
    process = subprocess.Popen([sys.executable, script_path, "basic-demo"])

    worker_pid = process.pid
    print(f"Worker started with PID: {worker_pid}")

    # Let it run for a few seconds
    await asyncio.sleep(5)

    print(f"Killing parent process (PID: {os.getpid()}) with SIGKILL...")
    print("Worker should automatically die due to PR_SET_PDEATHSIG")

    # Simulate parent being killed
    os.kill(os.getpid(), signal.SIGKILL)


async def demo_supervisor_orphan_prevention():
    """Demonstrate supervisor-based orphan prevention."""
    print("\n" + "=" * 80)
    print("DEMO 2: Supervisor-Based Orphan Prevention")
    print("=" * 80)

    socket_path = "/tmp/orphan_demo.sock"
    worker_id = "supervisor-demo"

    # Create supervisor
    config = SupervisorConfig.from_socket_path(socket_path)
    supervisor = ProcessSupervisor(config)

    # Set up emergency cleanup handler
    supervisor.setup_emergency_cleanup_handler()

    script_path = create_worker_script()

    print("Starting worker via ProcessSupervisor...")

    # Spawn worker
    success = await supervisor.spawn(
        worker_id=worker_id,
        command=[sys.executable, script_path, worker_id],
        startup_timeout=10.0,
    )

    if not success:
        print("Failed to spawn worker")
        return

    worker_pid = supervisor._worker_pid
    worker_pgid = supervisor._worker_pgid
    print(f"Worker spawned: PID={worker_pid}, PGID={worker_pgid}")

    # Let it run
    await asyncio.sleep(5)

    print("Testing graceful shutdown...")
    await supervisor.stop(force=False, timeout=10.0)
    print("Graceful shutdown completed")

    # Spawn another worker to test force cleanup
    print("\nTesting force cleanup...")
    success = await supervisor.spawn(
        worker_id=worker_id + "-2",
        command=[sys.executable, script_path, worker_id + "-2"],
        startup_timeout=10.0,
    )

    if success:
        await asyncio.sleep(3)
        print("Force killing worker tree...")
        await supervisor.force_cleanup_process_tree(timeout=5.0)
        print("Force cleanup completed")


async def demo_process_group_cleanup():
    """Demonstrate process group cleanup."""
    print("\n" + "=" * 80)
    print("DEMO 3: Process Group Cleanup")
    print("=" * 80)

    script_path = create_worker_script()

    def preexec_fn():
        """Enhanced preexec function with process isolation."""
        setup_enhanced_orphan_prevention()

    print("Starting worker with process group isolation...")

    # Start worker with process group
    process = subprocess.Popen(
        [sys.executable, script_path, "group-demo"], preexec_fn=preexec_fn
    )

    worker_pid = process.pid

    # Get process group ID
    try:
        worker_pgid = os.getpgid(worker_pid)
        print(f"Worker PID: {worker_pid}, PGID: {worker_pgid}")
    except OSError:
        print(f"Worker PID: {worker_pid}, PGID: unknown")
        worker_pgid = None

    # Let it run
    await asyncio.sleep(5)

    print("Killing entire process group...")
    try:
        if worker_pgid:
            os.killpg(worker_pgid, signal.SIGKILL)
            print(f"Killed process group {worker_pgid}")
        else:
            process.kill()
            print(f"Killed process {worker_pid}")
    except (OSError, ProcessLookupError) as e:
        print(f"Process cleanup: {e}")

    # Wait for cleanup
    try:
        process.wait(timeout=5)
        print("Process cleanup completed")
    except subprocess.TimeoutExpired:
        print("Process may still be running")


async def main():
    """Run all orphan prevention demos."""
    print("Enhanced Orphan Prevention Demonstration")
    print("This demo shows multiple techniques to prevent orphaned worker processes")

    try:
        # Demo 2: Supervisor-based (safest to run)
        await demo_supervisor_orphan_prevention()

        # Demo 3: Process group cleanup
        await demo_process_group_cleanup()

        print("\n" + "=" * 80)
        print("All demos completed successfully!")
        print("=" * 80)

        # Note about Demo 1
        print("\nNOTE: Demo 1 (basic orphan prevention) kills the parent process")
        print("and is commented out to avoid terminating this script.")
        print("Uncomment the line below to test it in isolation:")
        print("# await demo_basic_orphan_prevention()")

    except KeyboardInterrupt:
        print("\nDemo interrupted by user")
    except Exception as e:
        print(f"\nDemo failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    # Clean up any existing demo files
    try:
        os.unlink("/tmp/demo_worker.py")
    except FileNotFoundError:
        pass

    asyncio.run(main())
