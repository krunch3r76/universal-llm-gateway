import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock
from systems.proxy.core.common.resource_manager import (
    GatewayResourceManager,
    ResourceReservation,
)
from fixtures import get_default_resource_management_config, mock_config_manager


class TestResourceReservation:
    """Test the ResourceReservation dataclass"""
    
    def test_reservation_creation(self):
        """Test creating a reservation with default values"""
        reservation = ResourceReservation(
            id="test_id",
            gateway_id="gateway_1",
            model_id="test_model",
            vram_mb=1000,
            ram_mb=500
        )
        
        assert reservation.id == "test_id"
        assert reservation.gateway_id == "gateway_1"
        assert reservation.model_id == "test_model"
        assert reservation.vram_mb == 1000
        assert reservation.ram_mb == 500
        assert reservation.state == "pending"
        assert isinstance(reservation.created_at, float)
        
    def test_reservation_expiration(self):
        """Test reservation expiration logic"""
        # Create reservation with old timestamp
        reservation = ResourceReservation(
            id="test_id",
            gateway_id="gateway_1",
            model_id="test_model",
            vram_mb=1000,
            ram_mb=500,
            created_at=time.time() - 600  # 10 minutes ago
        )
        
        # Should be expired with 5 minute timeout
        assert reservation.is_expired(300) is True
        
        # Should not be expired with 15 minute timeout
        assert reservation.is_expired(900) is False


class TestGatewayResourceManager:
    """Test the GatewayResourceManager class"""
    
    @pytest.fixture
    def mock_metrics_provider(self):
        """Mock metrics provider"""
        provider = Mock()
        provider.get_gateway_metrics.return_value = {
            "vram_free_mb": 8000,
            "ram_free_mb": 16000
        }
        return provider
        
    @pytest.fixture
    def mock_state_manager(self):
        """Mock state manager"""
        return Mock()
        
    @pytest.fixture
    def config_manager(self):
        """Mock configuration manager"""
        return mock_config_manager()
        
    @pytest.fixture
    async def resource_manager(self, mock_metrics_provider, mock_state_manager,
        config_manager):
        """Create and initialize resource manager"""
        manager = GatewayResourceManager(
            gateway_id="test-gateway",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config_manager=config_manager
        )
        await manager.initialize()
        yield manager
        await manager.shutdown()
        
    @pytest.mark.asyncio
    async def test_initialization(self, mock_metrics_provider, mock_state_manager,
        config):
        """Test resource manager initialization"""
        manager = GatewayResourceManager(
            gateway_id="test_gateway",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=config
        )
        
        assert not manager._initialized
        await manager.initialize()
        assert manager._initialized
        assert manager._cleanup_task is not None
        
        await manager.shutdown()
        
    @pytest.mark.asyncio
    async def test_successful_reservation(self, resource_manager):
        """Test successful resource reservation"""
        reservation = await resource_manager.try_reserve_resources(
            model_id="test_model",
            vram_mb=1000,
            ram_mb=500
        )
        
        assert reservation is not None
        assert reservation.model_id == "test_model"
        assert reservation.vram_mb == 1000
        assert reservation.ram_mb == 500
        assert reservation.state == "pending"
        assert reservation.gateway_id == "test_gateway"
        
        # Check metrics updated
        metrics = resource_manager.get_metrics()
        assert metrics["reservations"]["total"] == 1
        assert metrics["reservations"]["active"] == 1
        assert "test_model" in metrics["active_models"]
        
    @pytest.mark.asyncio
    async def test_insufficient_resources(self, resource_manager):
        """Test reservation failure when insufficient resources"""
        # Try to reserve more VRAM than available
        reservation = await resource_manager.try_reserve_resources(
            model_id="large_model",
            vram_mb=10000,  # More than the 8000MB available
            ram_mb=500
        )
        
        assert reservation is None
        
        # Check metrics updated
        metrics = resource_manager.get_metrics()
        assert metrics["reservations"]["failed"] == 1
        assert metrics["reservations"]["active"] == 0
        
    @pytest.mark.asyncio
    async def test_atomic_reservation(self, mock_metrics_provider, mock_state_manager,
        config):
        """Verify atomic check-and-reserve under concurrent access"""
        manager = GatewayResourceManager(
            gateway_id="test_gateway",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=config
        )
        await manager.initialize()
        
        try:
            # Both models need 5000MB VRAM, but only 8000MB available
            # Only one should succeed
            results = await asyncio.gather(
                manager.try_reserve_resources("model1", 5000, 1000),
                manager.try_reserve_resources("model2", 5000, 1000),
                return_exceptions=True
            )
            
            # Exactly one should succeed
            successful = [r for r in results if isinstance(r, ResourceReservation)]
            failed = [r for r in results if r is None]
            
            assert len(successful) == 1
            assert len(failed) == 1
            
            # Check that the successful reservation is tracked
            metrics = manager.get_metrics()
            assert metrics["reservations"]["active"] == 1
            assert metrics["reservations"]["failed"] == 1
            
        finally:
            await manager.shutdown()
            
    @pytest.mark.asyncio
    async def test_duplicate_model_reservation(self, resource_manager):
        """Test that duplicate reservations for same model are rejected"""
        # First reservation should succeed
        reservation1 = await resource_manager.try_reserve_resources(
            model_id="test_model",
            vram_mb=1000,
            ram_mb=500
        )
        assert reservation1 is not None
        
        # Second reservation for same model should fail
        reservation2 = await resource_manager.try_reserve_resources(
            model_id="test_model",
            vram_mb=1000,
            ram_mb=500
        )
        assert reservation2 is None
        
    @pytest.mark.asyncio
    async def test_reservation_activation(self, resource_manager):
        """Test reservation activation"""
        reservation = await resource_manager.try_reserve_resources(
            model_id="test_model",
            vram_mb=1000,
            ram_mb=500
        )
        assert reservation.state == "pending"
        
        await resource_manager.activate_reservation(reservation.id)
        assert reservation.state == "active"
        
    @pytest.mark.asyncio
    async def test_reservation_release(self, resource_manager):
        """Test reservation release"""
        reservation = await resource_manager.try_reserve_resources(
            model_id="test_model",
            vram_mb=1000,
            ram_mb=500
        )
        
        initial_metrics = resource_manager.get_metrics()
        assert initial_metrics["reservations"]["active"] == 1
        
        await resource_manager.release_reservation(reservation.id)
        
        assert reservation.state == "released"
        final_metrics = resource_manager.get_metrics()
        assert final_metrics["reservations"]["active"] == 0
        assert "test_model" not in final_metrics["active_models"]
        
    @pytest.mark.asyncio
    async def test_cleanup_expired_reservations(self, mock_metrics_provider,
        mock_state_manager):
        """Test automatic cleanup of expired reservations"""
        config = {
            "reservation_timeout": 1,  # 1 second timeout
            "reservation_cleanup_interval": 0.5  # 0.5 second cleanup interval
        }
        
        manager = GatewayResourceManager(
            gateway_id="test_gateway",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=config
        )
        await manager.initialize()
        
        try:
            # Create reservation
            reservation = await manager.try_reserve_resources(
                model_id="test_model",
                vram_mb=1000,
                ram_mb=500
            )
            assert reservation is not None
            
            initial_metrics = manager.get_metrics()
            assert initial_metrics["reservations"]["active"] == 1
            
            # Wait for expiration and cleanup
            await asyncio.sleep(2)
            
            final_metrics = manager.get_metrics()
            assert final_metrics["reservations"]["active"] == 0
            assert final_metrics["reservations"]["expired"] == 1
            assert reservation.state == "released"
            
        finally:
            await manager.shutdown()
            
    @pytest.mark.asyncio
    async def test_no_metrics_available(self, mock_state_manager, config):
        """Test handling when no metrics are available"""
        mock_metrics_provider = Mock()
        mock_metrics_provider.get_gateway_metrics.return_value = None
        
        manager = GatewayResourceManager(
            gateway_id="test_gateway",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=config
        )
        await manager.initialize()
        
        try:
            reservation = await manager.try_reserve_resources(
                model_id="test_model",
                vram_mb=1000,
                ram_mb=500
            )
            
            # Should fail when no metrics available
            assert reservation is None
            
            metrics = manager.get_metrics()
            assert metrics["reservations"]["failed"] == 1
            
        finally:
            await manager.shutdown()
            
    @pytest.mark.asyncio
    async def test_metrics_exception_handling(self, mock_state_manager, config):
        """Test handling of metrics provider exceptions"""
        mock_metrics_provider = Mock()
        mock_metrics_provider.get_gateway_metrics.side_effect = Exception("Metrics"
            "error")
        
        manager = GatewayResourceManager(
            gateway_id="test_gateway",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=config
        )
        await manager.initialize()
        
        try:
            reservation = await manager.try_reserve_resources(
                model_id="test_model",
                vram_mb=1000,
                ram_mb=500
            )
            
            # Should fail gracefully
            assert reservation is None
            
            metrics = manager.get_metrics()
            assert metrics["reservations"]["failed"] == 1
            
        finally:
            await manager.shutdown()
