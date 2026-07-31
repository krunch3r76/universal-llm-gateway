"""Dispatch-op → typed OpenAPI route seed (migration bridge).

``mcp_route_seed`` is the *only* hand-maintained served-op table that remains
while FastAPI routes lack native ``openapi_extra={"x-mcp": …}`` stamps. It
carries ``(METHOD, path)`` only — ``operationId`` is always read from the live
OpenAPI document via ``openapi_mcp.binding.inject_x_mcp`` +
``extract_typed_routes``.

Consumers must call ``typed_routes_from_openapi`` (or the codegen path) rather
than treating this seed as the served set of record.
"""

from __future__ import annotations

from collections.abc import Mapping

from openapi_mcp.binding import Method, TypedRoute, extract_typed_routes, inject_x_mcp

# Mechanical census from path-sim A §2.1 (2026-07-18 checkout); method/path only.
_MCP_ROUTE_SEED: dict[str, tuple[Method, str]] = {
    "activate": ("GET", "/assertions/activate"),
    "analyze_impact": ("POST", "/assertions/analyze-impact"),
    "assert": ("POST", "/assertions"),
    "assertions": ("GET", "/assertions"),
    "audit": ("GET", "/boot-audit-counters"),
    "deadlines": ("GET", "/deadlines"),
    "edge_types": ("GET", "/edges/types"),
    "edges": ("POST", "/edges"),
    "entities": ("GET", "/entities"),
    "impact": ("GET", "/edges/impact"),
    "relationships": ("GET", "/relationships"),
    "render_subgraph": ("GET", "/subgraph/render"),
    "resolve": ("GET", "/resolve"),
    "search": ("GET", "/assertions/search"),
    "stats": ("GET", "/stats"),
    "supersede": ("POST", "/assertions/supersede"),
    "surface_forms": ("GET", "/surface-forms"),
    "todo_audit": ("GET", "/todo-audit"),
    "todo_candidates": ("GET", "/todo-candidates"),
    "walk_subgraph": ("GET", "/subgraph/walk"),
}

# Adapter-orchestration ops — structurally untypeable on HTTP SOT (bucket d).
UNTYPEABLE_OPS: frozenset[str] = frozenset(
    {
        "doc_template",
        "doc_validate",
        "implement_ready_preflight",
        "resolve_assertion_chunk",
    }
)


def mcp_route_seed() -> Mapping[str, tuple[Method, str]]:
    """Return the migration seed: op → (METHOD, path)."""
    return _MCP_ROUTE_SEED


def typed_routes_from_openapi(openapi_schema: Mapping) -> dict[str, TypedRoute]:
    """Derive served bindings by injecting seed ``x-mcp`` then extracting."""
    enriched = inject_x_mcp(openapi_schema, _MCP_ROUTE_SEED, tool="cortex")
    return extract_typed_routes(enriched)


def _legacy_typed_route_by_op() -> dict[str, TypedRoute]:
    """Compat view with placeholder operationIds — prefer typed_routes_from_openapi."""
    return {
        op: TypedRoute(method=method, path=path, operation_id="", tool="cortex")
        for op, (method, path) in _MCP_ROUTE_SEED.items()
    }


# Backward-compat name used by census/bijection before derivation landed.
# operation_id is empty here; live ids come from typed_routes_from_openapi.
TYPED_ROUTE_BY_OP: dict[str, TypedRoute] = _legacy_typed_route_by_op()
