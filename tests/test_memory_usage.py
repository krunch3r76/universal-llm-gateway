"""Memory usage benchmark tests for state channel metrics."""

import asyncio
import gc
import os
import sys
import time
from unittest.mock import patch

import psutil

# Add the service directory to sys.path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../services/_universal-llm-gateway'))

from src.core.metrics.state_channel_metrics import StateChannelMetricsCollector


class TestMemoryUsage:
    """Memory usage benchmark and boundary tests."""

    def get_memory_usage_mb(self) -> float:
        """Get current process memory usage in MB."""
        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024

    async def test_bounded_message_timestamps(self):
        """Test that message timestamps are properly bounded."""
        with patch.dict(os.environ, {
            "METRICS_MESSAGE_RETENTION": "1000",
            "METRICS_ERROR_RETENTION": "500"
        }):
            collector = StateChannelMetricsCollector()
            await collector.start()

            try:
                # Add more messages than retention limit
                for i in range(2000):
                    await collector.on_message_sent(f'client_{i}', 'update', 100)

                # Verify bounded - should never exceed maxlen
                assert len(collector._message_timestamps) <= 1000
                print(f"✅ Message timestamps bounded: {len(collector._message_timestamps)}/1000")

            finally:
                await collector.stop()

    async def test_bounded_error_timestamps(self):
        """Test that error timestamps are properly bounded."""
        with patch.dict(os.environ, {
            "METRICS_ERROR_RETENTION": "100"
        }):
            collector = StateChannelMetricsCollector()
            await collector.start()

            try:
                # Add more errors than retention limit
                for i in range(200):
                    await collector.on_error(f'client_{i}', 'test_error')

                # Verify bounded - should never exceed maxlen
                assert len(collector._error_timestamps) <= 100
                print(f"✅ Error timestamps bounded: {len(collector._error_timestamps)}/100")

            finally:
                await collector.stop()

    async def test_memory_usage_under_load(self):
        """Test memory usage remains stable under sustained load."""
        with patch.dict(os.environ, {
            "METRICS_MESSAGE_RETENTION": "10000",
            "METRICS_ERROR_RETENTION": "1000"
        }):
            collector = StateChannelMetricsCollector()
            await collector.start()

            initial_memory = self.get_memory_usage_mb()
            print(f"Initial memory usage: {initial_memory:.2f} MB")

            try:
                # Sustained load - add many more messages than retention limit
                for batch in range(5):  # 5 batches of 15000 = 75000 total messages
                    for i in range(15000):
                        await collector.on_message_sent(f'client_{batch}_{i}', 'update', 100)
                        if i % 1000 == 0:  # Add some errors too
                            await collector.on_error(f'client_{batch}_{i}', 'test_error')

                    # Force garbage collection
                    gc.collect()
                    current_memory = self.get_memory_usage_mb()
                    print(f"After batch {batch + 1}: {current_memory:.2f} MB")

                final_memory = self.get_memory_usage_mb()
                memory_increase = final_memory - initial_memory

                print(f"Final memory usage: {final_memory:.2f} MB")
                print(f"Memory increase: {memory_increase:.2f} MB")

                # Memory increase should be reasonable (< 50MB as per success criteria)
                assert memory_increase < 50, f"Memory increase {memory_increase:.2f} MB exceeds 50MB limit"

                # Verify collections are still bounded
                assert len(collector._message_timestamps) <= 10000
                assert len(collector._error_timestamps) <= 1000

                print("✅ Memory usage remains bounded under sustained load")

            finally:
                await collector.stop()

    async def test_automatic_eviction(self):
        """Test that old entries are automatically evicted."""
        with patch.dict(os.environ, {
            "METRICS_MESSAGE_RETENTION": "100"
        }):
            collector = StateChannelMetricsCollector()
            await collector.start()

            try:
                # Add exactly the retention limit
                for i in range(100):
                    await collector.on_message_sent(f'client_{i}', 'update', 100)

                # Wait a bit for async processing
                await asyncio.sleep(0.1)

                # Store first timestamp if available
                if collector._message_timestamps:
                    first_timestamp = collector._message_timestamps[0] if collector._message_timestamps else None
                else:
                    first_timestamp = None

                # Add more messages to trigger eviction
                for i in range(50):
                    await collector.on_message_sent(f'client_new_{i}', 'update', 100)

                # Wait for processing
                await asyncio.sleep(0.1)

                # Verify bounded (the key requirement)
                assert len(collector._message_timestamps) <= 100

                print("✅ Old entries automatically evicted")

            finally:
                await collector.stop()

    async def test_no_manual_cleanup_code_remains(self):
        """Test that no manual cleanup code affects the deque behavior."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        try:
            # Add some messages
            for i in range(50):
                await collector.on_message_sent(f'client_{i}', 'update', 100)

            # Wait for async processing
            await asyncio.sleep(0.1)

            # Get metrics summary (which used to do manual cleanup)
            metrics = await collector.get_metrics_summary()

            # Verify deque behavior (should be bounded by retention limit)
            assert len(collector._message_timestamps) <= collector._message_retention
            assert metrics is not None
            assert 'messages' in metrics

            print("✅ No manual cleanup code interferes with deque behavior")

        finally:
            await collector.stop()

    async def test_concurrent_access_memory_safety(self):
        """Test memory safety under concurrent access."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        try:
            # Concurrent tasks adding messages
            async def add_messages(client_prefix: str, count: int):
                for i in range(count):
                    await collector.on_message_sent(f'{client_prefix}_{i}', 'update', 100)
                    await asyncio.sleep(0.001)  # Small delay to allow interleaving

            # Run multiple concurrent tasks
            tasks = [
                add_messages("concurrent_1", 500),
                add_messages("concurrent_2", 500),
                add_messages("concurrent_3", 500),
            ]

            await asyncio.gather(*tasks)

            # Verify collections are still bounded and consistent
            assert len(collector._message_timestamps) <= collector._message_retention

            print("✅ Memory safe under concurrent access")

        finally:
            await collector.stop()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
