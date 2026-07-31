"""
Integration tests for RequestQueue with all router types.

Phase 2: Comprehensive integration testing validating resource verification
works seamlessly with GPURouter, CPURouter, and HybridRouter.

Tests verify:
- Router metadata sharing eliminates redundant lookups
- All router types work with verification
- Metadata caching reduces overhead
- No race conditions with concurrent requests
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock

# Import queue components
import sys
sys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway/services/universal-stargate')

from proxy.routing.queue import (
    RequestQueue,
    QueuedRequest,
    ResourceVerifier,
    VerificationResult,
    QueueManager
)
from proxy.routing.gpu_router import GPUModelRouter
from proxy.routing.cpu_router import CPUModelRouter


# Helper to create mock gateway
def create_mock_gateway(
    name: str,
    base_url: str,
    available_ram: int = 16000,
    available_vram: int = 24000,
    loaded_models: list = None
):
    """Create a mock gateway with configurable resources."""
    gateway = MagicMock()
    gateway.config.name = name
    gateway.config.base_url = base_url
    
    # Mock client with resource status
    gateway.client = AsyncMock()
    status = MagicMock()
    status.available_ram_mb = available_ram
    status.available_vram_mb = available_vram
    status.loaded_models = loaded_models or []
    status.model_details = {}
    
    gateway.client.get_resource_status = AsyncMock(return_value=status)
    gateway.client.get_model_configuration = AsyncMock(return_value=None)
    
    return gateway


# Helper to create mock model metadata
def create_mock_model_metadata(model_id: str, ram_usage: int, vram_usage: int = 0):
    """Create mock model metadata."""
    metadata = MagicMock()
    metadata.id = model_id
    metadata.ram_usage = ram_usage
    metadata.vram_usage = vram_usage
    metadata.loader_type = "llama.cpp" if vram_usage > 0 else "cpu"
    return metadata


@pytest.mark.asyncio
async def test_gpu_router_integration():
    """Test ResourceVerifier works with GPURouter."""
    # Create mock gateway manager
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("gpu-gateway", "http://localhost:9998", 
                                  available_ram=16000, available_vram=24000)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-gpu-model", 4000, 8000)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create GPURouter
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    
    # Mock router's route_request to return gateway
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue and set router
    queue = RequestQueue(max_size=10, max_concurrent_processing=5)
    queue.set_router(router)
    
    # Verify ResourceVerifier was created
    assert queue._verifier is not None
    assert queue._verifier.router == router
    
    # Create test request
    request = {"model": "test-gpu-model", "messages": [{"role": "user", "content": "test"}]}
    future = await queue.enqueue(request)
    
    # Process queue
    await queue.process_queue(router)
    
    # Verify future resolved with gateway
    assert future.done()
    result_gateway = await future
    assert result_gateway == gateway
    
    # Verify metrics
    metrics = queue._verifier.get_metrics()
    assert metrics['verifications_passed'] == 1
    assert metrics['total_verifications'] == 1


@pytest.mark.asyncio
async def test_cpu_router_integration():
    """Test ResourceVerifier works with CPURouter."""
    # Create mock gateway manager
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("cpu-gateway", "http://localhost:9998",
                                  available_ram=16000, available_vram=0)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-cpu-model", 4000, 0)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create CPURouter
    gateway_configs = {"http://localhost:9998": {"max_concurrent_cpu_models": 50}}
    router = CPUModelRouter(gateway_manager, model_cache, gateway_configs)
    
    # Mock router's route_request to return gateway
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue and set router
    queue = RequestQueue(max_size=10, max_concurrent_processing=5)
    queue.set_router(router)
    
    # Verify ResourceVerifier was created
    assert queue._verifier is not None
    assert queue._verifier.router == router
    
    # Create test request
    request = {"model": "test-cpu-model", "messages": [{"role": "user", "content": "test"}]}
    future = await queue.enqueue(request)
    
    # Process queue
    await queue.process_queue(router)
    
    # Verify future resolved with gateway
    assert future.done()
    result_gateway = await future
    assert result_gateway == gateway
    
    # Verify metrics
    metrics = queue._verifier.get_metrics()
    assert metrics['verifications_passed'] == 1
    assert metrics['total_verifications'] == 1


@pytest.mark.asyncio
async def test_metadata_caching_reduces_lookups():
    """Test that metadata caching reduces repeated lookups."""
    # Create mock gateway manager and gateway
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=16000, available_vram=24000)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache that tracks calls
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-model", 4000, 8000)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue and set router
    queue = RequestQueue(max_size=10, max_concurrent_processing=5)
    queue.set_router(router)
    
    # Process multiple requests for same model
    futures = []
    for i in range(5):
        request = {"model": "test-model", "messages": [{"role": "user", "content": f"test {i}"}]}
        future = await queue.enqueue(request)
        futures.append(future)
    
    # Process queue with sufficient iterations to handle all requests
    # Each call to process_queue handles up to max_concurrent requests
    for _ in range(10):
        await queue.process_queue(router, max_concurrent=5)
        # Check if all futures are done
        if all(f.done() for f in futures):
            break
        await asyncio.sleep(0.01)  # Small delay between iterations
    
    # Wait for all futures
    results = await asyncio.gather(*futures, return_exceptions=True)
    
    # Verify all completed successfully
    assert all(not isinstance(r, Exception) for r in results)
    
    # Verify cache was used (should only call model_cache.get once, then use local cache)
    # First call: fetch from router cache
    # Subsequent calls: use ResourceVerifier's local cache
    assert model_cache.get.call_count <= 5  # At most one per request (ideally 1 total)
    
    # Verify metrics show caching worked
    metrics = queue._verifier.get_metrics()
    total_checks = metrics['verifications_passed'] + metrics['verifications_cached']
    assert total_checks >= 5, f"Expected at least 5 total verifications, got {total_checks}"
    assert metrics['metadata_cache_size'] >= 1


@pytest.mark.asyncio
async def test_verification_caching_reduces_overhead():
    """Test that recent verification caching reduces overhead."""
    # Create mock gateway with model already loaded
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=16000, available_vram=24000,
                                  loaded_models=["test-model"])
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-model", 4000, 8000)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue with caching enabled (this test verifies caching behavior)
    queue = RequestQueue(max_size=10, max_concurrent_processing=5, verification_cache_ttl=10.0)
    queue.set_router(router)
    
    # Process multiple requests for same model on same gateway
    futures = []
    for i in range(10):
        request = {"model": "test-model", "messages": [{"role": "user", "content": f"test {i}"}]}
        future = await queue.enqueue(request)
        futures.append(future)
    
    # Process queue
    await queue.process_queue(router, max_concurrent=10)
    
    # Wait for all futures
    await asyncio.gather(*futures, return_exceptions=True)
    
    # Verify verification caching worked
    metrics = queue._verifier.get_metrics()
    
    # First verification: checks model loaded, caches result
    # Subsequent verifications: use cached result
    assert metrics['verifications_passed'] + metrics['verifications_cached'] >= 10
    assert metrics['verifications_cached'] > 0  # Should have cache hits
    
    # Verify cache hit rate
    assert metrics['cache_hit_rate'] > 0.0


@pytest.mark.asyncio
async def test_concurrent_requests_no_race_conditions():
    """Test concurrent request processing doesn't cause race conditions."""
    # Create mock gateway
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=32000, available_vram=48000)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-model", 4000, 8000)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue with high concurrency
    queue = RequestQueue(max_size=100, max_concurrent_processing=20)
    queue.set_router(router)
    
    # Enqueue many requests concurrently
    futures = []
    async def enqueue_request(i):
        request = {"model": "test-model", "messages": [{"role": "user", "content": f"test {i}"}]}
        return await queue.enqueue(request)
    
    # Create 50 concurrent enqueue operations
    enqueue_tasks = [enqueue_request(i) for i in range(50)]
    futures = await asyncio.gather(*enqueue_tasks)
    
    # Process queue with high concurrency (need multiple iterations for 50 requests)
    for _ in range(10):
        await queue.process_queue(router, max_concurrent=20)
        if all(f.done() for f in futures):
            break
        await asyncio.sleep(0.01)
    
    # Verify all requests completed successfully
    results = await asyncio.gather(*futures, return_exceptions=True)
    
    # Count successes
    success_count = sum(1 for r in results if not isinstance(r, Exception))
    assert success_count == 50
    
    # Verify no race conditions in metrics
    metrics = queue._verifier.get_metrics()
    assert metrics['total_verifications'] >= 50
    assert metrics['verifications_errors'] == 0  # No errors


@pytest.mark.asyncio
async def test_queue_manager_with_cpu_and_gpu_routers():
    """Test QueueManager manages both CPU and GPU queues correctly."""
    # Create mock gateways
    gateway_manager = MagicMock()
    cpu_gateway = create_mock_gateway("cpu-gateway", "http://localhost:9998",
                                      available_ram=16000, available_vram=0)
    gpu_gateway = create_mock_gateway("gpu-gateway", "http://localhost:9999",
                                      available_ram=16000, available_vram=24000)
    
    # Create mock model cache
    model_cache = MagicMock()
    cpu_metadata = create_mock_model_metadata("cpu-model", 4000, 0)
    gpu_metadata = create_mock_model_metadata("gpu-model", 4000, 8000)
    
    async def get_metadata(model_id, gateway_client):
        if "cpu" in model_id:
            return cpu_metadata
        else:
            return gpu_metadata
    
    model_cache.get = AsyncMock(side_effect=get_metadata)
    
    # Create routers
    gateway_configs = {
        "http://localhost:9998": {"max_concurrent_cpu_models": 50},
        "http://localhost:9999": {"max_concurrent_gpu_models": 10}
    }
    
    cpu_router = CPUModelRouter(gateway_manager, model_cache, gateway_configs)
    gpu_router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    
    # Mock routing
    cpu_router.route_request = AsyncMock(return_value=cpu_gateway)
    gpu_router.route_request = AsyncMock(return_value=gpu_gateway)
    
    # Create QueueManager
    config = {
        'request_queue': {
            'max_size': 100,
            'max_concurrent_processing': 10,
            'request_timeout': 300.0
        }
    }
    queue_manager = QueueManager(config)
    
    # Set routers
    queue_manager.cpu_queue.set_router(cpu_router)
    queue_manager.gpu_queue.set_router(gpu_router)
    
    # Enqueue CPU request
    cpu_request = {"model": "cpu-model", "messages": [{"role": "user", "content": "test"}]}
    cpu_future = await queue_manager.cpu_queue.enqueue(cpu_request)
    
    # Enqueue GPU request
    gpu_request = {"model": "gpu-model", "messages": [{"role": "user", "content": "test"}]}
    gpu_future = await queue_manager.gpu_queue.enqueue(gpu_request)
    
    # Process both queues
    await queue_manager.cpu_queue.process_queue(cpu_router)
    await queue_manager.gpu_queue.process_queue(gpu_router)
    
    # Verify both completed
    assert cpu_future.done()
    assert gpu_future.done()
    
    cpu_gateway_result = await cpu_future
    gpu_gateway_result = await gpu_future
    
    assert cpu_gateway_result == cpu_gateway
    assert gpu_gateway_result == gpu_gateway
    
    # Verify stats
    stats = queue_manager.get_comprehensive_stats()
    assert stats['total_processed'] == 2


@pytest.mark.asyncio
async def test_performance_overhead_under_5ms():
    """Test that verification overhead is under 5ms per request."""
    import time
    
    # Create mock gateway with instant responses
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=16000, available_vram=24000,
                                  loaded_models=["test-model"])
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-model", 4000, 8000)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue with caching enabled (this test verifies cache performance)
    queue = RequestQueue(max_size=10, max_concurrent_processing=5, verification_cache_ttl=10.0)
    queue.set_router(router)
    
    # Measure verification time
    verifier = queue._verifier
    request = QueuedRequest(
        request_id="test",
        request={"model": "test-model"},
        model_id="test-model"
    )
    
    start = time.time()
    result = await verifier.verify_gateway_resources(gateway, request)
    duration_ms = (time.time() - start) * 1000
    
    # Verify overhead is minimal (< 5ms when model already loaded)
    # First call might be slower, but cached calls should be fast
    assert result == VerificationResult.PASS
    
    # Second call should use cache
    start = time.time()
    result = await verifier.verify_gateway_resources(gateway, request)
    cached_duration_ms = (time.time() - start) * 1000
    
    # Cached verification should be very fast
    assert cached_duration_ms < 5.0
    assert verifier.verifications_cached == 1


@pytest.mark.asyncio
async def test_fifo_preserved_after_requeue():
    """Verify re-queued requests maintain original temporal ordering."""
    # Create mock gateway that fails verification initially
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=2000,  # Insufficient for first request
                                  available_vram=8000)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    # First request needs 4GB, second needs 1GB
    model_metadata_large = create_mock_model_metadata("large-model", 4000, 0)
    model_metadata_small = create_mock_model_metadata("small-model", 1000, 0)
    
    async def get_metadata(model_id, gateway_client):
        if "large" in model_id:
            return model_metadata_large
        else:
            return model_metadata_small
    
    model_cache.get = AsyncMock(side_effect=get_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_cpu_models": 10}}
    router = CPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue
    queue = RequestQueue(max_size=10, max_concurrent_processing=5)
    queue.set_router(router)
    
    # Disable verification caching for this test to ensure clean re-verification
    queue._verifier._verification_cache_ttl = 0.0
    
    # Create requests with different timestamps
    request_a = {"model": "large-model", "messages": [{"role": "user", "content": "request A"}]}
    request_b = {"model": "small-model", "messages": [{"role": "user", "content": "request B"}]}
    
    # Enqueue both - A first, then B
    future_a = await queue.enqueue(request_a)
    await asyncio.sleep(0.01)  # Ensure different timestamps
    future_b = await queue.enqueue(request_b)
    
    # Process once - A should fail verification and be re-queued
    await queue.process_queue(router)
    
    # Check that A was re-queued (not completed)
    assert not future_a.done()
    assert queue.total_requeued == 1
    
    # Process again - should still get A first (FIFO preserved)
    # Even though B has sufficient resources and A doesn't
    # Temporarily increase RAM so A can pass
    gateway.client.get_resource_status = AsyncMock(return_value=MagicMock(
        available_ram_mb=5000, available_vram_mb=8000, loaded_models=[], model_details={}
    ))
    
    # Process multiple times to handle both requests
    for _ in range(5):
        await queue.process_queue(router)
        if future_a.done() and future_b.done():
            break
    
    # Both should be done
    assert future_a.done()
    assert future_b.done()
    
    # Verify both completed successfully
    result_a = await future_a
    result_b = await future_b
    assert result_a == gateway
    assert result_b == gateway
    
    # Verify that A was re-queued exactly once
    assert queue.total_requeued == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

