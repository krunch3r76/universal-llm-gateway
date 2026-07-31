"""Smoke tests for HTTP telemetry endpoint."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.federation.remote.api import telemetry


class TestHTTPTelemetryEndpoint:
    """Smoke tests for /api/v1/federation/telemetry endpoint."""
    
    @pytest.fixture
    def app(self):
        """Create minimal FastAPI app with telemetry endpoint."""
        app = FastAPI()
        app.include_router(telemetry.router)
        
        # Mock app state (gateway_manager, federation_config)
        class MockResources:
            available_vram_mb = 16000
            available_ram_mb = 32000
        
        class MockClient:
            def get_ws_resources(self):
                return MockResources()
            
            def get_loaded_models(self):
                return []
            
            def get_busy_models(self):
                return []
        
        class MockGateway:
            def __init__(self):
                self.client = MockClient()
        
        class MockGatewayManager:
            def get_gateway(self):
                return MockGateway()
        
        class MockLocalGateway:
            gateway_id = "test-gateway"
        
        class MockFederationConfig:
            local_gateway = MockLocalGateway()
        
        app.state.gateway_manager = MockGatewayManager()
        app.state.federation_config = MockFederationConfig()
        
        # Initialize telemetry tracker
        telemetry.initialize_telemetry(node_id="test-node", log_level="INFO")
        
        return app
    
    def test_empty_delta_returns_204(self, app):
        """Empty delta should return 204 No Content."""
        client = TestClient(app)
        
        # First request (will have initial state)
        response1 = client.get(
            "/api/v1/federation/telemetry",
            headers={
                "X-Federation-Source": "master",
                "X-Federation-Key": "test-key",
            },
        )
        assert response1.status_code == 200  # First delta has state
        
        # Second request (no state change)
        response2 = client.get(
            "/api/v1/federation/telemetry",
            headers={
                "X-Federation-Source": "master",
                "X-Federation-Key": "test-key",
            },
        )
        assert response2.status_code == 204  # Empty delta
        assert response2.content == b""
    
    def test_delta_response_structure(self, app):
        """Delta response should have correct structure."""
        client = TestClient(app)
        
        # Update state to trigger delta
        if telemetry.tracker:
            telemetry.tracker.update({"active_requests": 1, "vram_free_mb": 14000})
        
        response = client.get(
            "/api/v1/federation/telemetry",
            headers={
                "X-Federation-Source": "master",
                "X-Federation-Key": "test-key",
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify structure
        assert data["type"] == "delta"
        assert "gateway_id" in data
        assert "changes" in data
        assert "sequence_number" in data
        assert "critical_events" in data
        assert "timestamp" in data
        assert "node_id" in data
        
        assert data["gateway_id"] == "test-gateway"
        assert data["node_id"] == "test-node"
    
    def test_full_snapshot_response_structure(self, app):
        """Full snapshot response should have correct structure."""
        client = TestClient(app)
        
        response = client.get(
            "/api/v1/federation/telemetry?full=true",
            headers={
                "X-Federation-Source": "master",
                "X-Federation-Key": "test-key",
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify structure
        assert data["type"] == "snapshot"
        assert "gateway_id" in data
        assert "state" in data
        assert "timestamp" in data
        assert "node_id" in data
        
        # State should have all fields
        state = data["state"]
        assert "loaded_models" in state
        assert "busy_models" in state
        assert "active_requests" in state
        assert "vram_free_mb" in state
        assert "ram_free_mb" in state
        assert "sequence_number" in state
    
    def test_authentication_required(self, app):
        """Request without auth headers should fail."""
        client = TestClient(app)
        
        response = client.get("/api/v1/federation/telemetry")
        
        assert response.status_code in (401, 403)
