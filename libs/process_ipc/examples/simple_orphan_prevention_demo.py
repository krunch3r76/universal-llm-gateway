#!/usr/bin/env python3
"""
Simple demonstration of orphan prevention mechanisms.

This script demonstrates the core orphan prevention features without
requiring the full Universal Protocol infrastructure.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Add libs to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from process_ipc.utils.helpers import setup_enhanced_orphan_prevention


def create_simple_worker_script() -> str:
    """Create a simple worker script that just runs and prints status."""
    script_content = """#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

# Add libs to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from process_ipc.utils.helpers import setup_enhanced_orphan_prevention

def signal_handler(signum, frame):
    print(f"Worker {os.getpid()} received signal {signum}, exiting")
    sys.exit(0)

def main():
    # Apply enhanced orphan prevention
    setup_enhanced_orphan_prevention()

    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    worker_id = sys.argv[1] if len(sys.argv) > 1 else "demo-worker"
    print(f"Worker {worker_id} starting (PID: {os.getpid()}, PPID: {os.getppid()})")

    # Run for a while, printing status
    counter = 0
    try:
        while True:
            counter += 1
            print(f"Worker {worker_id} heartbeat {counter} (PID: {os.getpid()})")
            time.sleep(2)
    except KeyboardInterrupt:
        print(f"Worker {worker_id} interrupted, exiting")
    except Exception as e:
        print(f"Worker {worker_id} error: {e}")

if __name__ == "__main__":
    main()
"""

    script_path = "/tmp/simple_demo_worker.py"
    with open(script_path, "w") as f:
        f.write(script_content)
    os.chmod(script_path, 0o755)
    return script_path


def demo_parent_death_signal():
    """Demonstrate parent death signal mechanism."""
    print("\n" + "=" * 80)
    print("DEMO 1: Parent Death Signal (PR_SET_PDEATHSIG)")
    print("=" * 80)

    script_path = create_simple_worker_script()

    print("Starting worker subprocess with parent death signal...")

    # Start worker
    process = subprocess.Popen([sys.executable, script_path, "pdeathsig-demo"])

    worker_pid = process.pid
    print(f"Worker started with PID: {worker_pid}")
    print(f"Parent PID: {os.getpid()}")

    # Let it run for a few seconds
    time.sleep(3)

    print("\nChecking if worker is still running...")
    try:
        os.kill(worker_pid, 0)  # Signal 0 just checks if process exists
        print(f"✓ Worker {worker_pid} is running")
    except (OSError, ProcessLookupError):
        print(f"✗ Worker {worker_pid} is not running")

    print("\nTerminating worker gracefully...")
    process.terminate()

    try:
        process.wait(timeout=5)
        print("✓ Worker terminated gracefully")
    except subprocess.TimeoutExpired:
        print("✗ Worker did not terminate, force killing...")
        process.kill()
        process.wait()


def demo_process_group_isolation():
    """Demonstrate process group isolation."""
    print("\n" + "=" * 80)
    print("DEMO 2: Process Group Isolation")
    print("=" * 80)

    script_path = create_simple_worker_script()

    def preexec_fn():
        """Enhanced preexec function with process isolation."""
        setup_enhanced_orphan_prevention()

    print("Starting worker with process group isolation...")

    # Start worker with enhanced preexec
    process = subprocess.Popen(
        [sys.executable, script_path, "group-demo"], preexec_fn=preexec_fn
    )

    worker_pid = process.pid

    # Get process group ID
    try:
        worker_pgid = os.getpgid(worker_pid)
        print(f"Worker PID: {worker_pid}, PGID: {worker_pgid}")

        # Check if it's in its own process group
        if worker_pgid == worker_pid:
            print("✓ Worker is process group leader (isolated)")
        else:
            print(f"✓ Worker is in process group {worker_pgid}")

    except OSError as e:
        print(f"Could not get process group: {e}")
        worker_pgid = None

    # Let it run
    time.sleep(3)

    print("\nTesting process group cleanup...")
    try:
        if worker_pgid and worker_pgid != os.getpgid(os.getpid()):
            # Kill entire process group
            os.killpg(worker_pgid, signal.SIGTERM)
            print(f"✓ Sent SIGTERM to process group {worker_pgid}")
        else:
            # Kill individual process
            process.terminate()
            print(f"✓ Sent SIGTERM to process {worker_pid}")
    except (OSError, ProcessLookupError) as e:
        print(f"Process cleanup: {e}")

    # Wait for cleanup
    try:
        process.wait(timeout=5)
        print("✓ Process cleanup completed")
    except subprocess.TimeoutExpired:
        print("✗ Process did not terminate, force killing...")
        try:
            if worker_pgid:
                os.killpg(worker_pgid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
            print("✓ Force cleanup completed")
        except (OSError, ProcessLookupError):
            print("✓ Process already terminated")


def demo_multiple_workers():
    """Demonstrate cleanup of multiple worker processes."""
    print("\n" + "=" * 80)
    print("DEMO 3: Multiple Workers Cleanup")
    print("=" * 80)

    script_path = create_simple_worker_script()

    def preexec_fn():
        """Enhanced preexec function."""
        setup_enhanced_orphan_prevention()

    workers = []
    print("Starting 3 worker processes...")

    # Start multiple workers
    for i in range(3):
        process = subprocess.Popen(
            [sys.executable, script_path, f"multi-demo-{i}"], preexec_fn=preexec_fn
        )

        workers.append(process)
        print(f"Started worker {i}: PID {process.pid}")

    # Let them run
    time.sleep(3)

    print("\nCleaning up all workers...")

    # Terminate all workers
    for i, process in enumerate(workers):
        try:
            process.terminate()
            print(f"Terminated worker {i}: PID {process.pid}")
        except (OSError, ProcessLookupError):
            print(f"Worker {i} already terminated")

    # Wait for all to finish
    all_cleaned = True
    for i, process in enumerate(workers):
        try:
            process.wait(timeout=3)
            print(f"✓ Worker {i} cleaned up")
        except subprocess.TimeoutExpired:
            print(f"✗ Worker {i} did not terminate, force killing...")
            try:
                process.kill()
                process.wait()
                print(f"✓ Worker {i} force killed")
            except (OSError, ProcessLookupError):
                print(f"✓ Worker {i} already gone")
            all_cleaned = False

    if all_cleaned:
        print("✓ All workers cleaned up successfully")
    else:
        print("⚠ Some workers required force cleanup")


def check_orphan_prevention_features():
    """Check which orphan prevention features are available."""
    print("\n" + "=" * 80)
    print("ORPHAN PREVENTION FEATURE CHECK")
    print("=" * 80)

    # Check platform
    print(f"Platform: {sys.platform}")

    # Check PR_SET_PDEATHSIG availability
    if sys.platform == "linux":
        try:
            import ctypes

            ctypes.CDLL("libc.so.6", use_errno=True)
            print("✓ PR_SET_PDEATHSIG (prctl) available")
        except Exception as e:
            print(f"✗ PR_SET_PDEATHSIG not available: {e}")
    else:
        print("✗ PR_SET_PDEATHSIG not available (non-Linux platform)")

    # Check process group support
    try:
        current_pgid = os.getpgid(0)
        print(f"✓ Process groups supported (current PGID: {current_pgid})")
    except Exception as e:
        print(f"✗ Process groups not available: {e}")

    # Check session support
    try:
        current_sid = os.getsid(0)
        print(f"✓ Process sessions supported (current SID: {current_sid})")
    except Exception as e:
        print(f"✗ Process sessions not available: {e}")

    # Check cgroup support
    cgroup_path = Path("/sys/fs/cgroup")
    if cgroup_path.exists():
        print("✓ Cgroups available")
    else:
        print("✗ Cgroups not available")

    import importlib.util

    if importlib.util.find_spec("psutil") is not None:
        print("✓ psutil available for advanced process management")
    else:
        print("✗ psutil not available (install with: pip install psutil)")


def main():
    """Run orphan prevention demos."""
    print("Enhanced Orphan Prevention Demonstration")
    print("This demo shows techniques to prevent orphaned worker processes")

    try:
        # Check available features
        check_orphan_prevention_features()

        # Run demos
        demo_parent_death_signal()
        demo_process_group_isolation()
        demo_multiple_workers()

        print("\n" + "=" * 80)
        print("All demos completed successfully!")
        print("=" * 80)
        print("\nKey takeaways:")
        print("1. PR_SET_PDEATHSIG ensures workers die when parent is killed")
        print("2. Process group isolation allows clean group termination")
        print("3. Enhanced preexec_fn combines multiple protection layers")
        print("4. Multiple cleanup methods provide robust orphan prevention")

    except KeyboardInterrupt:
        print("\nDemo interrupted by user")
    except Exception as e:
        print(f"\nDemo failed: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Clean up demo script
        try:
            os.unlink("/tmp/simple_demo_worker.py")
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
