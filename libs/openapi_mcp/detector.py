"""Fail-closed unbound-op detection for OpenAPI-stamped MCP facades."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from openapi_mcp.binding import extract_typed_routes


def served_ops(openapi_schema: Mapping) -> frozenset[str]:
    """Return dispatch op names bound to a stamped served route."""
    return frozenset(extract_typed_routes(openapi_schema))


def unbound_dispatch_ops(
    openapi_schema: Mapping | None = None,
    *,
    all_ops: Iterable[str],
    exempt_ops: frozenset[str] = frozenset(),
    load_openapi: Callable[[], Mapping] | None = None,
) -> list[str]:
    """Return ops with neither an ``x-mcp`` stamp nor an exemption.

    An op added without a stamp, or whose route lost its stamp, appears here
    instead of silently dropping out of the served surface.
    """
    if openapi_schema is None:
        if load_openapi is None:
            raise ValueError("openapi_schema or load_openapi is required")
        openapi_schema = load_openapi()
    ops = frozenset(all_ops)
    return sorted(ops - served_ops(openapi_schema) - exempt_ops)
