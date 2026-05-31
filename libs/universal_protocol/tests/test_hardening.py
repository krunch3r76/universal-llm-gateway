#!/usr/bin/env python3
"""Hardening tests for Universal Protocol MVP.

Tests the security and resilience features:
1. Queue timeout and error propagation
2. 4 MB cumulative payload limit
3. Tokenizer-backed exact counts
4. UDS hardening (symlink rejection, permissions)
"""

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Add libs to Python path if not already there
libs_path = Path(__file__).resolve().parents[3]
if str(libs_path) not in sys.path:
    sys.path.insert(0, str(libs_path))

from universal_protocol.config import get_config  # noqa: E402
from universal_protocol.rpc.handlers import (  # noqa: E402
    handle_count_tokens,
    register_tokenizer_callback,
)
from universal_protocol.server.uds_security import bind_socket  # noqa: E402
from universal_protocol.ws import producer_put, stream_registry  # noqa: E402
from universal_protocol.ws.bounded_queue import BoundedQueue  # noqa: E402
from universal_protocol.ws.lifecycle import StreamContext  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_queue_timeout_error_propagation():
    """Test that queue timeout errors are properly handled."""
    logger.info("\n=== Testing Queue Timeout Error Propagation ===")

    # Create a bounded queue
    queue = BoundedQueue()
    context = StreamContext("test-stream-1")

    # Register stream in stream_registry (type: ignore for BoundedQueue vs
    # UnboundedStreamQueue)
    stream_registry.register(
        "test-stream-1", kind="stream", context=context, queue=queue
    )

    try:
        # Fill the queue to capacity
        logger.info(f"Filling queue to capacity ({queue.QUEUE_CAPACITY} frames)")
        for i in range(queue.QUEUE_CAPACITY):
            frame = {"t": "token", "i": i, "txt": f"token{i}"}
            success = await producer_put("test-stream-1", frame)
            assert success, f"Failed to enqueue frame {i}"

        logger.info("Queue is now full")

        # Try to add one more frame - should timeout
        logger.info("Attempting to add frame to full queue (should timeout)")
        frame = {"t": "token", "i": queue.QUEUE_CAPACITY, "txt": "overflow"}
        success = await producer_put("test-stream-1", frame)
        assert not success, "Producer should have failed due to timeout"

        logger.info("✅ Producer correctly returned False on timeout")

        # Test that oversized frames are also handled
        logger.info("\nTesting oversized frame handling...")

        # Clear some space
        for _ in range(10):
            await queue.get()

        # Try to send an oversized frame
        oversized_text = "x" * 5000  # Definitely over 4KB limit
        oversized_frame = {"t": "token", "txt": oversized_text}
        success = await producer_put("test-stream-1", oversized_frame)
        assert not success, "Producer should have failed due to frame size"

        logger.info("✅ Producer correctly rejected oversized frame")

        # Test cancellation handling
        logger.info("\nTesting cancellation handling...")

        # Simulate a cancelled task scenario
        try:
            # This would normally be triggered by task cancellation
            raise asyncio.CancelledError("Simulated cancellation")
        except asyncio.CancelledError:
            # producer_put should handle this gracefully
            cancelled_frame = {"t": "token", "txt": "cancelled"}
            # Wrap in a task to simulate real cancellation scenario
            task = asyncio.create_task(producer_put("test-stream-1", cancelled_frame))
            task.cancel()
            try:
                success = await task
            except asyncio.CancelledError:
                logger.info("✅ Cancellation handled as expected")

        logger.info("✅ Queue timeout error propagation test passed")

    finally:
        # Cleanup
        stream_registry.unregister("test-stream-1")
        await queue.close()


@pytest.mark.asyncio
async def test_cumulative_4mb_limit():
    """Test that streams are terminated when exceeding 4MB cumulative limit."""
    logger.info("\n=== Testing 4MB Cumulative Limit ===")

    # Create a bounded queue
    queue = BoundedQueue()
    context = StreamContext("test-stream-2")

    # Register stream in stream_registry (type: ignore for BoundedQueue vs
    # UnboundedStreamQueue)
    stream_registry.register(
        "test-stream-2", kind="stream", context=context, queue=queue
    )

    try:
        config = get_config()
        limit_mb = config.cumulative_limit_bytes // 1024 // 1024
        logger.info(f"Cumulative limit: {limit_mb} MB")

        # Create large frames (but under individual frame limit)
        frame_size = 1024  # 1 KB per frame
        large_text = "x" * frame_size
        frames_sent = 0
        total_bytes = 0

        # Send frames until we hit the cumulative limit
        logger.info("Sending frames to approach cumulative limit...")

        # Use frames close to but under the individual limit
        # Account for SSE overhead (data: prefix and \n\n suffix)
        frame_size = 3900  # Leave room for JSON structure and SSE formatting
        large_text = "x" * frame_size

        while total_bytes < config.cumulative_limit_bytes:
            frame = {"t": "token", "i": frames_sent, "txt": large_text}

            # Calculate frame size in SSE format
            from sse.core import format_sse

            sse_frame = format_sse(frame)
            frame_bytes = len(sse_frame.encode("utf-8"))

            # Check if this would exceed limit
            if total_bytes + frame_bytes > config.cumulative_limit_bytes:
                # This frame should fail
                logger.info("Attempting frame that would exceed limit...")
                logger.info(
                    f"Current total: {total_bytes:,} bytes, frame size: {frame_bytes:,} bytes"
                )
                logger.info(
                    f"Would total: {total_bytes + frame_bytes:,} bytes (limit: {config.cumulative_limit_bytes:,})"
                )

                success = await producer_put("test-stream-2", frame)
                assert not success, (
                    "Producer should have failed due to cumulative limit"
                )
                logger.info("✅ Cumulative limit correctly enforced")

                break

            # Send frame (also consume old frames to prevent queue full)
            if queue.qsize() >= queue.QUEUE_CAPACITY - 2:
                # Make room by consuming old frames
                for _ in range(10):
                    if queue.qsize() > 0:
                        await queue.get()

            success = await producer_put("test-stream-2", frame)
            assert success, f"Failed to send frame {frames_sent}"

            frames_sent += 1
            total_bytes += frame_bytes

            if frames_sent % 10 == 0:
                logger.info(f"Sent {frames_sent} frames, total {total_bytes:,} bytes")

        logger.info(
            f"✅ Cumulative limit test passed - limit enforced at {total_bytes:,} bytes"
        )

    finally:
        # Cleanup
        stream_registry.unregister("test-stream-2")
        await queue.close()


@pytest.mark.asyncio
async def test_tokenizer_exact_counts():
    """Test tokenizer-backed exact token counting."""
    logger.info("\n=== Testing Tokenizer Exact Counts ===")

    # Define a mock tokenizer that counts words (simplified)
    async def mock_tokenizer(text: str, model: str = None) -> dict[str, Any]:
        """Mock tokenizer that counts words as tokens."""
        # Simple word-based tokenization for testing
        tokens = text.split()
        return {"count": len(tokens)}

    # Register the tokenizer
    register_tokenizer_callback(mock_tokenizer)

    try:
        # Test with exact tokenizer
        test_text = "The quick brown fox jumps over the lazy dog"
        result = await handle_count_tokens({"text": test_text})

        logger.info(f"Text: '{test_text}'")
        logger.info(f"Token count result: {result}")

        assert result["method"] == "exact", "Should use exact tokenizer"
        assert result["count"] == 9, f"Expected 9 tokens, got {result['count']}"

        logger.info("✅ Exact tokenizer test passed")

        # Unregister tokenizer to test fallback
        handle_count_tokens._tokenizer_callback = None

        # Test fallback to estimation
        result2 = await handle_count_tokens({"text": test_text})
        logger.info(f"Fallback result: {result2}")

        assert result2["method"] == "estimate", "Should use estimation fallback"
        # The text is 43 characters, so estimate should be 43//4 = 10
        expected_estimate = max(1, len(test_text) // 4)
        assert result2["count"] == expected_estimate, (
            f"Expected {expected_estimate}, got {result2['count']}"
        )

        logger.info("✅ Estimation fallback test passed")

    finally:
        # Cleanup
        handle_count_tokens._tokenizer_callback = None


@pytest.mark.asyncio
async def test_uds_hardening():
    """Test UDS security hardening features."""
    logger.info("\n=== Testing UDS Hardening ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Test 1: Symlink rejection
        logger.info("Testing symlink rejection...")
        socket_path = os.path.join(tmpdir, "test.sock")
        symlink_path = os.path.join(tmpdir, "symlink.sock")

        # Create a symlink
        open(socket_path, "w").close()  # Create target file
        os.symlink(socket_path, symlink_path)

        try:
            bind_socket(symlink_path)
            assert False, "Should have rejected symlink"
        except OSError as e:
            logger.info(f"✅ Symlink correctly rejected: {e}")
            assert "symlink" in str(e).lower()

        # Test 2: Socket permissions
        logger.info("Testing socket permissions...")
        real_socket_path = os.path.join(tmpdir, "real.sock")
        sock = bind_socket(real_socket_path, permissions=0o600)

        try:
            # Check permissions
            stat_info = os.stat(real_socket_path)
            perms = stat_info.st_mode & 0o777
            logger.info(f"Socket permissions: {oct(perms)}")
            assert perms == 0o600, f"Expected 0o600, got {oct(perms)}"
            logger.info("✅ Socket permissions correctly set to 0600")

        finally:
            sock.close()
            if os.path.exists(real_socket_path):
                os.unlink(real_socket_path)

        # Test 3: Long path warning
        logger.info("Testing long path warning...")
        # Create a path that's exactly 100 bytes
        long_name = "a" * (100 - len(tmpdir) - 1)  # -1 for the '/'
        long_path = os.path.join(tmpdir, long_name)

        # This should log a warning but still work
        sock = bind_socket(long_path)
        try:
            assert os.path.exists(long_path), (
                "Socket should be created despite long path"
            )
            logger.info("✅ Long path warning test passed")
        finally:
            sock.close()
            if os.path.exists(long_path):
                os.unlink(long_path)

        # Test 4: Unlink before bind
        logger.info("Testing unlink before bind...")
        stale_path = os.path.join(tmpdir, "stale.sock")

        # Create a stale socket
        sock1 = bind_socket(stale_path)
        sock1.close()
        assert os.path.exists(stale_path), "Socket should exist"

        # Bind again - should unlink first
        sock2 = bind_socket(stale_path, unlink_first=True)
        try:
            logger.info("✅ Successfully bound to path with stale socket")
        finally:
            sock2.close()
            if os.path.exists(stale_path):
                os.unlink(stale_path)


async def main():
    """Run all hardening tests."""
    logger.info("Starting Universal Protocol hardening tests")

    tests = [
        ("Queue Timeout Error Propagation", test_queue_timeout_error_propagation),
        ("4MB Cumulative Limit", test_cumulative_4mb_limit),
        ("Tokenizer Exact Counts", test_tokenizer_exact_counts),
        ("UDS Hardening", test_uds_hardening),
    ]

    failed = False

    for test_name, test_func in tests:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Running test: {test_name}")
        logger.info(f"{'=' * 60}")

        try:
            await test_func()
            logger.info(f"✅ {test_name} PASSED")
        except Exception as e:
            logger.error(f"❌ {test_name} FAILED: {e}", exc_info=True)
            failed = True

    if failed:
        logger.error("\n❌ Some hardening tests failed")
        return 1
    else:
        logger.info("\n✅ All hardening tests passed!")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
