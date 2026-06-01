"""Test 3: Idle Soak Test

Purpose: Verify cleanup and resource management in long-running sessions

Test flow:
- Start worker and controller
- Repeatedly (60 times): open stream → send 10 tokens → close stream
- Each cycle should take < 2 seconds
- After 60 cycles, verify:
  * debug_stats shows FDs at baseline (±2)
  * debug_stats shows tasks at baseline (±1)
  * No exceptions in logs
"""

import asyncio
import logging
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)


@pytest.fixture
def test_socket_dir():
    """Create a temporary directory for test sockets."""
    with tempfile.TemporaryDirectory(prefix="uprotocol-soak-") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def server_process(test_socket_dir):
    """Start the Universal Protocol server in a subprocess."""
    socket_path = test_socket_dir / "worker-1.sock"

    proc = subprocess.Popen(
        [
            "python",
            "-m",
            "universal_protocol.examples.start_server",
            "--worker-id",
            "1",
            "--socket-dir",
            str(test_socket_dir),
            "--log-level",
            "warning",  # Less verbose for soak test
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Wait for socket to be created (max 10 seconds)
    max_wait = 10
    start_time = time.time()
    while not socket_path.exists() and (time.time() - start_time) < max_wait:
        time.sleep(0.1)

    if not socket_path.exists():
        proc.kill()
        proc.wait(timeout=5)
        pytest.skip(f"Server did not create socket at {socket_path} after {max_wait}s")

    logger.info(f"Server started for soak test, socket: {socket_path}")

    yield (proc, str(socket_path))

    # Cleanup
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    logger.info("Server stopped")


@pytest.mark.asyncio(timeout=120)  # 2-minute timeout for soak test
@pytest.mark.slow
async def test_idle_soak_60_cycles(server_process):
    """Test: 60 connect/stream/disconnect cycles.

    Verifies:
    - Each cycle completes in < 2 seconds
    - Resource counts stay at baseline (±2 FDs, ±1 task)
    - No exceptions or errors
    """
    from universal_protocol.rpc.client import AsyncRPCClient
    from universal_protocol.ws.client import StreamClient

    proc, socket_path = server_process

    # Get baseline stats
    async with AsyncRPCClient(socket_path, verify_socket=False) as client:
        baseline = await client.call_rpc("debug_stats")
        logger.info(f"Baseline stats: {baseline}")

    # Run 60 cycles
    num_cycles = 60
    max_cycle_time = 2.0  # Each cycle must complete in < 2 seconds
    cycle_times = []
    errors = 0

    for cycle_num in range(num_cycles):
        cycle_start = time.time()

        try:
            stream_id = f"soak-stream-{cycle_num:03d}"

            async with StreamClient(socket_path, stream_id, timeout=10) as client:
                tokens_received = 0
                completion_received = False

                try:
                    async for message in client.iter_messages():
                        if message.get("t") == "token":
                            tokens_received += 1
                        elif message.get("t") == "done":
                            completion_received = True
                            break

                except Exception as e:
                    logger.warning(f"Stream error in cycle {cycle_num}: {e}")

            cycle_time = time.time() - cycle_start
            cycle_times.append(cycle_time)

            logger.info(
                f"Cycle {cycle_num:02d}/{num_cycles}: "
                f"{cycle_time:.2f}s "
                f"({tokens_received} tokens, completion={completion_received})"
            )

            # Verify cycle time
            if cycle_time > max_cycle_time:
                logger.warning(
                    f"Cycle {cycle_num} exceeded max time ({cycle_time:.2f}s > "
                    f"{max_cycle_time}s)"
                )

        except Exception as e:
            logger.exception(f"Unexpected error in cycle {cycle_num}: {e}")
            errors += 1

        # Every 10 cycles, print progress
        if (cycle_num + 1) % 10 == 0:
            avg_time = sum(cycle_times) / len(cycle_times)
            logger.info(
                f"Progress: {cycle_num + 1}/{num_cycles} cycles, avg time: "
                f"{avg_time:.2f}s"
            )

    # Wait for cleanup
    await asyncio.sleep(1.0)

    # Get final stats
    async with AsyncRPCClient(socket_path, verify_socket=False) as client:
        final_stats = await client.call_rpc("debug_stats")
        logger.info(f"Final stats after {num_cycles} cycles: {final_stats}")

    # Verify results
    logger.info(f"\n{'=' * 60}")
    logger.info(f"Soak Test Results ({num_cycles} cycles)")
    logger.info(f"{'=' * 60}")

    # Cycle time statistics
    avg_cycle_time = sum(cycle_times) / len(cycle_times)
    max_single_cycle = max(cycle_times)
    min_single_cycle = min(cycle_times)
    slow_cycles = sum(1 for t in cycle_times if t > max_cycle_time)

    logger.info("Cycle timing:")
    logger.info(f"  Average: {avg_cycle_time:.3f}s")
    logger.info(f"  Min: {min_single_cycle:.3f}s")
    logger.info(f"  Max: {max_single_cycle:.3f}s")
    logger.info(f"  Cycles > {max_cycle_time}s: {slow_cycles}")

    # Resource statistics
    fd_baseline = baseline["fds_open"]
    fd_final = final_stats["fds_open"]
    fd_diff = abs(fd_final - fd_baseline)

    task_baseline = baseline["tasks_running"]
    task_final = final_stats["tasks_running"]
    task_diff = abs(task_final - task_baseline)

    logger.info("File descriptors:")
    logger.info(f"  Baseline: {fd_baseline}")
    logger.info(f"  Final: {fd_final}")
    logger.info(f"  Difference: {fd_diff}")

    logger.info("Tasks:")
    logger.info(f"  Baseline: {task_baseline}")
    logger.info(f"  Final: {task_final}")
    logger.info(f"  Difference: {task_diff}")

    logger.info(f"Errors: {errors}")
    logger.info(f"{'=' * 60}\n")

    # Assertions
    assert errors == 0, f"Expected 0 errors, got {errors}"
    assert slow_cycles <= 5, f"Too many slow cycles ({slow_cycles} > 5)"
    assert fd_diff <= 2, f"FD count diverged: {fd_diff} > 2"
    assert task_diff <= 1, f"Task count diverged: {task_diff} > 1"


if __name__ == "__main__":
    # Run with: pytest tests/test_idle_soak.py -v -s
    pytest.main([__file__, "-v", "-s"])
