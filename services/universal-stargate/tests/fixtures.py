"""
Test fixtures for resource management configuration.

Provides default configurations and mock objects for testing.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from systems.proxy.resource_management import (
    ResourceManagementConfig,
    GatewayConfig,
    GatewayConfigManager,
)


def get_default_resource_management_config() -> ResourceManagementConfig:
    """
    Get default resource management configuration for testing.
    
    Returns:
        Default ResourceManagementConfig with safe test values
    """
    return ResourceManagementConfig(
        max_concurrent_model_loads=1,
        model_loading_slot_acquisition_timeout=0.25,
        reservation_timeout=300,  # 5 minutes
        reservation_cleanup_interval=30,  # 30 seconds
        enable_reservation_monitoring=True,
    )


def get_test_gateway_config(gateway_name: str = "test-gateway") -> GatewayConfig:
    """
    Get test gateway configuration.
    
    Args:
        gateway_name: Name for the test gateway
        
    Returns:
        GatewayConfig with test resource management settings
    """
    return GatewayConfig(
        url="http://localhost:9998",
        name=gateway_name,
        resource_management=get_default_resource_management_config(),
        timeout=30.0,
        connectivity_timeout=1.0,
        health_timeout=2.0,
        max_concurrent_cpu_models=50,
        max_concurrent_gpu_models=10,
        api_key="test-key",
    )


@pytest.fixture
def resource_management_config():
    """Fixture providing default resource management configuration."""
    return get_default_resource_management_config()


@pytest.fixture
def gateway_config():
    """Fixture providing test gateway configuration."""
    return get_test_gateway_config()


def mock_config_manager(**resource_management_overrides):
    """
    Factory function providing mock GatewayConfigManager with optional overrides.
    
    Args:
        **resource_management_overrides: Override resource management config fields
            (e.g., max_concurrent_model_loads=2,
                model_loading_slot_acquisition_timeout=0.5)
    
    Returns:
        AsyncMock GatewayConfigManager with customizable configuration
    """
    manager = AsyncMock(spec=GatewayConfigManager)
    
    # Create base config
    base_resource_config = get_default_resource_management_config()
    
    # Apply overrides
    if resource_management_overrides:
        config_dict = {
            "max_concurrent_model_loads":
                base_resource_config.max_concurrent_model_loads,
            "model_loading_slot_acquisition_timeout":
                base_resource_config.model_loading_slot_acquisition_timeout,
            "reservation_timeout": base_resource_config.reservation_timeout,
            "reservation_cleanup_interval":
                base_resource_config.reservation_cleanup_interval,
            "enable_reservation_monitoring":
                base_resource_config.enable_reservation_monitoring,
        }
        config_dict.update(resource_management_overrides)
        resource_config = ResourceManagementConfig(**config_dict)
    else:
        resource_config = base_resource_config
    
    # Create test gateway config with (possibly overridden) resource config
    test_config = GatewayConfig(
        url="http://localhost:9998",
        name="test-gateway",
        resource_management=resource_config,
        timeout=30.0,
        connectivity_timeout=1.0,
        health_timeout=2.0,
        max_concurrent_cpu_models=50,
        max_concurrent_gpu_models=10,
        api_key="test-key",
    )
    
    # Set up mock behavior
    manager.get_gateway_config.return_value = test_config
    manager.get_all_gateway_configs.return_value = {"test-gateway": test_config}
    manager.subscribe.return_value = None
    
    return manager


@pytest.fixture
def config_manager_fixture():
    """Pytest fixture providing mock GatewayConfigManager with defaults."""
    return mock_config_manager()


@pytest.fixture
def mock_metrics_provider():
    """Fixture providing mock metrics provider."""
    provider = Mock()
    provider.get_gateway_metrics.return_value = {
        "vram_free_mb": 8000,
        "ram_free_mb": 16000
    }
    return provider


@pytest.fixture
def mock_state_manager():
    """Fixture providing mock state manager."""
    return Mock()
