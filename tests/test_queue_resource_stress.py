"""
Stress tests for RequestQueue resource verification under heavy load.

Phase 2: Comprehensive stress testing validating system behavior under:
- High re-queue rates
- Resource competition
- Concurrent request handling
- Memory pressure
- Gateway failures

Tests verify:
- Stable memory usage under load
- No deadlocks with shared router reference
- Graceful degradation under resource exhaustion
- Cache performance under stress
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock

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
async def test_high_requeue_rate_handling():
    """Test system handles high re-queue rates gracefully."""
    # Create gateway that fails verification initially (insufficient resources)
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=2000,  # Insufficient
                                  available_vram=2000)  # Insufficient
    
    # Track verification calls
    verification_count = {'count': 0}
    
    async def get_status():
        verification_count['count'] += 1
        # After 5 verifications, resources become available
        if verification_count['count'] > 5:
            status = MagicMock()
            status.available_ram_mb = 16000
            status.available_vram_mb = 24000
            status.loaded_models = []
            status.model_details = {}
            return status
        else:
            # Keep returning insufficient resources
            status = MagicMock()
            status.available_ram_mb = 2000
            status.available_vram_mb = 2000
            status.loaded_models = []
            status.model_details = {}
            return status
    
    gateway.client.get_resource_status = AsyncMock(side_effect=get_status)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-model", 4000, 8000)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue with longer timeout to allow re-queuing
    queue = RequestQueue(max_size=10, max_concurrent_processing=1, default_timeout=60.0)
    queue.set_router(router)
    
    # Disable verification caching for this stress test (to test actual re-queue logic)
    queue._verifier._verification_cache_ttl = 0.0  # Disable verification cache
    
    # Enqueue request
    request = {"model": "test-model", "messages": [{"role": "user", "content": "test"}]}
    future = await queue.enqueue(request)
    
    # Process queue repeatedly until request succeeds
    max_iterations = 20
    for i in range(max_iterations):
        await queue.process_queue(router)
        if future.done():
            break
        await asyncio.sleep(0.1)
    
    # Verify request eventually succeeded
    assert future.done()
    result = await future
    assert result == gateway
    
    # Verify multiple re-queues happened
    metrics = queue._verifier.get_metrics()
    assert metrics['verifications_failed'] >= 5  # Should have failed multiple times before succeeding
    assert verification_count['count'] > 5


@pytest.mark.asyncio
async def test_concurrent_resource_competition():
    """Test concurrent requests competing for limited resources."""
    # Create gateway with limited resources (can only handle 2 models at once)
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=10000,  # Enough for 2 models
                                  available_vram=20000)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-model", 4000, 8000)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue
    queue = RequestQueue(max_size=100, max_concurrent_processing=10)
    queue.set_router(router)
    
    # Enqueue 10 concurrent requests (more than gateway can handle)
    futures = []
    for i in range(10):
        request = {"model": "test-model", "messages": [{"role": "user", "content": f"test {i}"}]}
        future = await queue.enqueue(request)
        futures.append(future)
    
    # Process queue
    await queue.process_queue(router, max_concurrent=10)
    
    # Some requests should have been routed, some might be re-queued
    completed = sum(1 for f in futures if f.done() and not f.exception())
    
    # Verify system handled resource competition without crashing
    assert completed >= 2  # At least some requests completed
    
    # Verify metrics tracked the competition
    metrics = queue._verifier.get_metrics()
    assert metrics['total_verifications'] >= 10


@pytest.mark.asyncio
async def test_memory_stability_under_load():
    """Test memory usage remains stable with metadata caching under load."""
    # Create mock gateway
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=16000, available_vram=24000)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    
    # Create multiple different models
    model_metadatas = {}
    for i in range(100):
        model_id = f"model-{i}"
        model_metadatas[model_id] = create_mock_model_metadata(model_id, 4000, 8000)
    
    async def get_metadata(model_id, gateway_client):
        return model_metadatas.get(model_id)
    
    model_cache.get = AsyncMock(side_effect=get_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 100}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue
    queue = RequestQueue(max_size=1000, max_concurrent_processing=50)
    queue.set_router(router)
    
    # Process 1000 requests across 100 different models
    futures = []
    for i in range(1000):
        model_id = f"model-{i % 100}"  # Cycle through models
        request = {"model": model_id, "messages": [{"role": "user", "content": f"test {i}"}]}
        future = await queue.enqueue(request)
        futures.append(future)
    
    # Process queue
    await queue.process_queue(router, max_concurrent=50)
    
    # Wait for completion
    await asyncio.gather(*futures, return_exceptions=True)
    
    # Verify cache sizes are bounded (not growing unbounded)
    metrics = queue._verifier.get_metrics()
    
    # Metadata cache should be <= 100 (one per unique model)
    assert metrics['metadata_cache_size'] <= 100
    
    # Verification cache should be bounded
    assert metrics['verification_cache_size'] <= 200  # Gateway + model combinations
    
    # Verify cache hit rate is good
    assert metrics['cache_hit_rate'] > 0.5  # At least 50% cache hits


@pytest.mark.asyncio
async def test_no_deadlocks_with_shared_router():
    """Test no deadlocks occur with shared router reference under concurrency."""
    # Create mock gateway
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=16000, available_vram=24000)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache with artificial delay
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-model", 4000, 8000)
    
    async def slow_get_metadata(model_id, gateway_client):
        await asyncio.sleep(0.01)  # Simulate slow lookup
        return model_metadata
    
    model_cache.get = AsyncMock(side_effect=slow_get_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue
    queue = RequestQueue(max_size=100, max_concurrent_processing=50)
    queue.set_router(router)
    
    # Enqueue many concurrent requests
    futures = []
    for i in range(100):
        request = {"model": "test-model", "messages": [{"role": "user", "content": f"test {i}"}]}
        future = await queue.enqueue(request)
        futures.append(future)
    
    # Process queue with high concurrency and timeout
    try:
        await asyncio.wait_for(
            queue.process_queue(router, max_concurrent=50),
            timeout=10.0  # Should complete within 10 seconds
        )
    except asyncio.TimeoutError:
        pytest.fail("Deadlock detected: queue processing timed out")
    
    # Verify requests completed
    completed = sum(1 for f in futures if f.done())
    assert completed == 100


@pytest.mark.asyncio
async def test_graceful_degradation_under_gateway_failures():
    """Test system degrades gracefully when gateway status unavailable."""
    # Create gateway that intermittently fails status checks
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=16000, available_vram=24000)
    
    # Track calls and fail intermittently
    call_count = {'count': 0}
    
    async def flaky_get_status():
        call_count['count'] += 1
        if call_count['count'] % 3 == 0:
            # Fail every 3rd call
            return None
        else:
            status = MagicMock()
            status.available_ram_mb = 16000
            status.available_vram_mb = 24000
            status.loaded_models = []
            status.model_details = {}
            return status
    
    gateway.client.get_resource_status = AsyncMock(side_effect=flaky_get_status)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-model", 4000, 8000)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue
    queue = RequestQueue(max_size=100, max_concurrent_processing=10)
    queue.set_router(router)
    
    # Enqueue many requests
    futures = []
    for i in range(30):
        request = {"model": "test-model", "messages": [{"role": "user", "content": f"test {i}"}]}
        future = await queue.enqueue(request)
        futures.append(future)
    
    # Process queue
    await queue.process_queue(router, max_concurrent=10)
    
    # Wait for completion
    await asyncio.gather(*futures, return_exceptions=True)
    
    # Verify some requests succeeded despite failures (fail-safe behavior)
    completed = sum(1 for f in futures if f.done() and not f.exception())
    assert completed > 0  # At least some requests should succeed
    
    # Verify errors were recorded but didn't block requests
    metrics = queue._verifier.get_metrics()
    assert metrics['verifications_errors'] > 0  # Should have some errors
    assert metrics['verifications_passed'] > 0  # But also some passes


@pytest.mark.asyncio
async def test_cache_cleanup_prevents_unbounded_growth():
    """Test cache cleanup prevents unbounded memory growth."""
    # Create mock gateway
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=16000, available_vram=24000)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    
    # Create many unique models
    model_metadatas = {}
    for i in range(500):
        model_id = f"model-{i}"
        model_metadatas[model_id] = create_mock_model_metadata(model_id, 4000, 8000)
    
    async def get_metadata(model_id, gateway_client):
        return model_metadatas.get(model_id)
    
    model_cache.get = AsyncMock(side_effect=get_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 100}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue
    queue = RequestQueue(max_size=1000, max_concurrent_processing=50)
    queue.set_router(router)
    
    # Create verifier with short TTL for testing
    verifier = queue._verifier
    verifier._metadata_cache_ttl = 1.0  # 1 second TTL
    verifier._verification_cache_ttl = 1.0
    
    # Process many requests with unique models
    futures = []
    for i in range(500):
        model_id = f"model-{i}"
        request = {"model": model_id, "messages": [{"role": "user", "content": f"test {i}"}]}
        future = await queue.enqueue(request)
        futures.append(future)
        
        # Process in batches
        if i > 0 and i % 50 == 0:
            await queue.process_queue(router, max_concurrent=50)
    
    # Final processing
    await queue.process_queue(router, max_concurrent=50)
    
    # Wait for TTL to expire
    await asyncio.sleep(2.0)
    
    # Trigger cache cleanup
    verifier.clear_stale_cache_entries()
    
    # Verify caches were cleaned
    metrics = verifier.get_metrics()
    
    # Caches should be mostly empty after cleanup
    assert metrics['metadata_cache_size'] < 50  # Most entries should be cleared
    assert metrics['verification_cache_size'] < 50


@pytest.mark.asyncio
async def test_requeue_tracking_under_pressure():
    """Test re-queue tracking works correctly under resource pressure."""
    # Create gateway with insufficient resources
    gateway_manager = MagicMock()
    gateway = create_mock_gateway("test-gateway", "http://localhost:9998",
                                  available_ram=2000,  # Insufficient
                                  available_vram=2000)
    gateway_manager.get_healthy_gateways.return_value = [gateway]
    
    # Create mock model cache
    model_cache = MagicMock()
    model_metadata = create_mock_model_metadata("test-model", 4000, 8000)
    model_cache.get = AsyncMock(return_value=model_metadata)
    
    # Create router
    gateway_configs = {"http://localhost:9998": {"max_concurrent_gpu_models": 10}}
    router = GPUModelRouter(gateway_manager, model_cache, gateway_configs)
    router.route_request = AsyncMock(return_value=gateway)
    
    # Create queue
    queue = RequestQueue(max_size=10, max_concurrent_processing=1, default_timeout=20.0)
    queue.set_router(router)
    
    # Disable verification caching to ensure re-queues happen
    queue._verifier._verification_cache_ttl = 0.0
    
    # Enqueue request
    request = {"model": "test-model", "messages": [{"role": "user", "content": "test"}]}
    future = await queue.enqueue(request)
    
    # Process queue multiple times to trigger re-queues
    requeue_count = 0
    for i in range(5):
        await queue.process_queue(router)
        if not future.done():
            requeue_count = queue.total_requeued
        await asyncio.sleep(0.01)
    
    # Verify re-queues were tracked
    assert requeue_count >= 3  # Should have been re-queued multiple times
    assert queue.total_requeued == requeue_count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

