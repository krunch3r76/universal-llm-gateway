"""Test suite for MODEL_IDLE event with last_inference_time timestamp.

Verifies that INFERENCE_COMPLETED events always include last_inference_time
and that the value flows correctly through the WebSocket layer.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.events import Event
from src.core.events.types import INFERENCE_COMPLETED
from src.core.websocket.event_forwarder import WebSocketEventForwarder
from src.core.websocket.messages import MessageType, create_model_idle_message


class TestModelIdleTimestamp:
    """Test MODEL_IDLE event timestamp handling."""

    def test_create_model_idle_message_includes_timestamp(self):
        """Verify create_model_idle_message includes last_inference_time."""
        model_id = "test-model"
        timestamp = time.time()

        message = create_model_idle_message(model_id, timestamp)

        assert message.type == MessageType.MODEL_IDLE
        assert message.data["model_id"] == model_id
        assert message.data["last_inference_time"] == timestamp

    def test_create_model_idle_message_required_parameter(self):
        """Verify last_inference_time is required (no default)."""
        model_id = "test-model"

        # Should raise TypeError if last_inference_time is missing
        with pytest.raises(TypeError):
            create_model_idle_message(model_id)  # type: ignore

    @pytest.mark.asyncio
    async def test_event_forwarder_passes_through_timestamp(self):
        """Verify event forwarder includes last_inference_time in WebSocket message."""
        # Setup
        event_bus = MagicMock()
        connection_manager = AsyncMock()
        connection_manager.broadcast = AsyncMock(return_value=1)

        forwarder = WebSocketEventForwarder(event_bus, connection_manager)

        # Create event with timestamp
        test_timestamp = time.time()
        event = Event(
            signal=INFERENCE_COMPLETED,
            payload={"model_id": "test-model", "last_inference_time": test_timestamp},
        )

        # Process event
        await forwarder._process_and_broadcast(event)

        # Verify broadcast was called
        connection_manager.broadcast.assert_called_once()
        call_args = connection_manager.broadcast.call_args[0]
        message = call_args[0]

        # Verify message structure
        assert message.type == MessageType.MODEL_IDLE
        assert message.data["model_id"] == "test-model"
        assert message.data["last_inference_time"] == test_timestamp

    @pytest.mark.asyncio
    async def test_event_forwarder_handles_missing_timestamp(self, caplog):
        """Verify event forwarder logs error when timestamp is missing."""
        # Setup
        event_bus = MagicMock()
        connection_manager = AsyncMock()
        connection_manager.broadcast = AsyncMock(return_value=1)

        forwarder = WebSocketEventForwarder(event_bus, connection_manager)

        # Create event WITHOUT timestamp (violates contract)
        event = Event(
            signal=INFERENCE_COMPLETED,
            payload={"model_id": "test-model"},
        )

        # Should log error but not raise (fire-and-forget pattern)
        with caplog.at_level("ERROR"):
            await forwarder._process_and_broadcast(event)

        # Verify error was logged
        assert any(
            "Failed to process event" in record.message
            and "last_inference_time" in record.message
            for record in caplog.records
        )

    def test_message_serialization_includes_timestamp(self):
        """Verify WebSocket message serialization includes last_inference_time."""
        model_id = "test-model"
        timestamp = 1234567890.123

        message = create_model_idle_message(model_id, timestamp)
        serialized = message.to_dict()

        assert serialized["type"] == "model_idle"
        assert serialized["data"]["model_id"] == model_id
        assert serialized["data"]["last_inference_time"] == timestamp
        assert "timestamp" in serialized  # Message-level timestamp


class TestTimestampFallback:
    """Test fallback behavior when last_inference_end is None."""

    @pytest.mark.asyncio
    async def test_fallback_timestamp_warning_logged(self, caplog):
        """Verify warning is logged when last_inference_end is None."""
        from src.core.resources.transitions import update_model_idle_status_async
        from src.core.resources.types import ModelResourceInfo, ModelStatus

        # Setup model already LOADED (not BUSY) with no last_inference_end
        # This simulates edge case where model state wasn't properly tracked
        models = {
            "test-model": ModelResourceInfo(
                model_id="test-model",
                status=ModelStatus.LOADED,
                last_inference_end=None,
            )
        }

        event_bus = AsyncMock()

        # Invoke update
        with caplog.at_level("WARNING"):
            await update_model_idle_status_async(
                models, "test-model", "test-model", event_bus
            )

        # Verify warning was logged (only when status is not BUSY)
        assert any(
            "falling back to current time" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_fallback_timestamp_still_emits_event(self):
        """Verify event is still emitted with fallback timestamp."""
        from src.core.resources.transitions import update_model_idle_status_async
        from src.core.resources.types import ModelResourceInfo, ModelStatus

        # Setup model with no last_inference_end
        models = {
            "test-model": ModelResourceInfo(
                model_id="test-model",
                status=ModelStatus.BUSY,
                last_inference_end=None,
            )
        }

        event_bus = AsyncMock()
        event_bus.publish_async_nowait = AsyncMock()

        # Invoke update
        await update_model_idle_status_async(
            models, "test-model", "test-model", event_bus
        )

        # Verify event was emitted with a timestamp
        event_bus.publish_async_nowait.assert_called_once()
        call_args = event_bus.publish_async_nowait.call_args[0]
        event = call_args[0]

        assert event.signal == INFERENCE_COMPLETED
        assert "last_inference_time" in event.payload
        assert isinstance(event.payload["last_inference_time"], float)
        assert event.payload["last_inference_time"] > 0


class TestCancellationPath:
    """Test that force_model_idle also includes timestamp."""

    @pytest.mark.asyncio
    async def test_force_model_idle_includes_timestamp(self):
        """Verify force_model_idle emits event with timestamp."""
        from src.core.resources.tracker import ResourceTracker
        from src.core.resources.types import ModelStatus

        # Setup tracker with a loaded model
        tracker = ResourceTracker()
        tracker.event_bus = AsyncMock()
        tracker.event_bus.publish_async_nowait = AsyncMock()

        tracker.register_model("test-model")
        tracker.set_model_status("test-model", ModelStatus.BUSY)

        # Force idle
        await tracker.force_model_idle("test-model", "test_cancellation")

        # Verify event was emitted
        tracker.event_bus.publish_async_nowait.assert_called_once()
        call_args = tracker.event_bus.publish_async_nowait.call_args[0]
        event = call_args[0]

        assert event.signal == INFERENCE_COMPLETED
        assert "last_inference_time" in event.payload
        assert isinstance(event.payload["last_inference_time"], float)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
