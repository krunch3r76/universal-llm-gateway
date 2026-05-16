"""Common test utilities and fixtures for Universal Protocol tests.

Provides fixtures for proper resource cleanup and test isolation.
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from universal_protocol.rpc.client import AsyncRPCClient
from universal_protocol.ws import stream_registry

logger = logging.getLogger(__name__)


@pytest.fixture
async def cleanup_active_streams():
    """Ensure stream_registry is cleaned up after each test."""
    # Clear any existing streams before test
    stream_registry.clear()

    yield

    # Clean up any remaining streams after test
    if len(stream_registry) > 0:
        logger.warning(
            f"Test left {len(stream_registry)} active streams, cleaning up..."
        )
        stream_registry.clear()


@asynccontextmanager
async def managed_subprocess(
    command: list,
    env: dict = None,
    cwd: str = None,
    startup_timeout: float = 5.0,
    shutdown_timeout: float = 5.0,
) -> AsyncIterator[subprocess.Popen]:
    """Context manager for subprocess with guaranteed cleanup.

    Args:
        command: Command to execute
        env: Environment variables
        cwd: Working directory
        startup_timeout: Maximum time to wait for process to start
        shutdown_timeout: Maximum time to wait for graceful shutdown

    Yields:
        subprocess.Popen instance
    """
    proc = None
    try:
        # Start process
        proc = subprocess.Popen(
            command,
            env=env or os.environ.copy(),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Give process time to start
        await asyncio.sleep(0.1)

        # Check if process started successfully
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=1)
            raise RuntimeError(
                f"Process exited immediately with code {proc.returncode}\\n"
                f"stdout: {stdout}\\n"
                f"stderr: {stderr}"
            )

        yield proc

    finally:
        # Ensure process is cleaned up
        if proc and proc.poll() is None:
            try:
                # Try graceful termination first
                proc.terminate()
                try:
                    proc.wait(timeout=shutdown_timeout)
                except subprocess.TimeoutExpired:
                    # Force kill if graceful shutdown fails
                    logger.warning("Process didn't terminate gracefully, killing...")
                    proc.kill()
                    proc.wait(timeout=2)
            except Exception as e:
                logger.error(f"Error cleaning up subprocess: {e}")


@asynccontextmanager
async def managed_worker_process(
    worker_id: str = "test-1", socket_dir: Path = None, startup_timeout: float = 10.0
) -> AsyncIterator[tuple[subprocess.Popen, str]]:
    """Start a test worker process with cleanup.

    Args:
        worker_id: Worker identifier
        socket_dir: Directory for socket files (temp dir if None)
        startup_timeout: Maximum time to wait for socket creation

    Yields:
        Tuple of (process, socket_path)
    """
    # Create temp dir if needed
    temp_dir = None
    if socket_dir is None:
        temp_dir = tempfile.TemporaryDirectory(prefix=f"uprotocol-{worker_id}-")
        socket_dir = Path(temp_dir.name)

    socket_path = socket_dir / f"worker-{worker_id}.sock"

    try:
        # Start worker process
        command = [
            "python",
            "-m",
            "universal_protocol.examples.start_server",
            "--worker-id",
            worker_id,
            "--socket-dir",
            str(socket_dir),
            "--log-level",
            "warning",
        ]

        async with managed_subprocess(command) as proc:
            # Wait for socket to be created
            start_time = time.time()
            while (
                not socket_path.exists()
                and (time.time() - start_time) < startup_timeout
            ):
                await asyncio.sleep(0.1)

                # Check if process is still running
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate(timeout=1)
                    raise RuntimeError(
                        f"Worker process exited during startup\\n"
                        f"stdout: {stdout}\\n"
                        f"stderr: {stderr}"
                    )

            if not socket_path.exists():
                raise RuntimeError(
                    f"Worker socket not created after {startup_timeout}s"
                )

            logger.info(f"Worker {worker_id} started, socket: {socket_path}")
            yield (proc, str(socket_path))

    finally:
        # Clean up socket file if it exists
        if socket_path.exists():
            try:
                socket_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to remove socket: {e}")

        # Clean up temp dir
        if temp_dir:
            temp_dir.cleanup()


async def wait_for_condition(
    condition_func,
    timeout: float = 5.0,
    interval: float = 0.1,
    error_msg: str = "Condition not met",
):
    """Wait for a condition to become true.

    Args:
        condition_func: Async function that returns True when condition is met
        timeout: Maximum time to wait
        interval: Time between checks
        error_msg: Error message if timeout occurs

    Raises:
        TimeoutError: If condition is not met within timeout
    """
    start_time = time.time()
    while (time.time() - start_time) < timeout:
        if await condition_func():
            return
        await asyncio.sleep(interval)

    raise TimeoutError(f"{error_msg} after {timeout}s")


async def assert_worker_healthy(socket_path: str, timeout: float = 5.0):
    """Assert that a worker is healthy and responding.

    Args:
        socket_path: Path to worker's Unix socket
        timeout: Maximum time to wait for health check

    Raises:
        AssertionError: If worker is not healthy
    """
    try:
        async with AsyncRPCClient(
            socket_path, timeout=timeout, verify_socket=False
        ) as client:
            health = await client.health()
            assert health.get("status") in ["ready", "busy"], (
                f"Unexpected health status: {health}"
            )
            logger.info(f"Worker health check passed: {health}")
    except Exception as e:
        raise AssertionError(f"Worker health check failed: {e}")


def cleanup_test_sockets(prefix: str = "uprotocol-test-"):
    """Clean up any leftover test sockets in /tmp.

    Args:
        prefix: Prefix of socket files to clean up
    """
    import glob

    sockets = glob.glob(f"/tmp/{prefix}*.sock")
    for socket in sockets:
        try:
            os.unlink(socket)
            logger.info(f"Cleaned up test socket: {socket}")
        except Exception as e:
            logger.warning(f"Failed to clean up socket {socket}: {e}")
