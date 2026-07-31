"""Unit tests for metrics cleanup functionality and error isolation."""

import asyncio
import os
import sys
import time
from unittest.mock import patch

import pytest

# Add the service directory to sys.path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../services/_universal-llm-gateway'))

from src.core.metrics.state_channel_metrics import StateChannelMetricsCollector


class TestMetricsCleanup:
    """Test suite for metrics cleanup and error isolation."""

    @pytest.fixture
    async def collector(self):
        """Create a metrics collector for testing."""
        # Use small values for testing
        with patch.dict(os.environ, {
            "METRICS_MESSAGE_RETENTION": "100",
            "METRICS_ERROR_RETENTION": "50",
            "METRICS_QUEUE_SIZE": "10",
            "METRICS_QUEUE_TIMEOUT": "0.05"
        }):
            collector = StateChannelMetricsCollector()
            await collector.start()
            yield collector
            await collector.stop()

    async def test_error_isolation_when_stopped(self, collector):
        """Test that operations don't crash when collector is stopped."""
        # Stop the collector
        await collector.stop()

        # These operations should not raise exceptions
        result = await collector.on_connection("test_client")
        assert result is None

        result = await collector.on_message_sent("test_client", "update", 100)
        assert result is None

        result = await collector.get_active_connections()
        assert result is None

    async def test_queue_timeout(self, collector):
        """Test timeout handling for queue operations."""
        # Stop the processor to simulate a blocked queue
        await collector.stop()

        # Now try to submit - should timeout quickly
        start_time = time.time()
        result = await collector.on_connection("client1")
        end_time = time.time()

        # Should complete quickly due to timeout/failure (within 0.2 seconds)
        assert end_time - start_time < 0.2
        # Should return None due to queue not being available
        assert result is None

    async def test_error_handling_in_submit(self, collector):
        """Test error handling in _submit method."""

        def failing_handler():
            raise ValueError("Test error")

        # Should not raise exception, should return None
        result = await collector._submit(failing_handler)
        assert result is None

    async def test_queue_operations_non_blocking(self, collector):
        """Test that queue operations are non-blocking."""
        start_time = time.time()

        # These operations should complete quickly
        await collector.on_connection("test_client")
        await collector.on_message_sent("test_client", "update", 100)
        await collector.on_disconnection("test_client")

        end_time = time.time()

        # Should complete within reasonable time (1 second)
        assert end_time - start_time < 1.0

    async def test_collector_start_stop_lifecycle(self):
        """Test collector start/stop lifecycle."""
        collector = StateChannelMetricsCollector()

        # Should start successfully
        await collector.start()
        assert collector._queue is not None
        assert collector._processor_task is not None

        # Should stop successfully
        await collector.stop()
        assert collector._queue is None
        assert collector._processor_task is None

    async def test_backward_compatibility_shutdown(self, collector):
        """Test that shutdown() method still works (backward compatibility)."""
        # Should work without errors
        await collector.shutdown()

        # Queue should be None after shutdown
        assert collector._queue is None


class TestConfigurationHandling:
    """Test environment variable configuration."""

    def test_default_configuration(self):
        """Test default configuration values."""
        collector = StateChannelMetricsCollector()

        assert collector._message_retention == 10000
        assert collector._error_retention == 1000
        assert collector._queue_size == 1000
        assert collector._queue_timeout == 0.1

    def test_custom_configuration(self):
        """Test custom configuration via environment variables."""
        with patch.dict(os.environ, {
            "METRICS_MESSAGE_RETENTION": "5000",
            "METRICS_ERROR_RETENTION": "500",
            "METRICS_QUEUE_SIZE": "2000",
            "METRICS_QUEUE_TIMEOUT": "0.2"
        }):
            collector = StateChannelMetricsCollector()

            assert collector._message_retention == 5000
            assert collector._error_retention == 500
            assert collector._queue_size == 2000
            assert collector._queue_timeout == 0.2

    def test_invalid_configuration_handling(self):
        """Test handling of invalid configuration values."""
        with patch.dict(os.environ, {
            "METRICS_MESSAGE_RETENTION": "invalid",
            "METRICS_QUEUE_TIMEOUT": "not_a_float"
        }):
            # Should raise ValueError for invalid values
            with pytest.raises(ValueError):
                StateChannelMetricsCollector()


if __name__ == "__main__":
    pytest.main([__file__])
