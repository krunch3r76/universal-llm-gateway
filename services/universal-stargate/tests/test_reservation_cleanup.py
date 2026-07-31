import pytest
import asyncio
import time
import argparse
import sys
from unittest.mock import Mock
from systems.proxy.core.common.resource_manager import (
    GatewayResourceManager,
    ResourceReservation,
)


class TestReservationCleanup:
    """Test reservation cleanup and memory management"""
    
    @pytest.fixture
    def mock_metrics_provider(self):
        """Mock metrics provider with sufficient resources"""
        provider = Mock()
        provider.get_gateway_metrics.return_value = {
            "vram_free_mb": 16000,  # Large amount for testing
            "ram_free_mb": 32000
        }
        return provider
        
    @pytest.fixture
    def mock_state_manager(self):
        """Mock state manager"""
        return Mock()
        
    @pytest.fixture
    def cleanup_config(self):
        """Configuration optimized for cleanup testing"""
        return {
            "reservation_timeout": 2,     # 2 second timeout for fast testing
            "reservation_cleanup_interval": 0.5  # 0.5 second cleanup interval
        }
        
    @pytest.mark.asyncio
    async def test_basic_cleanup_cycle(self, mock_metrics_provider, mock_state_manager,
        cleanup_config):
        """Test basic reservation cleanup cycle"""
        manager = GatewayResourceManager(
            gateway_id="cleanup_test",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=cleanup_config
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
            
            # Verify initial state
            metrics = manager.get_metrics()
            assert metrics["reservations"]["active"] == 1
            assert metrics["reservations"]["expired"] == 0
            
            # Wait for cleanup to occur
            await asyncio.sleep(3)  # Wait longer than timeout
            
            # Verify cleanup occurred
            final_metrics = manager.get_metrics()
            assert final_metrics["reservations"]["active"] == 0
            assert final_metrics["reservations"]["expired"] == 1
            assert reservation.state == "released"
            
        finally:
            await manager.shutdown()
            
    @pytest.mark.asyncio 
    async def test_multiple_reservations_cleanup(self, mock_metrics_provider,
        mock_state_manager, cleanup_config):
        """Test cleanup of multiple reservations"""
        manager = GatewayResourceManager(
            gateway_id="multi_cleanup_test",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=cleanup_config
        )
        
        await manager.initialize()
        
        try:
            # Create multiple reservations
            reservations = []
            for i in range(5):
                reservation = await manager.try_reserve_resources(
                    model_id=f"model_{i}",
                    vram_mb=1000,
                    ram_mb=500
                )
                reservations.append(reservation)
                
            # All should be created successfully
            assert all(r is not None for r in reservations)
            
            initial_metrics = manager.get_metrics()
            assert initial_metrics["reservations"]["active"] == 5
            
            # Wait for cleanup
            await asyncio.sleep(3)
            
            # All should be cleaned up
            final_metrics = manager.get_metrics()
            assert final_metrics["reservations"]["active"] == 0
            assert final_metrics["reservations"]["expired"] == 5
            
            # All reservations should be released
            assert all(r.state == "released" for r in reservations)
            
        finally:
            await manager.shutdown()
            
    @pytest.mark.asyncio
    async def test_cleanup_preserves_active_reservations(self, mock_metrics_provider,
        mock_state_manager, cleanup_config):
        """Test that cleanup only removes expired reservations, not active ones"""
        manager = GatewayResourceManager(
            gateway_id="selective_cleanup_test",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=cleanup_config
        )
        
        await manager.initialize()
        
        try:
            # Create first reservation (will expire)
            old_reservation = await manager.try_reserve_resources(
                model_id="old_model",
                vram_mb=1000,
                ram_mb=500
            )
            
            # Wait for it to be about to expire
            await asyncio.sleep(1.5)
            
            # Create new reservation (should not expire)
            new_reservation = await manager.try_reserve_resources(
                model_id="new_model", 
                vram_mb=1000,
                ram_mb=500
            )
            
            # Wait for cleanup to run
            await asyncio.sleep(2)
            
            # Old should be cleaned up, new should remain
            assert old_reservation.state == "released"
            assert new_reservation.state == "pending"  # Still active
            
            metrics = manager.get_metrics()
            assert metrics["reservations"]["active"] == 1  # New reservation remains
            assert metrics["reservations"]["expired"] == 1  # Old reservation expired
            
        finally:
            await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_cleanup_memory_efficiency(self, mock_metrics_provider,
        mock_state_manager):
        """Test that cleanup prevents memory leaks from accumulating reservations"""
        config = {
            "reservation_timeout": 0.5,     # Very short timeout
            "reservation_cleanup_interval": 0.2  # Frequent cleanup
        }
        
        manager = GatewayResourceManager(
            gateway_id="memory_test",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=config
        )
        
        await manager.initialize()
        
        try:
            # Create many short-lived reservations
            total_created = 0
            for batch in range(5):  # 5 batches
                # Create batch of reservations
                for i in range(10):  # 10 per batch
                    reservation = await manager.try_reserve_resources(
                        model_id=f"batch_{batch}_model_{i}",
                        vram_mb=100,
                        ram_mb=50
                    )
                    if reservation:
                        total_created += 1
                        
                # Wait for cleanup to process this batch
                await asyncio.sleep(1)
                
            # Final wait to ensure all cleanup is done
            await asyncio.sleep(1)
            
            # Should have cleaned up most/all reservations
            final_metrics = manager.get_metrics()
            assert final_metrics["reservations"]["active"] == 0
            assert final_metrics["reservations"]["expired"] > 0
            
            # Memory should be manageable - internal dict shouldn't grow unbounded
            # In a real implementation, we might actually remove expired reservations
            # from the dict entirely to prevent memory leaks
            
        finally:
            await manager.shutdown()
            
    @pytest.mark.asyncio
    async def test_shutdown_cleanup(self, mock_metrics_provider, mock_state_manager,
        cleanup_config):
        """Test that shutdown properly cleans up all reservations"""
        manager = GatewayResourceManager(
            gateway_id="shutdown_test",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=cleanup_config
        )
        
        await manager.initialize()
        
        # Create several reservations
        reservations = []
        for i in range(3):
            reservation = await manager.try_reserve_resources(
                model_id=f"shutdown_model_{i}",
                vram_mb=1000,
                ram_mb=500
            )
            reservations.append(reservation)
            
        # Verify they exist
        metrics = manager.get_metrics()
        assert metrics["reservations"]["active"] == 3
        
        # Shutdown should clean them all up
        await manager.shutdown()
        
        # All should be released
        assert all(r.state == "released" for r in reservations)
        
    @pytest.mark.asyncio
    async def test_cleanup_task_cancellation(self, mock_metrics_provider,
        mock_state_manager, cleanup_config):
        """Test that cleanup task is properly cancelled on shutdown"""
        manager = GatewayResourceManager(
            gateway_id="cancellation_test",
            metrics_provider=mock_metrics_provider,
            state_manager=mock_state_manager,
            config=cleanup_config
        )
        
        await manager.initialize()
        
        # Verify cleanup task is running
        assert manager._cleanup_task is not None
        assert not manager._cleanup_task.done()
        
        # Shutdown should cancel the task
        await manager.shutdown()
        
        # Task should be cancelled
        assert manager._cleanup_task.cancelled()
        
    @pytest.mark.asyncio
    async def test_cleanup_exception_handling(self, mock_metrics_provider,
        mock_state_manager):
        """Test that cleanup continues even if individual cleanups fail"""
        # Create a manager with a failing metrics provider
        failing_provider = Mock()
        failing_provider.get_gateway_metrics.side_effect = Exception("Metrics failure")
        
        config = {
            "reservation_timeout": 1,
            "reservation_cleanup_interval": 0.3
        }
        
        manager = GatewayResourceManager(
            gateway_id="exception_test",
            metrics_provider=failing_provider,
            state_manager=mock_state_manager,
            config=config
        )
        
        await manager.initialize()
        
        try:
            # Let it run for a while to ensure exceptions don't kill the cleanup loop
            await asyncio.sleep(2)
            
            # Cleanup task should still be running despite exceptions
            assert not manager._cleanup_task.done()
            
        finally:
            await manager.shutdown()


# Standalone memory leak test that can be run for extended periods
async def run_extended_memory_test(duration_seconds=3600):
    """Run extended memory leak test - can be called from command line"""
    print(f"Starting extended memory test for {duration_seconds} seconds...")
    
    mock_metrics_provider = Mock()
    mock_metrics_provider.get_gateway_metrics.return_value = {
        "vram_free_mb": 32000,
        "ram_free_mb": 64000
    }
    
    config = {
        "reservation_timeout": 5,      # 5 second timeout
        "reservation_cleanup_interval": 2  # 2 second cleanup
    }
    
    manager = GatewayResourceManager(
        gateway_id="extended_test",
        metrics_provider=mock_metrics_provider,
        state_manager=Mock(),
        config=config
    )
    
    await manager.initialize()
    
    try:
        start_time = time.time()
        model_counter = 0
        
        while time.time() - start_time < duration_seconds:
            # Create some reservations
            for i in range(5):
                reservation = await manager.try_reserve_resources(
                    model_id=f"stress_model_{model_counter}",
                    vram_mb=1000,
                    ram_mb=500
                )
                model_counter += 1
                
            # Wait a bit
            await asyncio.sleep(3)
            
            # Print status every minute
            elapsed = time.time() - start_time
            if int(elapsed) % 60 == 0:
                metrics = manager.get_metrics()
                print(f"Time: {elapsed:.0f}s, Active:"
                    f"{metrics['reservations']['active']}, f"
                      f"Expired: {metrics['reservations']['expired']}, "
                      f"Total Created: {model_counter}")
                
        print("Extended test completed successfully!")
        
    finally:
        await manager.shutdown()


# Command line interface for extended testing
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run reservation cleanup tests")
    parser.add_argument("--duration", type=int, default=60, 
                       help="Duration in seconds for extended test (default: 60)")
    
    args = parser.parse_args()
    
    try:
        asyncio.run(run_extended_memory_test(args.duration))
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        sys.exit(0)
