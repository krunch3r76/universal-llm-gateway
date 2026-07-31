"""
Tests for queue resource verification functionality.

This module tests the resource verification capability that validates
a router's gateway selection before assignment, preventing race conditions
where gateway resources are consumed between routing and assignment.
"""

import asyncio

# Import the classes we're testing
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Add services directory to path
services_dir = Path(__file__).parent.parent / 'services' / 'universal-stargate'
sys.path.insert(0, str(services_dir))

from proxy.routing.queue import (
    QueuedRequest,
    RequestQueue,
    RequestStatus,
    ResourceVerifier,
    VerificationResult,
)
from universal_logging import get_logger

logger = get_logger(__name__)

# Mock classes for testing

@dataclass
class MockGatewayConfig:
    """Mock gateway configuration"""
    name: str
    base_url: str


class MockResourceStatus:
    """Mock resource status from gateway"""

    def __init__(
        self,
        loaded_models: list[str] | None = None,
        busy_models: list[str] | None = None,
        available_ram_mb: int = 16000,
        available_vram_mb: int = 24000
    ):
        self.loaded_models = loaded_models or []
        self.busy_models = busy_models or []
        self.available_ram_mb = available_ram_mb
        self.available_vram_mb = available_vram_mb
        self.model_details = {}


class MockModelMetadata:
    """Mock model metadata"""

    def __init__(
        self,
        id: str,
        ram_usage: int = 2000,
        vram_usage: int = 8000,
        loader_type: str = "llama_cpp_gpu"
    ):
        self.id = id
        self.ram_usage = ram_usage
        self.vram_usage = vram_usage
        self.loader_type = loader_type


class MockGatewayClient:
    """Mock gateway client"""

    def __init__(self, resource_status: MockResourceStatus | None = None):
        self.resource_status = resource_status or MockResourceStatus()

    async def get_resource_status(self):
        """Return mock resource status"""
        return self.resource_status


class MockGateway:
    """Mock gateway instance"""

    def __init__(
        self,
        name: str = "test-gateway",
        resource_status: MockResourceStatus | None = None
    ):
        self.config = MockGatewayConfig(name=name, base_url=f"http://{name}:9998")
        self.client = MockGatewayClient(resource_status)


class MockModelCache:
    """Mock model metadata cache"""

    def __init__(self, metadata: dict[str, MockModelMetadata] | None = None):
        self.metadata = metadata or {}

    async def get(self, model_id: str, gateway_client):
        """Return mock model metadata"""
        return self.metadata.get(model_id)


class MockRouter:
    """Mock router"""

    def __init__(self, model_cache: MockModelCache | None = None):
        self.model_cache = model_cache or MockModelCache()

    async def route_request(self, request):
        """Return None by default (no gateway available)"""
        return None


# Test fixtures

@pytest.fixture
def request_queue():
    """Create a request queue for testing"""
    return RequestQueue(max_size=10, max_concurrent_processing=3)


@pytest.fixture
def queued_request():
    """Create a queued request for testing"""
    return QueuedRequest(
        request_id="test-123",
        request={"model": "test-model", "messages": []},
        model_id="test-model",
        future=asyncio.Future()
    )


@pytest.fixture
def router():
    """Create a router with empty model cache"""
    return MockRouter()


@pytest.fixture
def verifier(router):
    """Create a resource verifier"""
    return ResourceVerifier(router, logger)


# Tests for ResourceVerifier

@pytest.mark.asyncio
async def test_verification_passes_when_model_already_loaded(verifier, queued_request):
    """Test that verification passes when model is already loaded on gateway"""
    # Setup: Gateway has model already loaded
    resource_status = MockResourceStatus(
        loaded_models=["test-model"],
        available_ram_mb=4000,
        available_vram_mb=8000
    )
    gateway = MockGateway(resource_status=resource_status)

    # Execute
    result = await verifier.verify_gateway_resources(gateway, queued_request)

    # Verify
    assert result == VerificationResult.PASS
    assert verifier.verifications_passed == 1


@pytest.mark.asyncio
async def test_verification_passes_with_sufficient_resources(queued_request):
    """Test that verification passes when gateway has sufficient resources"""
    # Setup: Gateway has sufficient RAM/VRAM
    resource_status = MockResourceStatus(
        loaded_models=[],
        available_ram_mb=16000,
        available_vram_mb=24000
    )
    gateway = MockGateway(resource_status=resource_status)

    # Setup model metadata
    model_metadata = MockModelMetadata(
        id="test-model",
        ram_usage=2000,
        vram_usage=8000
    )
    model_cache = MockModelCache(metadata={"test-model": model_metadata})
    router = MockRouter(model_cache=model_cache)
    verifier = ResourceVerifier(router, logger)

    # Execute
    result = await verifier.verify_gateway_resources(gateway, queued_request)

    # Verify
    assert result == VerificationResult.PASS
    assert verifier.verifications_passed == 1


@pytest.mark.asyncio
async def test_verification_fails_with_insufficient_ram(queued_request):
    """Test that verification fails when gateway has insufficient RAM"""
    # Setup: Gateway has insufficient RAM
    resource_status = MockResourceStatus(
        loaded_models=[],
        available_ram_mb=1000,  # Less than required
        available_vram_mb=24000
    )
    gateway = MockGateway(resource_status=resource_status)

    # Setup model metadata requiring 2GB RAM
    model_metadata = MockModelMetadata(
        id="test-model",
        ram_usage=2000,
        vram_usage=8000
    )
    model_cache = MockModelCache(metadata={"test-model": model_metadata})
    router = MockRouter(model_cache=model_cache)
    verifier = ResourceVerifier(router, logger)

    # Execute
    result = await verifier.verify_gateway_resources(gateway, queued_request)

    # Verify
    assert result == VerificationResult.FAIL
    assert verifier.verifications_failed == 1


@pytest.mark.asyncio
async def test_verification_fails_with_insufficient_vram(queued_request):
    """Test that verification fails when gateway has insufficient VRAM"""
    # Setup: Gateway has insufficient VRAM
    resource_status = MockResourceStatus(
        loaded_models=[],
        available_ram_mb=16000,
        available_vram_mb=4000  # Less than required
    )
    gateway = MockGateway(resource_status=resource_status)

    # Setup model metadata requiring 8GB VRAM
    model_metadata = MockModelMetadata(
        id="test-model",
        ram_usage=2000,
        vram_usage=8000
    )
    model_cache = MockModelCache(metadata={"test-model": model_metadata})
    router = MockRouter(model_cache=model_cache)
    verifier = ResourceVerifier(router, logger)

    # Execute
    result = await verifier.verify_gateway_resources(gateway, queued_request)

    # Verify
    assert result == VerificationResult.FAIL
    assert verifier.verifications_failed == 1


@pytest.mark.asyncio
async def test_verification_trusts_router_when_no_metadata(verifier, queued_request):
    """Test that verification returns ERROR when model metadata unavailable"""
    # Setup: Gateway with resources but no model metadata
    resource_status = MockResourceStatus(
        loaded_models=[],
        available_ram_mb=16000,
        available_vram_mb=24000
    )
    gateway = MockGateway(resource_status=resource_status)

    # Execute (verifier has empty model cache)
    result = await verifier.verify_gateway_resources(gateway, queued_request)

    # Verify: Should return ERROR (fail open)
    assert result == VerificationResult.ERROR
    assert verifier.verifications_errors == 1


@pytest.mark.asyncio
async def test_verification_trusts_router_when_status_unavailable(verifier, queued_request):
    """Test that verification returns ERROR when gateway status unavailable"""
    # Setup: Gateway that returns None for status
    gateway = MockGateway()
    gateway.client.get_resource_status = AsyncMock(return_value=None)

    # Execute
    result = await verifier.verify_gateway_resources(gateway, queued_request)

    # Verify: Should return ERROR (fail open)
    assert result == VerificationResult.ERROR
    assert verifier.verifications_errors == 1


@pytest.mark.asyncio
async def test_verification_trusts_router_on_error(verifier, queued_request):
    """Test that verification returns ERROR when error occurs"""
    # Setup: Gateway that raises exception
    gateway = MockGateway()
    gateway.client.get_resource_status = AsyncMock(side_effect=Exception("Network error"))

    # Execute
    result = await verifier.verify_gateway_resources(gateway, queued_request)

    # Verify: Should return ERROR (fail open on error)
    assert result == VerificationResult.ERROR
    assert verifier.verifications_errors == 1


@pytest.mark.asyncio
async def test_verification_metrics():
    """Test that verification metrics are tracked correctly"""
    # Setup
    model_metadata = MockModelMetadata(
        id="test-model",
        ram_usage=2000,
        vram_usage=8000
    )
    model_cache = MockModelCache(metadata={"test-model": model_metadata})
    router = MockRouter(model_cache=model_cache)
    verifier = ResourceVerifier(router, logger)

    queued_request = QueuedRequest(
        request_id="test-123",
        request={"model": "test-model", "messages": []},
        model_id="test-model",
        future=asyncio.Future()
    )

    # Test PASS
    resource_status = MockResourceStatus(
        loaded_models=["test-model"],
        available_ram_mb=16000,
        available_vram_mb=24000
    )
    gateway = MockGateway(resource_status=resource_status)
    result = await verifier.verify_gateway_resources(gateway, queued_request)
    assert result == VerificationResult.PASS

    # Test FAIL
    resource_status = MockResourceStatus(
        loaded_models=[],
        available_ram_mb=1000,  # Insufficient
        available_vram_mb=24000
    )
    gateway = MockGateway(resource_status=resource_status)
    result = await verifier.verify_gateway_resources(gateway, queued_request)
    assert result == VerificationResult.FAIL

    # Test ERROR
    gateway = MockGateway()
    gateway.client.get_resource_status = AsyncMock(return_value=None)
    result = await verifier.verify_gateway_resources(gateway, queued_request)
    assert result == VerificationResult.ERROR

    # Check metrics
    metrics = verifier.get_metrics()
    assert metrics['verifications_passed'] == 1
    assert metrics['verifications_failed'] == 1
    assert metrics['verifications_errors'] == 1
    assert metrics['total_verifications'] == 3


# Tests for RequestQueue integration

@pytest.mark.asyncio
async def test_set_router_method_creates_verifier(request_queue):
    """Test that set_router method creates ResourceVerifier"""
    # Setup
    router = MockRouter()

    # Execute
    request_queue.set_router(router)

    # Verify
    assert request_queue._verifier is not None
    assert isinstance(request_queue._verifier, ResourceVerifier)


@pytest.mark.asyncio
async def test_queue_stats_include_verification_metrics():
    """Test that queue stats include verification metrics"""
    # Setup
    queue = RequestQueue(max_size=10, max_concurrent_processing=1)
    router = MockRouter()
    queue.set_router(router)

    # Create and process a request
    request = {"model": "test-model", "messages": []}
    future = await queue.enqueue(request, timeout=10.0)

    # Setup gateway that will pass verification
    resource_status = MockResourceStatus(
        loaded_models=["test-model"],
        available_ram_mb=16000,
        available_vram_mb=24000
    )
    gateway = MockGateway(resource_status=resource_status)

    # Setup router that returns this gateway
    router.route_request = AsyncMock(return_value=gateway)

    # Process queue
    await queue.process_queue(router)

    # Get stats
    stats = queue.get_queue_stats()

    # Verify stats include verification metrics
    assert 'verification' in stats
    assert 'verifications_passed' in stats['verification']
    assert stats['verification']['verifications_passed'] == 1


@pytest.mark.asyncio
async def test_integration_verification_in_process_queue():
    """Test that verification is properly integrated in process_queue"""
    # Setup
    queue = RequestQueue(max_size=10, max_concurrent_processing=1)

    # Create request
    request = {"model": "test-model", "messages": []}
    future = await queue.enqueue(request, timeout=10.0)

    # Setup gateway that will pass verification
    resource_status = MockResourceStatus(
        loaded_models=["test-model"],
        available_ram_mb=16000,
        available_vram_mb=24000
    )
    gateway = MockGateway(resource_status=resource_status)

    # Setup router that returns this gateway
    router = MockRouter()
    router.route_request = AsyncMock(return_value=gateway)
    queue.set_router(router)

    # Process queue
    await queue.process_queue(router)

    # Verify: Request should be resolved with gateway
    assert future.done()
    assert not future.cancelled()
    result = future.result()
    assert result is gateway


@pytest.mark.asyncio
async def test_integration_verification_failure_requeues():
    """Test that verification failure causes request to be re-queued"""
    # Setup
    queue = RequestQueue(max_size=10, max_concurrent_processing=1)

    # Create request
    request = {"model": "test-model", "messages": []}
    future = await queue.enqueue(request, timeout=10.0)

    # Setup gateway with insufficient resources
    resource_status = MockResourceStatus(
        loaded_models=[],
        available_ram_mb=1000,  # Insufficient
        available_vram_mb=24000
    )
    gateway = MockGateway(resource_status=resource_status)

    # Setup model metadata
    model_metadata = MockModelMetadata(
        id="test-model",
        ram_usage=2000,
        vram_usage=8000
    )
    model_cache = MockModelCache(metadata={"test-model": model_metadata})
    router = MockRouter(model_cache=model_cache)
    router.route_request = AsyncMock(return_value=gateway)
    queue.set_router(router)

    # Process queue
    await queue.process_queue(router)

    # Verify: Request should not be resolved yet (re-queued)
    assert not future.done()

    # Verify: Queue should have the request back
    assert queue.queue.qsize() == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
