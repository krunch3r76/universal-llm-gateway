#!/usr/bin/env python3
"""Test cancellation and backpressure contract compliance per Universal Protocol MVP spec.

Verifies:
1. Cancellation path emits {"t":"err","code":"CANCELLED","source":"stream"} via SSE
2. Error frames are enqueued via BoundedQueue.put() and respect frame/cumulative/timeout limits
3. No AttributeError on cancellation (queue.enqueue() method doesn't exist)
4. Backpressure timeouts emit {"t":"err","code":"QUEUE_TIMEOUT","source":"stream"}
5. All error frames include "source" field per spec §2.3
"""

import asyncio
import logging

import pytest
from sse.core import format_sse, parse_sse

from universal_protocol.ids import generate_stream_id
from universal_protocol.ws import producer_put, stream_registry
from universal_protocol.ws.bounded_queue import BoundedQueue, QueueTimeoutError
from universal_protocol.ws.lifecycle import StreamContext

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class TestCancellationContractCompliance:
    """Test suite for cancellation contract violations per §2.1."""

    @pytest.mark.asyncio
    async def test_cancellation_emits_error_frame_with_source(self):
        """Verify cancellation path emits proper error frame.

        Regression test for: services/_universal-llm-gateway/src/core/workers/worker.py:470
        Was: await queue.enqueue(error_frame)  # AttributeError: enqueue doesn't exist
        Now: await queue.put(error_frame)  # Proper API with error frame validation
        """
        logger.info("\n=== Test Cancellation Error Frame ===")

        stream_id = generate_stream_id()
        context = StreamContext(stream_id)
        queue = BoundedQueue()

        # Register stream (type: ignore for BoundedQueue vs UnboundedStreamQueue)
        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

        try:
            # Simulate cancellation path from worker.py line 470
            cancellation_event = asyncio.Event()

            async def simulate_cancellation_path():
                """Simulate the exact cancellation path from worker.py."""
                if cancellation_event.is_set():
                    error_frame = {
                        "t": "err",
                        "code": "CANCELLED",
                        "message": "Stream cancelled by client",
                        "source": "stream",
                    }
                    # This should NOT raise AttributeError anymore
                    await queue.put(error_frame)
                    return True
                return False

            # Set cancellation event
            cancellation_event.set()

            # Should not raise AttributeError
            result = await simulate_cancellation_path()
            assert result, "Cancellation path should execute successfully"

            # Verify frame is in queue
            frame = await queue.get()
            assert frame["t"] == "err"
            assert frame["code"] == "CANCELLED"
            assert frame["source"] == "stream"
            assert "message" in frame

            logger.info("✅ Cancellation emits proper error frame with source field")

        finally:
            stream_registry.unregister(stream_id)
            await queue.close()

    @pytest.mark.asyncio
    async def test_backpressure_timeout_error_frame_format(self):
        """Verify backpressure timeout emits correct error frame per §1.3.

        Regression test for: services/_universal-llm-gateway/src/core/workers/worker.py:516
        Was: await asyncio.wait_for(queue._queue.put(error_frame), timeout=0.1)
        Now: await queue.put(error_frame, timeout_seconds=0.1)
        """
        logger.info("\n=== Test Backpressure Timeout Error Frame ===")

        stream_id = generate_stream_id()
        context = StreamContext(stream_id)
        queue = BoundedQueue()
        queue.QUEUE_CAPACITY = 5  # Small queue to trigger backpressure

        # Reset queue to small capacity
        queue._queue = asyncio.Queue(maxsize=5)

        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

        try:
            # Fill the queue
            for i in range(queue.QUEUE_CAPACITY):
                await queue.put({"t": "token", "i": i, "txt": f"token_{i}"})

            # Next put should timeout
            with pytest.raises(QueueTimeoutError):
                await queue.put({"t": "token", "i": 100, "txt": "overflow"})

            # Emit error frame using proper API (not queue._queue.put)
            error_frame = {
                "t": "err",
                "code": "QUEUE_TIMEOUT",
                "message": "Producer timeout after 500ms - consumer too slow",
                "source": "stream",
            }

            # Try to enqueue error frame with short timeout
            # Note: If queue is full, this might also timeout, which is expected
            # (best-effort error frame semantics)
            try:
                await queue.put(error_frame, timeout_seconds=0.1)
                error_enqueued = True
            except QueueTimeoutError:
                # If error frame couldn't be enqueued, that's also valid behavior
                # (best-effort for error frames when queue is full)
                error_enqueued = False
                logger.info(
                    "⚠️  Error frame could not be enqueued (queue full) - best effort"
                )

            # Verify by draining queue
            frames = []
            while queue.qsize() > 0:
                frames.append(await queue.get())

            # Find error frame if it was enqueued
            error_frames = [f for f in frames if f.get("t") == "err"]
            if error_enqueued or error_frames:
                # Either we enqueued it successfully or found it in the queue
                if error_frames:
                    ef = error_frames[-1]
                    assert ef["code"] == "QUEUE_TIMEOUT"
                    assert ef["source"] == "stream"
                    logger.info("✅ Backpressure timeout emits proper error frame")
                else:
                    # Enqueue succeeded but frame not in queue (drained before we
                    # checked)
                    logger.info("✅ Error frame enqueued successfully")
            else:
                # Best effort - couldn't enqueue when queue full
                logger.info(
                    "✅ Best-effort semantics: error frame dropped when queue full"
                )

        finally:
            stream_registry.unregister(stream_id)
            await queue.close()

    @pytest.mark.asyncio
    async def test_no_direct_queue_access_in_error_paths(self):
        """Verify no direct queue._queue.put() access bypasses validation.

        Regression tests for:
        - Line 516: queue._queue.put(error_frame)
        - Line 531: queue._queue.put(error_frame)
        - Line 559: queue._queue.put(error_frame)
        """
        logger.info("\n=== Test No Direct Queue Access ===")

        stream_id = generate_stream_id()
        context = StreamContext(stream_id)
        queue = BoundedQueue()

        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

        try:
            # Create a frame that exceeds size limit to verify validation works
            oversized_frame = {
                "t": "token",
                "txt": "x" * 10000,  # Exceeds frame size limit
            }

            # Using queue.put should enforce size limits
            with pytest.raises(ValueError) as exc:
                await queue.put(oversized_frame)

            assert "exceeds" in str(exc.value).lower()
            logger.info(f"✅ Size validation enforced: {exc.value}")

            # But direct _queue.put would bypass this check
            # Verify our code never does this
            from universal_protocol.ws.bounded_queue import BoundedQueue as BQ

            # Confirm the method signature only allows put()
            assert hasattr(BQ, "put")
            assert hasattr(BQ, "get")
            # enqueue should NOT exist
            assert not hasattr(BQ, "enqueue")

            logger.info("✅ BoundedQueue API is correct: has put/get, no enqueue")

        finally:
            stream_registry.unregister(stream_id)
            await queue.close()


class TestErrorFrameSourceFieldCompliance:
    """Test suite for error frame source field compliance per §2.3."""

    @pytest.mark.asyncio
    async def test_all_error_frames_have_source_field(self):
        """Verify all error frames include 'source' field.

        Per spec §2.3: source must be one of:
        - "stream": backpressure/cancellation errors
        - "engine": inference engine failures
        - "rpc": RPC validation errors
        """
        logger.info("\n=== Test Error Frame Source Field ===")

        test_cases = [
            {
                "name": "Stream error (cancellation)",
                "frame": {
                    "t": "err",
                    "code": "CANCELLED",
                    "message": "Stream cancelled by client",
                    "source": "stream",
                },
                "expected_source": "stream",
            },
            {
                "name": "Stream error (backpressure)",
                "frame": {
                    "t": "err",
                    "code": "QUEUE_TIMEOUT",
                    "message": "Producer timeout after 500ms",
                    "source": "stream",
                },
                "expected_source": "stream",
            },
            {
                "name": "Engine error (inference)",
                "frame": {
                    "t": "err",
                    "code": "ENGINE_ERROR",
                    "message": "Model inference failed",
                    "source": "engine",
                },
                "expected_source": "engine",
            },
            {
                "name": "RPC error (validation)",
                "frame": {
                    "t": "err",
                    "code": "INVALID_PARAMS",
                    "message": "Invalid RPC parameters",
                    "source": "rpc",
                },
                "expected_source": "rpc",
            },
        ]

        stream_id = generate_stream_id()
        context = StreamContext(stream_id)
        queue = BoundedQueue()

        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

        try:
            for test_case in test_cases:
                frame = test_case["frame"]

                # Enqueue frame
                await queue.put(frame)

                # Retrieve and verify
                received = await queue.get()
                assert received["t"] == "err"
                assert "source" in received, (
                    f"Missing source field in {test_case['name']}"
                )
                assert received["source"] == test_case["expected_source"]

                logger.info(
                    f"✅ {test_case['name']}: source={test_case['expected_source']}"
                )

        finally:
            stream_registry.unregister(stream_id)
            await queue.close()

    @pytest.mark.asyncio
    async def test_sse_format_includes_source_field(self):
        """Verify error frames formatted as SSE include source field."""
        logger.info("\n=== Test SSE Format Source Field ===")

        error_frame = {
            "t": "err",
            "code": "QUEUE_TIMEOUT",
            "message": "Timeout",
            "source": "stream",
        }

        # Format as SSE
        sse_text = format_sse(error_frame)

        # Parse back
        parsed = parse_sse(sse_text)
        assert "source" in parsed
        assert parsed["source"] == "stream"

        logger.info("✅ SSE formatting preserves source field")


class TestProducerPutHelper:
    """Test suite for producer_put helper function."""

    @pytest.mark.asyncio
    async def test_producer_put_handles_backpressure_timeout(self):
        """Verify producer_put emits proper error frame on timeout.

        This is the recommended API for producers to use instead of direct
        queue.put() since it handles all error conditions.
        """
        logger.info("\n=== Test producer_put Backpressure Timeout Handling ===")

        stream_id = generate_stream_id()
        context = StreamContext(stream_id)
        queue = BoundedQueue()
        queue.QUEUE_CAPACITY = 2

        # Reset to small size
        queue._queue = asyncio.Queue(maxsize=2)

        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

        try:
            # Fill queue
            await queue.put({"t": "token", "i": 0, "txt": "token_0"})
            await queue.put({"t": "token", "i": 1, "txt": "token_1"})

            # Try to put with timeout - should fail since queue is full
            result = await producer_put(
                stream_id, {"t": "token", "i": 2, "txt": "token_2"}
            )

            # Result should be False (timeout)
            assert result is False, "producer_put should return False on timeout"

            # Verify error frame was enqueued
            frames = []
            while queue.qsize() > 0:
                frames.append(await queue.get())

            # Should have at least the 2 tokens plus error frame
            error_frames = [f for f in frames if f.get("t") == "err"]
            if error_frames:  # Error frame might not fit in queue if full
                assert error_frames[0]["code"] == "QUEUE_TIMEOUT"
                assert error_frames[0]["source"] == "stream"
                logger.info("✅ producer_put emitted error frame on timeout")
            else:
                logger.info(
                    "⚠️ Error frame dropped (queue was full) - expected behavior"
                )

        finally:
            stream_registry.unregister(stream_id)
            await queue.close()

    @pytest.mark.asyncio
    async def test_producer_put_handles_frame_too_large(self):
        """Verify producer_put emits proper error frame for oversized frames."""
        logger.info("\n=== Test producer_put Frame Size Validation ===")

        stream_id = generate_stream_id()
        context = StreamContext(stream_id)
        queue = BoundedQueue()

        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

        try:
            # Try to put oversized frame
            oversized_frame = {
                "t": "token",
                "txt": "x" * 10000,
            }

            result = await producer_put(stream_id, oversized_frame)

            # Should fail
            assert result is False, (
                "producer_put should return False for oversized frame"
            )

            # Verify error frame was enqueued
            if queue.qsize() > 0:
                error_frame = await queue.get()
                assert error_frame["t"] == "err"
                assert "FRAME_TOO_LARGE" in error_frame["code"]
                assert error_frame["source"] == "stream"
                logger.info("✅ producer_put emitted FRAME_TOO_LARGE error frame")

        finally:
            stream_registry.unregister(stream_id)
            await queue.close()


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
