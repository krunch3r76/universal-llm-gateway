"""
Event-driven resource management configuration for universal-stargate proxy.

Provides typed configuration with async subscription patterns for atomic VRAM reservation system.
This module replaces file-based polling with event-driven updates for zero-downtime configuration changes.
"""

from .config import (
    ConfigUpdateCallback,
    GatewayConfig,
    GatewayConfigManager,
    ResourceManagementConfig,
    ResourceManagementConfigError,
)

__all__ = [
    "ResourceManagementConfig",
    "ResourceManagementConfigError",
    "GatewayConfig",
    "GatewayConfigManager",
    "ConfigUpdateCallback",
]
