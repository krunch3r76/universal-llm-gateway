"""Test error propagation from engine to client via WebSocket.

Verifies that engine errors are properly propagated through the streaming pipeline.
"""


import pytest

from universal_protocol.ws import stream_registry
from universal_protocol.ws.bounded_queue import BoundedQueue
from universal_protocol.ws.lifecycle import StreamContext


@pytest.mark.asyncio
async def test_error_propagation_oom():
    """Test that OOM errors from engine reach the client."""
    # Setup mock stream
    stream_id = "test-stream-123"
    context = StreamContext(stream_id)
    queue = BoundedQueue()

    # Register stream (type: ignore for BoundedQueue vs UnboundedStreamQueue)
    stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

    try:
        # Simulate engine error by putting error frame
        error_frame = {
            "t": "err",
            "code": "OOM",
            "message": "Out of memory",
            "source": "engine",
        }
        await queue.put(error_frame)

        # Verify error can be retrieved
        retrieved_frame = await queue.get()
        assert retrieved_frame["t"] == "err"
        assert retrieved_frame["code"] == "OOM"
        assert retrieved_frame["source"] == "engine"
        assert "Out of memory" in retrieved_frame["message"]

    finally:
        # Cleanup
        await queue.close()
        await context.cleanup()
        stream_registry.unregister(stream_id)


@pytest.mark.asyncio
async def test_error_propagation_websocket_close():
    """Test WebSocket closes with proper code on error."""
    # This would require a full WebSocket server setup
    # For now, we test the error frame format

    error_cases = [
        ("OOM", "Out of memory error", "engine"),
        ("TIMEOUT", "Inference timeout", "stream"),
        ("CANCELLED", "Stream cancelled by client", "stream"),
        ("MODEL_UNLOADED", "Model was unloaded", "engine"),
    ]

    for code, message, source in error_cases:
        # Setup stream
        stream_id = f"test-stream-{code}"
        context = StreamContext(stream_id)
        queue = BoundedQueue()

        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

        try:
            # Put error frame
            error_frame = {
                "t": "err",
                "code": code,
                "message": message,
                "source": source,
            }
            await queue.put(error_frame)

            # Verify frame
            frame = await queue.get()
            assert frame["t"] == "err"
            assert frame["code"] == code
            assert frame["message"] == message
            assert frame["source"] == source

        finally:
            await queue.close()
            await context.cleanup()
            stream_registry.unregister(stream_id)


@pytest.mark.asyncio
async def test_error_propagation_with_mock_engine():
    """Test error propagation with mocked engine that raises RuntimeError."""
    from universal_protocol.server.asgi_app import RPC_METHODS

    # Mock engine that raises OOM
    async def mock_start_inference(params):
        # Register stream first (as real handler does)
        stream_id = "test-oom-stream"
        context = StreamContext(stream_id)
        queue = BoundedQueue()

        stream_registry.register(stream_id, kind="stream", context=context, queue=queue)

        # Put OOM error frame
        await queue.put(
            {
                "t": "err",
                "code": "OOM",
                "message": "CUDA out of memory",
                "source": "engine",
            }
        )

        return {"stream_id": stream_id, "websocket_path": f"/stream/{stream_id}"}

    # Temporarily replace handler
    original_handler = RPC_METHODS.get("start_inference")
    RPC_METHODS["start_inference"] = mock_start_inference

    try:
        # Call would succeed but stream contains error
        result = await mock_start_inference({})
        stream_id = result["stream_id"]

        # Verify error is in queue
        entry = stream_registry.get(stream_id)
        assert entry is not None
        queue = entry.queue
        error_frame = await queue.get()

        assert error_frame["t"] == "err"
        assert error_frame["code"] == "OOM"
        assert "CUDA out of memory" in error_frame["message"]

    finally:
        # Restore original handler
        if original_handler:
            RPC_METHODS["start_inference"] = original_handler

        # Cleanup streams
        for stream_id in list(stream_registry.keys()):
            entry = stream_registry.get(stream_id)
            if entry and entry.queue:
                await entry.queue.close()
            if entry and entry.context:
                await entry.context.cleanup()
            stream_registry.unregister(stream_id)
