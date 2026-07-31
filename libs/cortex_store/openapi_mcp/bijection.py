"""Reachable enum ↔ served operationId bijection helpers (A1 / AC7)."""

from __future__ import annotations

from typing import Any

from cortex_store.dispatch_ops import _OP_SPECS

from ._route_map import UNTYPEABLE_OPS, served_ops, typed_routes_from_openapi


def served_operation_ids(
    openapi_schema: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return dispatch-op → OpenAPI operationId for the served bucket."""
    if openapi_schema is None:
        from cortex_store.main import create_app

        openapi_schema = create_app().openapi()
    return {
        op: route.operation_id
        for op, route in typed_routes_from_openapi(openapi_schema).items()
    }


def assert_op_served_bijection(openapi_schema: dict[str, Any]) -> None:
    """Falsifier harness: ``assert`` must biject to POST /assertions operationId."""
    routes = typed_routes_from_openapi(openapi_schema)
    route = routes["assert"]
    paths = openapi_schema.get("paths", {})
    spec = paths.get(route.path, {}).get(route.method.lower())
    if not spec:
        raise AssertionError(f"missing OpenAPI path {route.method} {route.path}")
    oid = spec.get("operationId")
    if oid != route.operation_id:
        raise AssertionError(
            f"assert operationId drift: expected {route.operation_id!r}, got {oid!r}"
        )
    if oid not in {r.operation_id for r in routes.values()}:
        raise AssertionError(f"operationId {oid!r} not in served map")


def find_reachable_unserved_violations(
    reachable_ops: frozenset[str],
    *,
    op_specs: dict[str, str] | None = None,
    openapi_schema: dict[str, Any] | None = None,
) -> list[str]:
    """Return reachable ops lacking a served route and not untypeable (A1).

    During strangler, violations are expected until cutover partition is applied.
    """
    ops = frozenset(op_specs or _OP_SPECS)
    allowed = (served_ops(openapi_schema) & ops) | (UNTYPEABLE_OPS & ops)
    return sorted(reachable_ops - allowed)
