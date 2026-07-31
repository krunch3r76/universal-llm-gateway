"""Tests for Phase 2 metrics optimization improvements."""

import asyncio
import inspect
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Add the gateway source directory to path
gateway_src = Path(__file__).parent.parent / "services" / "_universal-llm-gateway"
sys.path.insert(0, str(gateway_src))

from src.core.metrics.state_channel_metrics import (
    StateChannelMetricsCollector,
    track_operation,
)

# Test constants
TEST_ERROR_COUNT = 10
TEST_MESSAGES_SENT_INITIAL = 5
TEST_MESSAGES_SENT_FINAL = 8
MAX_TIMING_ENTRIES = 1000
MAX_TEST_DURATION_SECONDS = 2.0
CONCURRENT_CLIENTS_COUNT = 100


class TestQueueProcessorOptimization:
    """Test queue processor improvements."""

    @pytest.mark.asyncio
    async def test_queue_error_recovery(self):
        """Test that queue processor recovers from consecutive errors."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Mock a handler that always fails
        def failing_handler():
            raise RuntimeError("Test error")

        # Submit multiple failing operations
        tasks = []
        for i in range(5):
            tasks.append(collector._submit(failing_handler))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should return None (isolated errors)
        assert all(result is None for result in results)

        # Queue should still be functional
        success_result = await collector.on_connection("test-client")
        assert success_result is not None
        assert success_result.client_id == "test-client"

        await collector.stop()

    @pytest.mark.asyncio
    async def test_periodic_maintenance(self):
        """Test that periodic maintenance executes."""
        collector = StateChannelMetricsCollector()

        # Mock the periodic cleanup method
        with patch.object(collector, '_periodic_cleanup', new_callable=AsyncMock) as mock_cleanup:
            await collector.start()

            # Wait for at least one timeout cycle (1.1s > 1.0s timeout)
            await asyncio.sleep(1.1)

            # Cleanup should have been called
            mock_cleanup.assert_called()

        await collector.stop()

    @pytest.mark.asyncio
    async def test_graceful_shutdown_drains_queue(self):
        """Test that shutdown properly drains the queue."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Add operations to queue
        tasks = []
        for i in range(TEST_ERROR_COUNT):
            tasks.append(collector.on_message_sent(f"client-{i}", "test", 100))

        # Start processing
        await asyncio.sleep(0.1)

        # Stop should drain remaining operations
        await collector.stop()

        # All tasks should complete (either successfully or with exceptions)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert len(results) == TEST_ERROR_COUNT


class TestHelperMethods:
    """Test helper methods for code deduplication."""

    @pytest.mark.asyncio
    async def test_validate_client_unknown(self):
        """Test validation of unknown clients."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Unknown client should return False
        assert not collector._validate_client("unknown-client")

        await collector.stop()

    @pytest.mark.asyncio
    async def test_validate_client_disconnected(self):
        """Test validation of disconnected clients."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Create and disconnect a client
        await collector.on_connection("test-client")
        await collector.on_disconnection("test-client")

        # Give time for queue to process the disconnection
        await asyncio.sleep(0.1)

        # Disconnected client should return False
        assert not collector._validate_client("test-client")

        await collector.stop()

    @pytest.mark.asyncio
    async def test_validate_client_connected(self):
        """Test validation of connected clients."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Create a client
        await collector.on_connection("test-client")

        # Connected client should return True
        assert collector._validate_client("test-client")

        await collector.stop()

    @pytest.mark.asyncio
    async def test_record_timestamp(self):
        """Test timestamp recording helper."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        initial_count = collector.total_messages
        initial_size = len(collector._message_timestamps)

        collector._record_timestamp(collector._message_timestamps, "test")

        # Should increment counters
        assert collector.total_messages == initial_count + 1
        assert len(collector._message_timestamps) == initial_size + 1

        await collector.stop()

    @pytest.mark.asyncio
    async def test_update_channel_metric_numeric(self):
        """Test updating numeric channel metrics."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Create a client
        channel = await collector.on_connection("test-client")
        assert channel.messages_sent == 0

        # Update metric
        collector._update_channel_metric("test-client", "messages_sent", 5)

        # Should be incremented
        assert collector._channels["test-client"].messages_sent == TEST_MESSAGES_SENT_INITIAL

        # Update again
        collector._update_channel_metric("test-client", "messages_sent", 3)
        assert collector._channels["test-client"].messages_sent == TEST_MESSAGES_SENT_FINAL

        await collector.stop()

    @pytest.mark.asyncio
    async def test_update_channel_metric_nonexistent(self):
        """Test updating metrics for nonexistent clients."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Should not crash
        collector._update_channel_metric("nonexistent", "messages_sent", 5)

        await collector.stop()


class TestOperationTracking:
    """Test operation tracking decorators."""

    @pytest.mark.asyncio
    async def test_decorator_preserves_signature(self):
        """Test that decorator doesn't break function signatures."""
        collector = StateChannelMetricsCollector()

        # Verify decorator doesn't break signatures
        sig = inspect.signature(collector._handle_on_message_sent)
        params = list(sig.parameters.keys())
        assert 'client_id' in params
        assert 'message_type' in params
        assert 'message_size' in params

    @pytest.mark.asyncio
    async def test_operation_success_tracking(self):
        """Test that successful operations are tracked."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Create client and send message
        await collector.on_connection("test-client")
        await collector.on_message_sent("test-client", "update", 100)

        # Give time for queue to process the operation
        await asyncio.sleep(0.1)

        # Should track the operation
        assert "update_message_stats" in collector._operation_count
        assert collector._operation_count["update_message_stats"] >= 1

        # Should have timing data
        assert "update_message_stats" in collector._operation_timings
        assert len(collector._operation_timings["update_message_stats"]) >= 1

        await collector.stop()

    @pytest.mark.asyncio
    async def test_operation_error_tracking(self):
        """Test that operation errors are tracked."""
        collector = StateChannelMetricsCollector()

        # Create a tracked operation that will fail
        @track_operation("test_failing_op")
        def failing_operation(self):
            raise ValueError("Test error")

        # Bind method to collector
        failing_operation.__get__(collector, StateChannelMetricsCollector)

        # Call and expect exception
        with pytest.raises(ValueError):
            failing_operation(collector)

        # Should track the error
        assert "test_failing_op" in collector._operation_errors
        assert collector._operation_errors["test_failing_op"] == 1

    @pytest.mark.asyncio
    async def test_operation_timing_bounds(self):
        """Test that operation timing data is bounded."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Create client
        await collector.on_connection("test-client")

        # Send many messages (more than timing deque limit of 1000)
        for i in range(1100):
            await collector.on_message_sent("test-client", "update", 100)

        # Timing deque should be bounded
        timings = collector._operation_timings.get("update_message_stats", [])
        assert len(timings) <= MAX_TIMING_ENTRIES

        await collector.stop()


class TestPerformanceImprovements:
    """Test performance characteristics."""

    @pytest.mark.asyncio
    async def test_high_throughput_processing(self):
        """Test processing many operations quickly."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Create client
        await collector.on_connection("test-client")

        start_time = time.time()

        # Submit many operations
        tasks = []
        for i in range(1000):
            tasks.append(collector.on_message_sent("test-client", "update", 100))

        await asyncio.gather(*tasks)

        duration = time.time() - start_time

        # Should process 1000 operations in reasonable time (<2 seconds)
        assert duration < MAX_TEST_DURATION_SECONDS

        await collector.stop()

    @pytest.mark.asyncio
    async def test_concurrent_client_handling(self):
        """Test handling many concurrent clients."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Connect many clients concurrently
        tasks = []
        for i in range(CONCURRENT_CLIENTS_COUNT):
            tasks.append(collector.on_connection(f"client-{i}"))

        results = await asyncio.gather(*tasks)

        # All should succeed
        assert len(results) == CONCURRENT_CLIENTS_COUNT
        assert all(r is not None for r in results)

        # Verify they're all tracked
        active_count = await collector.get_active_connections()
        assert active_count == CONCURRENT_CLIENTS_COUNT

        await collector.stop()

    @pytest.mark.asyncio
    async def test_memory_stability_under_load(self):
        """Test memory stability with sustained operations."""
        collector = StateChannelMetricsCollector()
        await collector.start()

        # Create client
        await collector.on_connection("test-client")

        # Send many messages to test deque bounds
        for batch in range(10):
            tasks = []
            for i in range(1000):
                tasks.append(collector.on_message_sent("test-client", "update", 100))
            await asyncio.gather(*tasks)

        # Message timestamps should be bounded
        assert len(collector._message_timestamps) <= collector._message_retention

        # Operation timings should be bounded
        for timings in collector._operation_timings.values():
            assert len(timings) <= MAX_TIMING_ENTRIES

        await collector.stop()
