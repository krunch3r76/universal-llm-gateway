"""Served-op derivation for the jobs satellite. No hand-kept route table."""

from __future__ import annotations

from collections.abc import Mapping

from openapi_mcp.binding import TypedRoute, extract_typed_routes
from openapi_mcp.detector import served_ops as _served_ops
from openapi_mcp.detector import unbound_dispatch_ops as _unbound_dispatch_ops

from jobs.openapi_mcp._ops import JOBS_DISPATCH_OPS

UNTYPEABLE_OPS: frozenset[str] = frozenset()


def typed_routes_from_openapi(openapi_schema: Mapping) -> dict[str, TypedRoute]:
    """Derive served bindings from native x-mcp stamps in the document."""
    return extract_typed_routes(openapi_schema)


def served_ops(openapi_schema: Mapping | None = None) -> frozenset[str]:
    """Return dispatch ops bound to a stamped served route."""
    if openapi_schema is None:
        openapi_schema = _live_openapi()
    return _served_ops(openapi_schema)


def unbound_dispatch_ops(
    openapi_schema: Mapping | None = None,
    *,
    op_specs: Mapping[str, str] | None = None,
) -> list[str]:
    """Return dispatch ops with neither an x-mcp stamp nor an exemption."""
    ops = op_specs if op_specs is not None else JOBS_DISPATCH_OPS
    return _unbound_dispatch_ops(
        openapi_schema,
        all_ops=ops,
        exempt_ops=UNTYPEABLE_OPS,
        load_openapi=_live_openapi if openapi_schema is None else None,
    )


def _live_openapi() -> Mapping:
    from jobs.server import create_app

    return create_app().openapi()
