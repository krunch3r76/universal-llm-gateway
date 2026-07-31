"""Service-agnostic OpenAPI → MCP binding machinery (W0 front).

Doctrine: HTTP + served typed-args OpenAPI is the substrate; MCP is a client
adapter. Bindings are derived from path-operation ``x-mcp`` extensions so the
op→route map cannot silently drift from the served document.
"""

from __future__ import annotations

from openapi_mcp.binding import (
    TypedRoute,
    extract_typed_routes,
    inject_x_mcp,
    x_mcp,
)
from openapi_mcp.registry import ServiceDescriptor, default_registry

__all__ = [
    "ServiceDescriptor",
    "TypedRoute",
    "default_registry",
    "extract_typed_routes",
    "inject_x_mcp",
    "x_mcp",
]
