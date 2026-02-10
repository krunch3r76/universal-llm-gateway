"""
Configuration management for universal-stargate proxy.

Provides typed configuration loading and validation for gateway and resource management settings.
"""

from .resource_management import (
    GatewayConfig,
    ResourceManagementConfig,
    ResourceManagementConfigError,
    get_default_resource_management_config,
    load_gateway_configs,
)

__all__ = [
    "ResourceManagementConfig",
    "ResourceManagementConfigError",
    "GatewayConfig",
    "load_gateway_configs",
    "get_default_resource_management_config",
]
