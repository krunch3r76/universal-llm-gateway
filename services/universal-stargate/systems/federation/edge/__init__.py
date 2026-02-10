"""
Edge mode federation support.

Accepts inbound federation connections from Relay Stargates.
"""

from .router import create_edge_federation_router
from .sender import EdgeTelemetrySender
from .server import EdgeFederationServer
from .telemetry import (
    build_initial_telemetry_payload,
    create_model_lifecycle_callbacks,
    create_periodic_heartbeat_task,
    create_resource_update_callback,
)

__all__ = [
    "EdgeFederationServer",
    "EdgeTelemetrySender",
    "create_edge_federation_router",
    "build_initial_telemetry_payload",
    "create_periodic_heartbeat_task",
    "create_resource_update_callback",
    "create_model_lifecycle_callbacks",
]
