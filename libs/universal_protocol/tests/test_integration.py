#!/usr/bin/env python3
"""Integration test for Universal Protocol MVP.

Tests the complete flow:
1. Start a worker process with Universal Protocol server
2. Use AsyncRPCClient to call health
3. Load a model (mock)
4. Start inference and get stream_id
5. Connect WebSocket client to stream
6. Read a few tokens
7. Verify cleanup after disconnect

This test simulates the full integration between process_ipc and
universal_protocol layers after MessagePump removal.
"""

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

import pytest

# Add libs to Python path if not already there
libs_path = Path(__file__).resolve().parents[3]
if str(libs_path) not in sys.path:
    sys.path.insert(0, str(libs_path))

from universal_protocol.rpc.client import AsyncRPCClient  # noqa: E402
from universal_protocol.ws.client import StreamClient  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class WorkerProcess:
    """Manages a worker process for testing."""

    def __init__(self, worker_id: str = "test-1"):
        self.worker_id = worker_id
        self.socket_path = f"/tmp/universal-protocol/worker-{worker_id}.sock"
        self.process: subprocess.Popen | None = None

    async def start(self) -> None:
        """Start the worker process."""
        # Ensure socket directory exists
        socket_dir = Path(self.socket_path).parent
        socket_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale socket if exists
        if Path(self.socket_path).exists():
            Path(self.socket_path).unlink()

        # Start worker subprocess
        cmd = [
            sys.executable,
            "-c",
            f"""
import asyncio
import sys
sys.path.insert(0, '{libs_path}')
from universal_protocol.server import serve, app

async def main():
    await serve(app=app, socket_path='{self.socket_path}', loop="uvloop", log_level="info")

if __name__ == "__main__":
    asyncio.run(main())
""",
        ]

        logger.info(f"Starting worker process {self.worker_id}")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for socket to be ready
        for _ in range(50):  # 5 seconds max
            if Path(self.socket_path).exists():
                logger.info(f"Worker socket ready: {self.socket_path}")
                break
            await asyncio.sleep(0.1)
        else:
            raise TimeoutError(
                f"Worker socket not ready after 5 seconds: {self.socket_path}"
            )

    async def stop(self) -> None:
        """Stop the worker process."""
        if self.process:
            logger.info(f"Stopping worker process {self.worker_id}")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Worker didn't terminate, killing...")
                self.process.kill()
                self.process.wait()

            # Clean up socket
            if Path(self.socket_path).exists():
                Path(self.socket_path).unlink()


@pytest.mark.asyncio
async def test_full_integration():
    """Test the complete Universal Protocol integration flow."""
    worker = WorkerProcess()

    try:
        # 1. Start worker process
        logger.info("=== Step 1: Starting worker process ===")
        await worker.start()

        # 2. Create RPC client and test health
        logger.info("=== Step 2: Testing RPC health check ===")
        rpc_client = AsyncRPCClient(socket_path=worker.socket_path)

        health_result = await rpc_client.call_rpc("health", {})
        logger.info(f"Health check result: {health_result}")
        assert health_result["status"] == "ready"
        assert isinstance(health_result["models"], list)

        # 3. Load a model (mock)
        logger.info("=== Step 3: Loading model (mock) ===")
        load_result = await rpc_client.call_rpc(
            "load_model", {"name": "test-model", "path": "/fake/path/model.gguf"}
        )
        logger.info(f"Load model result: {load_result}")
        assert load_result["success"] is True
        assert load_result["model_loaded"] is True
        assert "context_size" in load_result

        # 4. Count tokens
        logger.info("=== Step 4: Testing token counting ===")
        count_result = await rpc_client.call_rpc(
            "count_tokens",
            {"text": "Hello, this is a test of the Universal Protocol integration."},
        )
        logger.info(f"Token count result: {count_result}")
        assert "count" in count_result
        assert count_result["count"] > 0

        # 5. Start inference and get stream_id
        logger.info("=== Step 5: Starting inference ===")
        inference_result = await rpc_client.call_rpc(
            "start_inference",
            {"prompt": "Once upon a time", "max_tokens": 50, "temperature": 0.7},
        )
        logger.info(f"Start inference result: {inference_result}")
        assert "stream_id" in inference_result
        assert "websocket_path" in inference_result

        stream_id = inference_result["stream_id"]
        ws_path = inference_result["websocket_path"]

        # 6. Connect WebSocket client
        logger.info("=== Step 6: Connecting WebSocket client ===")

        # 7. Read a few tokens
        logger.info("=== Step 7: Reading tokens from stream ===")
        tokens_received = 0
        max_tokens = 5

        async with StreamClient(worker.socket_path, stream_id) as ws_client:
            async for message in ws_client.iter_messages():
                if message["t"] == "token":
                    token = message.get("txt", "")
                    logger.info(f"Received token: {repr(token)}")
                    tokens_received += 1

                    if tokens_received >= max_tokens:
                        logger.info(f"Received {max_tokens} tokens, closing connection")
                        break

                elif message["t"] == "done":
                    logger.info(f"Stream completed: {message.get('usage', {})}")
                    break
                elif message["t"] == "err":
                    logger.error(f"Stream error: {message}")
                    break

        # 8. Disconnect and verify cleanup
        logger.info("=== Step 8: Testing cleanup ===")
        # ws_client is automatically closed by the context manager

        # Get debug stats to verify cleanup
        stats_result = await rpc_client.call_rpc("debug_stats", {})
        logger.info(f"Debug stats after cleanup: {stats_result}")

        # 9. Cancel inference (even though it may be done)
        logger.info("=== Step 9: Testing inference cancellation ===")
        cancel_result = await rpc_client.call_rpc(
            "cancel_inference", {"stream_id": stream_id}
        )
        logger.info(f"Cancel result: {cancel_result}")
        assert cancel_result["success"] is True

        # 10. Unload model
        logger.info("=== Step 10: Unloading model ===")
        unload_result = await rpc_client.call_rpc(
            "unload_model", {"name": "test-model"}
        )
        logger.info(f"Unload result: {unload_result}")
        assert unload_result["success"] is True

        # Close RPC client
        await rpc_client.close()

        logger.info("=== Integration test completed successfully! ===")

    finally:
        # Always stop the worker
        await worker.stop()


@pytest.mark.asyncio
async def test_multiple_clients():
    """Test multiple concurrent RPC clients."""
    worker = WorkerProcess()

    try:
        await worker.start()

        # Create multiple RPC clients
        clients = []
        for i in range(3):
            client = AsyncRPCClient(socket_path=worker.socket_path)
            clients.append(client)

        # Make concurrent requests
        logger.info("Testing concurrent RPC requests from multiple clients")
        tasks = []
        for i, client in enumerate(clients):
            task = client.call_rpc("health", {})
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        for i, result in enumerate(results):
            logger.info(f"Client {i} health result: {result}")
            assert result["status"] == "ready"

        # Close all clients
        for client in clients:
            await client.close()

        logger.info("Multiple client test completed successfully!")

    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_error_handling():
    """Test RPC error handling."""
    worker = WorkerProcess()

    try:
        await worker.start()

        rpc_client = AsyncRPCClient(socket_path=worker.socket_path)

        # Test invalid method
        logger.info("Testing invalid RPC method")
        try:
            await rpc_client.call_rpc("invalid_method", {})
            assert False, "Should have raised an error"
        except Exception as e:
            logger.info(f"Expected error for invalid method: {e}")

        # Test missing required parameters
        logger.info("Testing missing required parameters")
        try:
            await rpc_client.call_rpc("load_model", {})  # Missing name and path
            assert False, "Should have raised an error"
        except Exception as e:
            logger.info(f"Expected error for missing params: {e}")

        # Test invalid parameter types
        logger.info("Testing invalid parameter types")
        try:
            await rpc_client.call_rpc(
                "count_tokens", {"text": 12345}
            )  # Should be string
            assert False, "Should have raised an error"
        except Exception as e:
            logger.info(f"Expected error for invalid type: {e}")

        await rpc_client.close()
        logger.info("Error handling test completed successfully!")

    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_chat_style_inference():
    """Test start_inference with chat-style messages parameter."""
    logger.info("\n=== Testing Chat-Style Inference ===")

    worker = WorkerProcess("test-chat")
    await worker.start()

    try:
        # Give server time to start
        await asyncio.sleep(1)

        # Create RPC client
        rpc_client = AsyncRPCClient(worker.socket_path)

        # Test health check
        health = await rpc_client.call_rpc("health", {})
        logger.info(f"Health check: {health}")
        assert health["status"] == "ready"

        # Load model (mock)
        load_result = await rpc_client.call_rpc(
            "load_model",
            {
                "name": "test-model",
                "path": "/fake/path",
            },
        )
        logger.info(f"Load model result: {load_result}")
        assert load_result["success"] is True

        # Test with prompt (existing functionality)
        logger.info("Testing inference with prompt")
        prompt_result = await rpc_client.call_rpc(
            "start_inference",
            {
                "prompt": "Hello, world!",
                "max_tokens": 50,
                "temperature": 0.7,
            },
        )
        logger.info(f"Prompt inference result: {prompt_result}")
        assert "stream_id" in prompt_result
        assert prompt_result["stream_id"].startswith("stream-")

        # Test with messages (new chat-style functionality)
        logger.info("Testing inference with messages")
        messages_result = await rpc_client.call_rpc(
            "start_inference",
            {
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is 2+2?"},
                ],
                "max_tokens": 50,
                "temperature": 0.7,
            },
        )
        logger.info(f"Messages inference result: {messages_result}")
        assert "stream_id" in messages_result
        assert messages_result["stream_id"].startswith("stream-")

        # Test error: both prompt and messages provided
        logger.info("Testing error: both prompt and messages")
        try:
            await rpc_client.call_rpc(
                "start_inference",
                {
                    "prompt": "Hello",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 50,
                },
            )
            assert False, "Should have raised an error"
        except Exception as e:
            logger.info(f"Expected error for both params: {e}")
            assert "Cannot provide both prompt and messages" in str(e)

        # Test error: neither prompt nor messages
        logger.info("Testing error: neither prompt nor messages")
        try:
            await rpc_client.call_rpc(
                "start_inference",
                {
                    "max_tokens": 50,
                    "temperature": 0.7,
                },
            )
            assert False, "Should have raised an error"
        except Exception as e:
            logger.info(f"Expected error for missing params: {e}")
            assert "Either prompt or messages is required" in str(e)

        # Test error: invalid messages format
        logger.info("Testing error: invalid messages format")
        try:
            await rpc_client.call_rpc(
                "start_inference",
                {
                    "messages": "not a list",
                    "max_tokens": 50,
                },
            )
            assert False, "Should have raised an error"
        except Exception as e:
            logger.info(f"Expected error for invalid messages: {e}")
            assert "messages must be a non-empty array" in str(e)

        # Test error: empty messages array
        try:
            await rpc_client.call_rpc(
                "start_inference",
                {
                    "messages": [],
                    "max_tokens": 50,
                },
            )
            assert False, "Should have raised an error"
        except Exception as e:
            logger.info(f"Expected error for empty messages: {e}")
            assert "messages must be a non-empty array" in str(e)

        # Test error: invalid message format
        try:
            await rpc_client.call_rpc(
                "start_inference",
                {
                    "messages": [{"invalid": "format"}],
                    "max_tokens": 50,
                },
            )
            assert False, "Should have raised an error"
        except Exception as e:
            logger.info(f"Expected error for invalid message format: {e}")
            assert "must have 'role' and 'content' fields" in str(e)

        await rpc_client.close()
        logger.info("Chat-style inference test completed successfully!")

    finally:
        await worker.stop()


async def main():
    """Run all integration tests."""
    logger.info("Starting Universal Protocol integration tests")

    # Run tests sequentially to avoid port conflicts
    tests = [
        ("Full Integration", test_full_integration),
        ("Multiple Clients", test_multiple_clients),
        ("Error Handling", test_error_handling),
        ("Chat-Style Inference", test_chat_style_inference),
    ]

    for test_name, test_func in tests:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Running test: {test_name}")
        logger.info(f"{'=' * 60}")

        try:
            await test_func()
            logger.info(f"✅ {test_name} PASSED")
        except Exception as e:
            logger.error(f"❌ {test_name} FAILED: {e}", exc_info=True)
            return 1

    logger.info("\n✅ All integration tests passed!")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
