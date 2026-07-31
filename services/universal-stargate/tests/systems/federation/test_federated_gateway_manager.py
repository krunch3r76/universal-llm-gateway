"""Unit tests for FederatedGatewayManager delta application."""

import time
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from model_id import ModelId

from src.scheduling.events import FEDERATION_GATEWAY_REACHABILITY_RESTORED
from systems.federation.common.types import FederatedGateway
from systems.federation.master.manager.federated_gateway_manager import (
    FederatedGatewayManager,
)


class TestFederatedGatewayManagerDeltaApplication:
    """Tests for FederatedGatewayManager.apply_delta()."""
    
    @pytest.fixture
    def manager(self):
        """Create FederatedGatewayManager with test gateway."""
        manager = FederatedGatewayManager(event_bus=MagicMock())
        
        # Add test gateway (FederatedGateway requires remote_stargate_id and url)
        gateway = FederatedGateway(
            gateway_id="test-gateway",
            remote_stargate_id="remote-1",
            remote_stargate_url="http://localhost:9999",
            loaded_models=frozenset(),
            busy_models=frozenset(),
            active_requests=0,
            vram_free_mb=16000,
            ram_free_mb=32000,
        )
        # _last_sequence_number is 0 by default (field definition)
        manager._gateways["test-gateway"] = gateway
        
        return manager
    
    async def test_apply_delta_updates_fields(self, manager):
        """apply_delta() should update changed fields."""
        delta = {
            "active_requests": 2,
            "vram_free_mb": 14000,
        }
        
        await manager.apply_delta("test-gateway", delta, sequence_number=1)
        
        gateway = manager.get_gateway("test-gateway")
        assert gateway.active_requests == 2
        assert gateway.vram_free_mb == 14000
        assert gateway.ram_free_mb == 32000  # Unchanged
    
    async def test_apply_delta_added_removed_models(self, manager):
        """apply_delta() should handle added/removed format for loaded_models."""
        # Initial state: model-a, model-b loaded
        gateway = manager._gateways["test-gateway"]
        manager._gateways["test-gateway"] = replace(
            gateway,
            loaded_models=frozenset(
                [ModelId.parse("model-a"), ModelId.parse("model-b")]
            ),
        )
        
        # Delta: add model-c, remove model-a
        delta = {
            "loaded_models": {
                "added": ["model-c"],
                "removed": ["model-a"],
            }
        }
        
        await manager.apply_delta("test-gateway", delta, sequence_number=1)
        
        gateway = manager.get_gateway("test-gateway")
        loaded_ids = {str(m) for m in gateway.loaded_models}
        assert loaded_ids == {"model-b", "model-c"}
    
    async def test_apply_delta_full_list_models(self, manager):
        """apply_delta() should handle full list format for models."""
        delta = {
            "loaded_models": ["model-a", "model-b", "model-c"]
        }
        
        await manager.apply_delta("test-gateway", delta, sequence_number=1)
        
        gateway = manager.get_gateway("test-gateway")
        loaded_ids = {str(m) for m in gateway.loaded_models}
        assert loaded_ids == {"model-a", "model-b", "model-c"}
    
    async def test_out_of_order_delta_skipped(self, manager, caplog):
        """Out-of-order deltas should be skipped with warning."""
        # Apply delta with sequence 2
        await manager.apply_delta(
            "test-gateway",
            {"active_requests": 2},
            sequence_number=2,
        )
        
        # Try to apply delta with sequence 1 (out of order)
        await manager.apply_delta(
            "test-gateway",
            {"active_requests": 99},
            sequence_number=1,
        )
        
        gateway = manager.get_gateway("test-gateway")
        assert gateway.active_requests == 2  # Should still be 2, not 99
        assert "Out-of-order delta" in caplog.text
    
    async def test_empty_delta_skipped(self, manager, caplog):
        """Empty deltas should be skipped."""
        await manager.apply_delta("test-gateway", {}, sequence_number=1)
        
        assert "Skipping empty delta" in caplog.text
        
        gateway = manager.get_gateway("test-gateway")
        assert gateway._last_sequence_number == 1  # Sequence updated
    
    async def test_sequence_number_updated(self, manager):
        """Sequence number should be tracked per gateway."""
        await manager.apply_delta(
            "test-gateway",
            {"active_requests": 1},
            sequence_number=1,
        )
        
        gateway = manager.get_gateway("test-gateway")
        assert gateway._last_sequence_number == 1
        
        await manager.apply_delta(
            "test-gateway",
            {"active_requests": 2},
            sequence_number=2,
        )
        
        gateway = manager.get_gateway("test-gateway")
        assert gateway._last_sequence_number == 2
    
    async def test_unknown_gateway_logged(self, manager, caplog):
        """Delta for unknown gateway should be logged."""
        await manager.apply_delta(
            "unknown-gateway",
            {"active_requests": 1},
            sequence_number=1,
        )
        
        assert "unknown gateway" in caplog.text.lower()
    
    async def test_apply_snapshot(self, manager):
        """apply_snapshot() should replace full state."""
        snapshot = {
            "loaded_models": ["model-a", "model-b"],
            "busy_models": ["model-a"],
            "active_requests": 3,
            "vram_free_mb": 12000,
            "ram_free_mb": 28000,
            "sequence_number": 42,
        }
        
        await manager.apply_snapshot("test-gateway", snapshot)
        
        gateway = manager.get_gateway("test-gateway")
        assert len(gateway.loaded_models) == 2
        assert gateway.active_requests == 3
        assert gateway.vram_free_mb == 12000
        assert gateway.ram_free_mb == 28000
        assert gateway._last_sequence_number == 42
    
    async def test_apply_delta_busy_models_added_removed(self, manager):
        """apply_delta() should handle added/removed format for busy_models."""
        # Initial state: model-a busy
        gateway = manager._gateways["test-gateway"]
        manager._gateways["test-gateway"] = replace(
            gateway,
            busy_models=frozenset([ModelId.parse("model-a")])
        )
        
        # Delta: add model-b, remove model-a
        delta = {
            "busy_models": {
                "added": ["model-b"],
                "removed": ["model-a"],
            }
        }
        
        await manager.apply_delta("test-gateway", delta, sequence_number=1)
        
        gateway = manager.get_gateway("test-gateway")
        busy_ids = {str(m) for m in gateway.busy_models}
        assert busy_ids == {"model-b"}
    
    async def test_sequence_gap_accepted(self, manager):
        """Sequence gaps should be accepted (only reject out-of-order)."""
        await manager.apply_delta(
            "test-gateway",
            {"active_requests": 1},
            sequence_number=1,
        )
        
        # Jump to sequence 5 (gap of 2, 3, 4)
        await manager.apply_delta(
            "test-gateway",
            {"active_requests": 5},
            sequence_number=5,
        )
        
        gateway = manager.get_gateway("test-gateway")
        assert gateway.active_requests == 5
        assert gateway._last_sequence_number == 5
    
    async def test_gateway_immutability(self, manager):
        """Applying delta should create new gateway instance, not mutate."""
        original = manager.get_gateway("test-gateway")
        original_id = id(original)
        
        await manager.apply_delta(
            "test-gateway",
            {"active_requests": 5},
            sequence_number=1,
        )
        
        updated = manager.get_gateway("test-gateway")
        assert id(updated) != original_id  # Different objects
        assert updated.active_requests == 5
        assert original.active_requests == 0  # Original unchanged
    
    async def test_apply_delta_unknown_fields_ignored(self, manager):
        """Delta with unknown fields should skip them but update sequence."""
        delta = {"unknown_field_123": "value"}
        
        await manager.apply_delta("test-gateway", delta, sequence_number=1)
        
        gateway = manager.get_gateway("test-gateway")
        assert gateway._last_sequence_number == 1
        assert not hasattr(gateway, "unknown_field_123")


class TestFederatedGatewayReachabilityRestored:
    """Pipeline reload trigger when UNREACHABLE → REACHABLE without catalog diff."""

    @staticmethod
    def _unreachable_gateway() -> FederatedGateway:
        stale = time.time() - 120
        return FederatedGateway(
            gateway_id="edge-jupiter-gateway",
            remote_stargate_id="relay-jupiter",
            remote_stargate_url="http://jupiter:9999",
            last_heartbeat=stale,
            telemetry_timestamp=stale,
            available_models=frozenset([ModelId.parse("model-a")]),
        )

    def test_telemetry_restore_publishes_reachability_event(self):
        manager = FederatedGatewayManager(event_bus=MagicMock())
        gateway = self._unreachable_gateway()
        published: list = []
        manager._event_bus = MagicMock()
        manager._event_bus.publish_nowait = lambda event: published.append(event)

        with patch("asyncio.create_task") as mock_create_task:
            updated = manager._update_telemetry_timestamps(gateway)

        mock_create_task.assert_called_once()
        assert not updated.is_unreachable
        assert len(published) == 1
        assert published[0].signal == FEDERATION_GATEWAY_REACHABILITY_RESTORED
        assert published[0].payload["gateway_id"] == "edge-jupiter-gateway"
        assert published[0].payload["model_count"] == 1

    def test_no_reachability_event_when_already_reachable(self):
        manager = FederatedGatewayManager(event_bus=MagicMock())
        gateway = replace(
            self._unreachable_gateway(),
            last_heartbeat=time.time(),
            telemetry_timestamp=time.time(),
        )
        published: list = []
        manager._event_bus = MagicMock()
        manager._event_bus.publish_nowait = lambda event: published.append(event)

        with patch("asyncio.create_task") as mock_create_task:
            manager._update_telemetry_timestamps(gateway)

        mock_create_task.assert_not_called()
        assert published == []
