"""Served-op derivation for cortex — no hand-maintained route table.

Every MCP-reachable cortex route declares its binding natively at the
decorator::

    @router.post("/assertions", openapi_extra=x_mcp("assert"))

so the served OpenAPI document *is* the op→route source of truth. The former
``_MCP_ROUTE_SEED`` / ``TYPED_ROUTE_BY_OP`` hand table is deleted: its failure
mode was that an op with no entry was invisible rather than unbound.

Detectability replaces it in two places:

* :func:`unbound_dispatch_ops` — dispatch ops with no stamped route, so an op
  added without a stamp is *enumerable*, not silent.
* the committed adapter manifest + ``openapi_mcp_codegen.py --check`` — a stamp
  that is added, removed or renamed changes the derived manifest and ``--check``
  exits non-zero.
"""

from __future__ import annotations

from collections.abc import Mapping

from openapi_mcp.binding import TypedRoute, extract_typed_routes

# Adapter-orchestration ops — structurally untypeable on HTTP SOT (bucket d).
UNTYPEABLE_OPS: frozenset[str] = frozenset(
    {
        "doc_template",
        "doc_validate",
        "implement_ready_preflight",
        "resolve_assertion_chunk",
    }
)


def typed_routes_from_openapi(openapi_schema: Mapping) -> dict[str, TypedRoute]:
    """Derive served bindings from native ``x-mcp`` stamps in the document."""
    return extract_typed_routes(openapi_schema)


def served_ops(openapi_schema: Mapping | None = None) -> frozenset[str]:
    """Return the set of dispatch ops bound to a stamped served route."""
    if openapi_schema is None:
        openapi_schema = _live_openapi()
    return frozenset(typed_routes_from_openapi(openapi_schema))


def unbound_dispatch_ops(
    openapi_schema: Mapping | None = None,
    *,
    op_specs: Mapping[str, str] | None = None,
) -> list[str]:
    """Return dispatch ops with neither an ``x-mcp`` stamp nor an exemption.

    This is the fail-closed counterpart to the deleted seed: a new op, or an op
    whose route lost its stamp, appears here instead of silently dropping out
    of the served surface.
    """
    from cortex_store.dispatch_ops import _OP_SPECS

    ops = frozenset(op_specs if op_specs is not None else _OP_SPECS)
    return sorted(ops - served_ops(openapi_schema) - UNTYPEABLE_OPS)


def _live_openapi() -> Mapping:
    from cortex_store.main import create_app

    return create_app().openapi()
