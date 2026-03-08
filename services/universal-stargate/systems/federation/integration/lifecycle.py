"""
Federation integration lifecycle management.

Module-level functions for managing the global FederationIntegration instance.
"""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from .core import FederationIntegration

# Global instance
_integration: FederationIntegration | None = None


def get_federation_integration(
    event_bus: Any | None = None,
    health_observer: Callable[..., None] | None = None,
) -> FederationIntegration:
    """
    Get or create federation integration.

    Args:
        event_bus: Optional event bus for critical events (required for HTTP polling)
        health_observer: Optional callback for cloud proxy health observations
    """
    global _integration
    if _integration is None:
        _integration = FederationIntegration(
            event_bus=event_bus,
            health_observer=health_observer,
        )
    return _integration


async def init_federation(
    app: FastAPI,
    gateway_socket_path: str | None = None,
    model_manager: Any | None = None,
    event_bus: Any | None = None,
    gateway_manager: Any | None = None,
    stargate_config: Any | None = None,
    health_observer: Callable[..., None] | None = None,
) -> FederationIntegration:
    """
    Initialize federation for app.

    Args:
        app: FastAPI application
        gateway_socket_path: Optional gateway socket path for Master/Standalone.
                           Required for Master mode token counting.
        model_manager: Optional model manager for Remote mode orchestration.
                      Required to load models before counting tokens.
        event_bus: Optional event bus for critical events (required for HTTP polling)
        gateway_manager: Optional gateway manager for Remote mode telemetry endpoint.
                        Required to read gateway state.
        stargate_config: Optional StargateConfig for cloud provider configuration.

    Returns:
        Initialized FederationIntegration instance
    """
    integration = get_federation_integration(
        event_bus=event_bus,
        health_observer=health_observer,
    )
    await integration.startup(
        app,
        gateway_socket_path=gateway_socket_path,
        model_manager=model_manager,
        gateway_manager=gateway_manager,
        stargate_config=stargate_config,
    )
    return integration


async def shutdown_federation() -> None:
    """Shutdown federation."""
    if _integration:
        await _integration.shutdown()
