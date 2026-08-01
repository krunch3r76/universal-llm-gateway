"""Served-op derivation for agent-bus — no hand-maintained route table.

Every MCP-reachable agent-bus route declares its binding natively at the
decorator::

    @router.post("/threads/send", openapi_extra=x_mcp("send", tool="agent_bus"))

Detectability replaces a seed table in two places:

* :func:`unbound_dispatch_ops` — dispatch ops with no stamped route.
* the committed adapter manifest + ``openapi_mcp_codegen.py --check``.
"""

from __future__ import annotations

from collections.abc import Mapping

from openapi_mcp.binding import TypedRoute, extract_typed_routes
from openapi_mcp.detector import served_ops as _served_ops
from openapi_mcp.detector import unbound_dispatch_ops as _unbound_dispatch_ops

from ._ops import AGENT_BUS_DISPATCH_OPS

# Adapter-orchestration ops — structurally untypeable on HTTP SOT.
UNTYPEABLE_OPS: frozenset[str] = frozenset({"request"})


def typed_routes_from_openapi(openapi_schema: Mapping) -> dict[str, TypedRoute]:
    """Derive served bindings from native ``x-mcp`` stamps in the document."""
    return extract_typed_routes(openapi_schema)


def served_ops(openapi_schema: Mapping | None = None) -> frozenset[str]:
    """Return the set of dispatch ops bound to a stamped served route."""
    if openapi_schema is None:
        openapi_schema = _live_openapi()
    return _served_ops(openapi_schema)


def unbound_dispatch_ops(
    openapi_schema: Mapping | None = None,
    *,
    op_specs: Mapping[str, str] | None = None,
) -> list[str]:
    """Return dispatch ops with neither an ``x-mcp`` stamp nor an exemption."""
    ops = op_specs if op_specs is not None else AGENT_BUS_DISPATCH_OPS
    return _unbound_dispatch_ops(
        openapi_schema,
        all_ops=ops,
        exempt_ops=UNTYPEABLE_OPS,
        load_openapi=_live_openapi if openapi_schema is None else None,
    )


def _live_openapi() -> Mapping:
    from agent_bus_store.server import create_app

    return create_app().openapi()
