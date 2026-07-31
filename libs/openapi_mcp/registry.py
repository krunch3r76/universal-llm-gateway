"""Fleet service registry for OpenAPI → MCP codegen (N-surface capable)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ServiceDescriptor:
    """One HTTP service that may publish MCP-bound OpenAPI operations."""

    name: str
    facade_tool: str
    load_openapi: Callable[[], dict[str, Any]]
    """Return a live or app-derived OpenAPI 3 document."""

    seed_bindings: Callable[[], dict[str, tuple[str, str]]] | None = None
    """Optional migration seed: op → (METHOD, path). Empty when routes are native."""


def _load_cortex_openapi() -> dict[str, Any]:
    from cortex_store.main import create_app

    return create_app().openapi()


def _cortex_seed() -> dict[str, tuple[str, str]]:
    from cortex_store.openapi_mcp._route_map import mcp_route_seed

    return dict(mcp_route_seed())


def _load_agent_bus_openapi() -> dict[str, Any]:
    from agent_bus_store.server import create_app

    return create_app().openapi()


def default_registry() -> tuple[ServiceDescriptor, ...]:
    """Built-in descriptors — cortex has a seed; agent-bus is dry-run-ready."""
    return (
        ServiceDescriptor(
            name="cortex",
            facade_tool="cortex",
            load_openapi=_load_cortex_openapi,
            seed_bindings=_cortex_seed,
        ),
        ServiceDescriptor(
            name="agent-bus",
            facade_tool="agent_bus",
            load_openapi=_load_agent_bus_openapi,
            seed_bindings=None,
        ),
    )
