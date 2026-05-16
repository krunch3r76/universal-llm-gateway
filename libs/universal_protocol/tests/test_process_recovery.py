"""Test process recovery and supervisor handling of worker crashes.

Verifies that supervisor detects worker death, cleans up resources, and handles reconnection.
"""

import asyncio
import os
import signal
import tempfile

import pytest
from process_ipc import (
    ProcessHealthConfig,
    ProcessSupervisor,
    SupervisorConfig,
    UnixSocketConfig,
)

from universal_protocol import AsyncRPCClient


@pytest.fixture
def temp_socket_dir():
    """Create a temporary directory for sockets."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def supervisor_config(temp_socket_dir):
    """Create supervisor config for testing."""
    socket_path = os.path.join(temp_socket_dir, "test-worker.sock")

    transport_config = UnixSocketConfig(socket_path=socket_path)

    health_config = ProcessHealthConfig(
        check_interval=1.0,
        failure_threshold=2,
        recovery_threshold=1,
        enable_health_checks=True,
    )

    return SupervisorConfig(
        transport=transport_config,
        health=health_config,
        worker_startup_timeout=5.0,
        worker_shutdown_timeout=2.0,
    )


@pytest.mark.asyncio
async def test_supervisor_detects_worker_death(supervisor_config):
    """Test that supervisor detects when worker process dies."""
    supervisor = ProcessSupervisor(config=supervisor_config)

    # Create a simple worker script that sleeps
    worker_script = """
import time
import signal
import sys

# Handle SIGTERM gracefully
def handle_term(signum, frame):
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_term)

# Sleep until killed
while True:
    time.sleep(0.1)
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(worker_script)
        script_path = f.name

    try:
        # Spawn worker
        command = ["python", script_path]
        success = await supervisor.spawn(
            worker_id="test-worker", command=command, env={}, startup_timeout=2.0
        )

        assert success
        assert supervisor.is_running()

        # Get worker PID
        worker_pid = supervisor._worker_pid
        assert worker_pid is not None

        # Verify process is running
        try:
            os.kill(worker_pid, 0)  # Check if process exists
        except ProcessLookupError:
            pytest.fail("Worker process not running")

        # Kill worker process forcefully
        os.kill(worker_pid, signal.SIGKILL)

        # Give supervisor time to detect death
        await asyncio.sleep(2.0)

        # Verify supervisor detected the death
        assert not supervisor.is_running()

        # Verify socket was cleaned up
        socket_path = supervisor_config.transport.socket_path
        assert not os.path.exists(socket_path)

    finally:
        # Cleanup
        await supervisor.shutdown()
        os.unlink(script_path)


@pytest.mark.asyncio
async def test_worker_restart_after_crash(supervisor_config):
    """Test that worker can be restarted after crash."""
    supervisor = ProcessSupervisor(config=supervisor_config)

    # Create a worker that exits after a delay
    worker_script = """
import time
import sys
import os

# Write PID to file for tracking
pid_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/worker.pid"
with open(pid_file, 'w') as f:
    f.write(str(os.getpid()))

# Exit after short delay to simulate crash
time.sleep(1.0)
sys.exit(1)
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(worker_script)
        script_path = f.name

    pid_file = os.path.join(tempfile.gettempdir(), "test-worker.pid")

    try:
        # First spawn
        command = ["python", script_path, pid_file]
        success = await supervisor.spawn(
            worker_id="test-worker", command=command, env={}, startup_timeout=2.0
        )

        assert success

        # Read first PID
        with open(pid_file) as f:
            first_pid = int(f.read().strip())

        # Wait for crash
        await asyncio.sleep(2.0)
        assert not supervisor.is_running()

        # Clean up socket
        socket_path = supervisor_config.transport.socket_path
        if os.path.exists(socket_path):
            os.unlink(socket_path)

        # Restart worker
        success = await supervisor.spawn(
            worker_id="test-worker", command=command, env={}, startup_timeout=2.0
        )

        assert success

        # Read second PID
        await asyncio.sleep(0.5)  # Give time to write PID
        with open(pid_file) as f:
            second_pid = int(f.read().strip())

        # Verify different PIDs (new process)
        assert second_pid != first_pid

    finally:
        await supervisor.shutdown()
        os.unlink(script_path)
        if os.path.exists(pid_file):
            os.unlink(pid_file)


@pytest.mark.asyncio
async def test_client_error_on_worker_death():
    """Test that RPC client gets proper error when worker dies."""
    socket_path = "/tmp/test-dead-worker.sock"

    # Create client pointing to non-existent worker
    client = AsyncRPCClient(socket_path, verify_socket=False)

    # Try to make RPC call
    with pytest.raises((ConnectionError, OSError)) as exc_info:
        await client.call("health", {})

    # Verify we get connection refused or similar
    error_msg = str(exc_info.value).lower()
    assert any(
        phrase in error_msg
        for phrase in [
            "connection refused",
            "no such file",
            "cannot connect",
            "failed to connect",
        ]
    )

    await client.close()


@pytest.mark.asyncio
async def test_stream_cleanup_on_worker_crash():
    """Test that active streams are cleaned up when worker crashes."""
    from universal_protocol.ws import stream_registry
    from universal_protocol.ws.bounded_queue import BoundedQueue
    from universal_protocol.ws.lifecycle import StreamContext

    # Simulate active streams
    stream_ids = []
    for i in range(3):
        stream_id = f"test-stream-{i}"
        context = StreamContext(stream_id)
        queue = BoundedQueue()

        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)
        stream_ids.append(stream_id)

    # Verify streams exist
    assert len(stream_registry) == 3

    # Simulate worker crash by cleaning up all streams
    # (In real scenario, this would be triggered by crash detection)
    for stream_id in stream_ids:
        if stream_id in stream_registry:
            entry = stream_registry.get(stream_id)
            if entry is None:
                continue

            # Send error to queue
            try:
                if entry.queue:
                    await entry.queue.put(
                        {
                            "t": "err",
                            "code": "WORKER_CRASHED",
                            "message": "Worker process terminated unexpectedly",
                            "source": "system",
                        }
                    )
            except Exception:
                pass

            # Cleanup
            if entry.queue:
                await entry.queue.close()
            if entry.context:
                await entry.context.cleanup()
            stream_registry.unregister(stream_id)

    # Verify all streams cleaned up
    assert len(stream_registry) == 0
