"""Integration tests for end-to-end telemetry flow."""

import pytest

from systems.federation.remote.telemetry.state_tracker import (
    TelemetryStateTracker,
)
from systems.federation.master.manager.federated_gateway_manager import (
    FederatedGatewayManager,
)
from systems.federation.common.types import FederatedGateway


class TestTelemetryIntegration:
    """End-to-end telemetry integration tests."""
    
    @pytest.fixture
    def remote_tracker(self):
        """Create Remote-side telemetry tracker."""
        return TelemetryStateTracker(node_id="remote-1")
    
    @pytest.fixture
    def master_manager(self):
        """Create Master-side gateway manager."""
        manager = FederatedGatewayManager()
        
        # Add remote gateway (FederatedGateway requires remote_stargate_id and url)
        gateway = FederatedGateway(
            gateway_id="remote-1-gateway",
            remote_stargate_id="remote-1",
            remote_stargate_url="http://localhost:9999",
            loaded_models=frozenset(),
            busy_models=frozenset(),
            active_requests=0,
            vram_free_mb=16000,
            ram_free_mb=32000,
        )
        # _last_sequence_number is 0 by default
        manager._gateways["remote-1-gateway"] = gateway
        
        return manager
    
    async def test_delta_computed_at_remote_applied_at_master(
        self,
        remote_tracker,
        master_manager,
    ):
        """Delta computed at Remote should be applied at Master."""
        # Remote: Update state
        remote_tracker.update({
            "loaded_models": ["model-a"],
            "active_requests": 1,
            "vram_free_mb": 14000,
        })
        
        # Remote: Compute delta
        delta = remote_tracker.get_delta()
        
        # Master: Apply delta (use gateway_id, not remote_id)
        gateway_id = "remote-1-gateway"
        
        # Extract changes (exclude sequence_number)
        changes = {
            k: v
            for k, v in delta.items()
            if k not in ("sequence_number", "critical_events")
        }
        
        await master_manager.apply_delta(
            gateway_id,
            changes,
            sequence_number=delta["sequence_number"],
        )
        
        # Verify Master state
        gateway = master_manager.get_gateway(gateway_id)
        assert len(gateway.loaded_models) == 1
        assert gateway.active_requests == 1
        assert gateway.vram_free_mb == 14000
    
    async def test_incremental_deltas(
        self,
        remote_tracker,
        master_manager,
    ):
        """Multiple incremental deltas should be applied correctly."""
        gateway_id = "remote-1-gateway"
        
        # Delta 1: Initial state
        remote_tracker.update({"active_requests": 1})
        delta1 = remote_tracker.get_delta()
        changes1 = {
            k: v
            for k, v in delta1.items()
            if k not in ("sequence_number", "critical_events")
        }
        await master_manager.apply_delta(
            gateway_id,
            changes1,
            sequence_number=delta1["sequence_number"],
        )

        # Delta 2: Change active_requests
        remote_tracker.update({"active_requests": 2})
        delta2 = remote_tracker.get_delta()
        changes2 = {
            k: v
            for k, v in delta2.items()
            if k not in ("sequence_number", "critical_events")
        }
        await master_manager.apply_delta(
            gateway_id,
            changes2,
            sequence_number=delta2["sequence_number"],
        )

        # Delta 3: Add model
        remote_tracker.update({
            "active_requests": 2,
            "loaded_models": ["model-a"],
        })
        delta3 = remote_tracker.get_delta()
        changes3 = {
            k: v
            for k, v in delta3.items()
            if k not in ("sequence_number", "critical_events")
        }
        await master_manager.apply_delta(
            gateway_id,
            changes3,
            sequence_number=delta3["sequence_number"],
        )
        
        # Verify final state
        gateway = master_manager.get_gateway(gateway_id)
        assert gateway.active_requests == 2
        assert len(gateway.loaded_models) == 1
    
    async def test_critical_events_flow(
        self,
        remote_tracker,
        master_manager,
    ):
        """Critical events should flow from Remote to Master."""
        # Remote: Add critical event
        remote_tracker.add_critical_event("MODEL_LOADED", {"model_id": "model-a"})
        
        # Remote: Get delta (includes critical events)
        delta = remote_tracker.get_delta()
        
        assert "critical_events" in delta
        assert delta["critical_events"][0]["event"] == "MODEL_LOADED"
        
        # Master would receive this and handle via HTTPTelemetryPoller
        # (tested separately in test_http_telemetry_poller.py)
    
    async def test_critical_events_cleared_after_delta(
        self,
        remote_tracker,
    ):
        """Critical events should be cleared from tracker after get_delta()."""
        remote_tracker.add_critical_event("MODEL_LOADED", {"model_id": "model-a"})
        
        # Get delta (includes critical events)
        delta1 = remote_tracker.get_delta()
        assert "critical_events" in delta1
        assert len(delta1["critical_events"]) == 1
        
        # Next delta should not have critical events
        delta2 = remote_tracker.get_delta()
        events = delta2.get("critical_events", [])
        assert "critical_events" not in delta2 or len(events) == 0
