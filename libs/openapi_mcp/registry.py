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


def _load_agent_bus_openapi() -> dict[str, Any]:
    from agent_bus_store.server import create_app

    return create_app().openapi()


def _load_rag_openapi() -> dict[str, Any]:
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)

    from services.rag.rag_service.main import app

    return app.openapi()


def default_registry() -> tuple[ServiceDescriptor, ...]:
    """Built-in descriptors — cortex is natively stamped; agent-bus is dry-run-ready."""
    return (
        ServiceDescriptor(
            name="cortex",
            facade_tool="cortex",
            load_openapi=_load_cortex_openapi,
            seed_bindings=None,
        ),
        ServiceDescriptor(
            name="agent-bus",
            facade_tool="agent_bus",
            load_openapi=_load_agent_bus_openapi,
            seed_bindings=None,
        ),
        ServiceDescriptor(
            name="rag",
            facade_tool="rag",
            load_openapi=_load_rag_openapi,
            seed_bindings=None,
        ),
    )
