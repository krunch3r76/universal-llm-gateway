"""Test concurrent streaming to verify stream independence.

Verifies that multiple concurrent streams work independently without interference.
"""

import asyncio

import pytest

from universal_protocol.ids import generate_stream_id
from universal_protocol.ws import stream_registry
from universal_protocol.ws.bounded_queue import BoundedQueue
from universal_protocol.ws.lifecycle import StreamContext


async def simulate_stream(stream_num: int, chunk_count: int = 10):
    """Simulate a streaming inference with given number of chunks."""
    stream_id = generate_stream_id()
    context = StreamContext(stream_id)
    queue = BoundedQueue()

    # Register stream (type: ignore for BoundedQueue vs UnboundedStreamQueue)
    stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

    try:
        # Producer task - simulates inference engine
        async def producer():
            for i in range(chunk_count):
                chunk = {
                    "t": "token",
                    "i": i,
                    "txt": f"Stream {stream_num} chunk {i}",
                }
                await queue.put(chunk)
                # Small delay to simulate generation time
                await asyncio.sleep(0.01)

            # Send done frame
            await queue.put(
                {
                    "t": "done",
                    "usage": {"input_tokens": 10, "output_tokens": chunk_count},
                }
            )

        # Consumer task - simulates WebSocket client
        async def consumer():
            chunks_received = []
            while True:
                frame = await queue.get()
                if frame["t"] == "done":
                    break
                chunks_received.append(frame)
            return chunks_received

        # Run producer and consumer concurrently
        producer_task = asyncio.create_task(producer())
        consumer_task = asyncio.create_task(consumer())

        # Wait for both
        await producer_task
        chunks = await consumer_task

        return stream_id, chunks

    except Exception:
        # Cleanup on error
        await queue.close()
        await context.cleanup()
        stream_registry.unregister(stream_id)
        raise


@pytest.mark.asyncio
async def test_concurrent_streams():
    """Test 5 concurrent streams work independently."""
    # Start 5 concurrent streams
    stream_tasks = []
    for i in range(5):
        task = asyncio.create_task(simulate_stream(i, chunk_count=20))
        stream_tasks.append(task)

    # Wait for all streams to complete
    results = await asyncio.gather(*stream_tasks)

    # Verify results
    assert len(results) == 5

    stream_ids = []
    for i, (stream_id, chunks) in enumerate(results):
        # Verify we got all chunks
        assert len(chunks) == 20

        # Verify chunks are for the right stream
        for j, chunk in enumerate(chunks):
            assert chunk["t"] == "token"
            assert chunk["i"] == j
            assert f"Stream {i}" in chunk["txt"]

        stream_ids.append(stream_id)

    # Verify all stream IDs are unique
    assert len(set(stream_ids)) == 5

    # Verify cleanup - all streams should be removed
    for stream_id, _ in results:
        if stream_id in stream_registry:
            # Clean up if not already done
            entry = stream_registry.get(stream_id)
            if entry and entry.queue:
                await entry.queue.close()
            if entry and entry.context:
                await entry.context.cleanup()
            stream_registry.unregister(stream_id)

    assert len(stream_registry) == 0


@pytest.mark.asyncio
async def test_concurrent_streams_with_errors():
    """Test concurrent streams with some experiencing errors."""

    async def simulate_stream_with_error(stream_num: int, should_error: bool):
        """Simulate stream that may error halfway through."""
        stream_id = generate_stream_id()
        context = StreamContext(stream_id)
        queue = BoundedQueue()

        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

        try:
            # Send some chunks
            for i in range(5):
                chunk = {
                    "t": "token",
                    "i": i,
                    "txt": f"Stream {stream_num} chunk {i}",
                }
                await queue.put(chunk)

            if should_error:
                # Send error frame
                await queue.put(
                    {
                        "t": "err",
                        "code": "TEST_ERROR",
                        "message": f"Stream {stream_num} errored",
                        "source": "test",
                    }
                )
            else:
                # Send more chunks and done
                for i in range(5, 10):
                    chunk = {
                        "t": "token",
                        "i": i,
                        "txt": f"Stream {stream_num} chunk {i}",
                    }
                    await queue.put(chunk)

                await queue.put(
                    {"t": "done", "usage": {"input_tokens": 10, "output_tokens": 10}}
                )

            # Consume all frames
            frames = []
            while True:
                frame = await queue.get()
                frames.append(frame)
                if frame["t"] in ("done", "err"):
                    break

            return stream_id, frames, should_error

        finally:
            await queue.close()
            await context.cleanup()
            stream_registry.unregister(stream_id)

    # Run 5 streams, alternating between success and error
    tasks = []
    for i in range(5):
        should_error = i % 2 == 1  # Streams 1, 3 will error
        task = asyncio.create_task(simulate_stream_with_error(i, should_error))
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    # Verify results
    for i, (stream_id, frames, should_error) in enumerate(results):
        if should_error:
            # Should have 5 chunks + 1 error
            assert len(frames) == 6
            assert frames[-1]["t"] == "err"
            assert frames[-1]["code"] == "TEST_ERROR"
        else:
            # Should have 10 chunks + 1 done
            assert len(frames) == 11
            assert frames[-1]["t"] == "done"

    # Verify no streams remain
    assert len(stream_registry) == 0
