"""
Large payload tests for universal_transport.

These tests verify that the length-prefixed protocol can handle
multi-MB messages that would fail with asyncio readline buffer limits.

Key test scenarios:
- 1MB messages (common large data)
- 10MB messages (would likely fail with readline)
- 100MB messages (would definitely fail with readline)
- Stress testing with multiple large messages
- Performance benchmarking
"""

import asyncio
import logging
import random
import string
import time
from pathlib import Path
from typing import Any

import pytest

from universal_transport.core.client_server.async_client import create_unix_client
from universal_transport.core.client_server.async_server import create_unix_server
from universal_transport.core.protocol.serializers import (
    JSONSerializer,
    MessagePackSerializer,
)

# Configure logging for tests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_large_data(size_mb: float) -> str:
    """Create large string data of specified size in MB."""
    target_size = int(size_mb * 1024 * 1024)  # Convert MB to bytes

    # Use a repeating pattern to generate data efficiently
    pattern = "".join(random.choices(string.ascii_letters + string.digits, k=1024))

    # Calculate how many full patterns we need
    full_patterns = target_size // len(pattern.encode("utf-8"))
    remainder = target_size % len(pattern.encode("utf-8"))

    # Generate the data
    data = pattern * full_patterns
    if remainder > 0:
        data += pattern[:remainder]

    return data


def create_large_message(size_mb: float) -> dict[str, Any]:
    """Create a large test message of specified size."""
    large_data = create_large_data(size_mb)

    return {
        "type": "large_payload_test",
        "size_mb": size_mb,
        "timestamp": time.time(),
        "data": large_data,
        "metadata": {
            "actual_size_bytes": len(large_data.encode("utf-8")),
            "checksum": hash(large_data) % (2**32),  # Simple checksum
        },
    }


async def echo_handler(message: dict[str, Any], session) -> dict[str, Any]:
    """Echo handler that adds response metadata."""
    return {
        "type": "echo_response",
        "original_message": message,
        "server_timestamp": time.time(),
        "client_id": session.client_id,
    }


class TestLargePayloads:
    """Test class for large payload handling."""

    @pytest.fixture
    async def server_client_pair(self, tmp_path):
        """Create server-client pair for testing."""
        socket_path = str(tmp_path / "test.sock")

        # Create server
        server = await create_unix_server(
            socket_path=socket_path,
            message_handler=echo_handler,
            serializer=JSONSerializer(),
            max_message_size=200 * 1024 * 1024,  # 200MB limit
            max_clients=5,
        )

        # Start server
        await server.start()

        # Create client
        client = await create_unix_client(
            socket_path=socket_path,
            serializer=JSONSerializer(),
            max_message_size=200 * 1024 * 1024,  # 200MB limit
        )

        await client.connect()

        yield server, client

        # Cleanup
        await client.close()
        await server.stop()

    @pytest.mark.asyncio
    async def test_1mb_payload(self, server_client_pair):
        """Test 1MB payload handling."""
        server, client = server_client_pair

        logger.info("Testing 1MB payload...")

        # Create 1MB message
        large_message = create_large_message(1.0)

        # Send and receive
        start_time = time.time()
        response = await client.request_response(large_message, timeout=10.0)
        duration = time.time() - start_time

        # Verify response
        assert response["type"] == "echo_response"
        assert response["original_message"] == large_message

        logger.info(f"1MB payload test completed in {duration:.2f}s")

    @pytest.mark.asyncio
    async def test_10mb_payload(self, server_client_pair):
        """Test 10MB payload handling (would fail with readline)."""
        server, client = server_client_pair

        logger.info("Testing 10MB payload (would fail with readline)...")

        # Create 10MB message
        large_message = create_large_message(10.0)

        # Send and receive
        start_time = time.time()
        response = await client.request_response(large_message, timeout=30.0)
        duration = time.time() - start_time

        # Verify response
        assert response["type"] == "echo_response"
        assert response["original_message"] == large_message

        logger.info(f"10MB payload test completed in {duration:.2f}s")

    @pytest.mark.asyncio
    async def test_100mb_payload(self, server_client_pair):
        """Test 100MB payload handling (would definitely fail with readline)."""
        server, client = server_client_pair

        logger.info("Testing 100MB payload (would definitely fail with readline)...")

        # Create 100MB message
        large_message = create_large_message(100.0)

        # Send and receive with generous timeout
        start_time = time.time()
        response = await client.request_response(large_message, timeout=120.0)
        duration = time.time() - start_time

        # Verify response
        assert response["type"] == "echo_response"
        assert response["original_message"] == large_message

        logger.info(f"100MB payload test completed in {duration:.2f}s")

    @pytest.mark.asyncio
    async def test_multiple_large_payloads(self, server_client_pair):
        """Test multiple large payloads in sequence."""
        server, client = server_client_pair

        logger.info("Testing multiple large payloads...")

        sizes = [1.0, 5.0, 10.0, 5.0, 1.0]  # Mix of sizes
        total_start = time.time()

        for i, size_mb in enumerate(sizes):
            logger.info(f"Sending payload {i + 1}/{len(sizes)}: {size_mb}MB")

            message = create_large_message(size_mb)

            start_time = time.time()
            response = await client.request_response(message, timeout=60.0)
            duration = time.time() - start_time

            # Verify response
            assert response["type"] == "echo_response"
            assert response["original_message"] == message

            logger.info(f"Payload {i + 1} completed in {duration:.2f}s")

        total_duration = time.time() - total_start
        logger.info(f"All {len(sizes)} payloads completed in {total_duration:.2f}s")

    @pytest.mark.asyncio
    async def test_concurrent_large_payloads(self, tmp_path):
        """Test concurrent large payload handling with multiple clients."""
        socket_path = str(tmp_path / "concurrent_test.sock")

        # Create server
        server = await create_unix_server(
            socket_path=socket_path,
            message_handler=echo_handler,
            serializer=JSONSerializer(),
            max_message_size=50 * 1024 * 1024,  # 50MB limit
            max_clients=5,
        )

        await server.start()

        async def client_task(client_id: int, size_mb: float):
            """Task for individual client."""
            client = await create_unix_client(
                socket_path=socket_path,
                serializer=JSONSerializer(),
                max_message_size=50 * 1024 * 1024,
            )

            try:
                await client.connect()

                message = create_large_message(size_mb)
                message["client_id"] = client_id

                start_time = time.time()
                response = await client.request_response(message, timeout=60.0)
                duration = time.time() - start_time

                assert response["type"] == "echo_response"
                assert response["original_message"]["client_id"] == client_id

                logger.info(
                    f"Client {client_id} ({size_mb}MB) completed in {duration:.2f}s"
                )

                return client_id, duration

            finally:
                await client.close()

        # Launch multiple concurrent clients
        tasks = [
            client_task(1, 5.0),
            client_task(2, 10.0),
            client_task(3, 5.0),
            client_task(4, 15.0),
        ]

        logger.info("Starting 4 concurrent clients with large payloads...")
        start_time = time.time()

        results = await asyncio.gather(*tasks)

        total_duration = time.time() - start_time
        logger.info(f"All concurrent clients completed in {total_duration:.2f}s")

        # Verify all clients completed
        assert len(results) == 4

        await server.stop()

    @pytest.mark.asyncio
    async def test_payload_size_limits(self, server_client_pair):
        """Test payload size limit enforcement."""
        server, client = server_client_pair

        logger.info("Testing payload size limits...")

        # Try to send a message larger than the configured limit
        # Server is configured for 200MB, so try 250MB
        oversized_message = create_large_message(250.0)

        # This should fail due to size limits
        with pytest.raises(Exception):  # Should raise some exception
            await client.request_response(oversized_message, timeout=30.0)

        logger.info("Payload size limit enforcement working correctly")

    @pytest.mark.asyncio
    async def test_messagepack_large_payload(self, tmp_path):
        """Test large payload with MessagePack serialization."""
        try:
            msgpack_serializer = MessagePackSerializer()
        except ImportError:
            pytest.skip("MessagePack not available")

        socket_path = str(tmp_path / "msgpack_test.sock")

        # Create server with MessagePack
        server = await create_unix_server(
            socket_path=socket_path,
            message_handler=echo_handler,
            serializer=msgpack_serializer,
            max_message_size=50 * 1024 * 1024,
        )

        await server.start()

        # Create client with MessagePack
        client = await create_unix_client(
            socket_path=socket_path,
            serializer=msgpack_serializer,
            max_message_size=50 * 1024 * 1024,
        )

        try:
            await client.connect()

            # Test with 10MB payload using MessagePack
            large_message = create_large_message(10.0)

            start_time = time.time()
            response = await client.request_response(large_message, timeout=30.0)
            duration = time.time() - start_time

            # Verify response
            assert response["type"] == "echo_response"
            assert response["original_message"] == large_message

            logger.info(f"10MB MessagePack payload completed in {duration:.2f}s")

        finally:
            await client.close()
            await server.stop()


@pytest.mark.asyncio
async def test_performance_benchmark():
    """Benchmark performance with different payload sizes."""
    socket_path = "/tmp/performance_test.sock"

    # Clean up any existing socket
    socket_file = Path(socket_path)
    if socket_file.exists():
        socket_file.unlink()

    # Create server
    server = await create_unix_server(
        socket_path=socket_path,
        message_handler=echo_handler,
        serializer=JSONSerializer(),
        max_message_size=200 * 1024 * 1024,
    )

    await server.start()

    # Create client
    client = await create_unix_client(
        socket_path=socket_path,
        serializer=JSONSerializer(),
        max_message_size=200 * 1024 * 1024,
    )

    try:
        await client.connect()

        sizes = [0.1, 0.5, 1.0, 5.0, 10.0, 25.0]  # MB
        results = []

        for size_mb in sizes:
            logger.info(f"Benchmarking {size_mb}MB payload...")

            message = create_large_message(size_mb)

            # Multiple iterations for averaging
            times = []
            for _ in range(3):
                start_time = time.time()
                response = await client.request_response(message, timeout=60.0)
                duration = time.time() - start_time
                times.append(duration)

                assert response["type"] == "echo_response"

            avg_time = sum(times) / len(times)
            throughput_mbps = (size_mb * 2) / avg_time  # *2 for round-trip

            results.append(
                {
                    "size_mb": size_mb,
                    "avg_time_s": avg_time,
                    "throughput_mbps": throughput_mbps,
                }
            )

            logger.info(
                f"{size_mb}MB: {avg_time:.2f}s avg, {throughput_mbps:.1f} MB/s throughput"
            )

        # Print summary
        logger.info("\nPerformance Summary:")
        logger.info("Size (MB) | Avg Time (s) | Throughput (MB/s)")
        logger.info("-" * 45)
        for result in results:
            logger.info(
                f"{result['size_mb']:8.1f} | {result['avg_time_s']:11.2f} | {result['throughput_mbps']:13.1f}"
            )

    finally:
        await client.close()
        await server.stop()

        # Clean up
        if socket_file.exists():
            socket_file.unlink()


if __name__ == "__main__":
    # Run tests directly

    print("=" * 60)
    print("LARGE PAYLOAD TESTS")
    print("=" * 60)
    print()
    print("These tests verify that length-prefixed protocol")
    print("can handle multi-MB messages that would fail with")
    print("asyncio readline buffer limits.")
    print()

    # Run performance benchmark
    asyncio.run(test_performance_benchmark())
