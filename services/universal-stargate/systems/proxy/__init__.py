"""
Proxy System - HTTP request/response handling.

This system handles:
- HTTP endpoint registration (FastAPI routers)
- Request preparation and validation
- Token counting and management
- Response building and streaming
- Gateway request forwarding

Depends on:
- systems.pipeline: For multi-model workflow execution
- systems.audio: For audio profile management
- systems.routing: For gateway selection and routing decisions

Usage:
    from systems.proxy import app
    from systems.proxy.stargate import StargateProxy

    # Use the pre-configured app instance
    proxy = StargateProxy(config)
"""

from .app import app
from .stargate.proxy import StargateProxy

__all__ = [
    "app",
    "StargateProxy",
]
