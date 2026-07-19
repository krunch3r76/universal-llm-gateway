"""Reachable enum ↔ served operationId bijection helpers (A1 / AC7)."""

from __future__ import annotations

from typing import Any

from cortex_store.dispatch_ops import _OP_SPECS

from ._route_map import TYPED_ROUTE_BY_OP, UNTYPEABLE_OPS


def served_operation_ids() -> dict[str, str]:
    """Return dispatch-op → OpenAPI operationId for the served bucket."""
    return {op: route.operation_id for op, route in TYPED_ROUTE_BY_OP.items()}


def assert_op_served_bijection(openapi_schema: dict[str, Any]) -> None:
    """Falsifier harness: ``assert`` must biject to POST /assertions operationId."""
    route = TYPED_ROUTE_BY_OP["assert"]
    paths = openapi_schema.get("paths", {})
    spec = paths.get(route.path, {}).get(route.method.lower())
    if not spec:
        raise AssertionError(f"missing OpenAPI path {route.method} {route.path}")
    oid = spec.get("operationId")
    if oid != route.operation_id:
        raise AssertionError(
            f"assert operationId drift: expected {route.operation_id!r}, got {oid!r}"
        )
    if oid not in {v for v in served_operation_ids().values()}:
        raise AssertionError(f"operationId {oid!r} not in served map")


def find_reachable_unserved_violations(
    reachable_ops: frozenset[str],
    *,
    op_specs: dict[str, str] | None = None,
) -> list[str]:
    """Return reachable ops lacking a served route and not untypeable (A1).

    During strangler, violations are expected until cutover partition is applied.
    """
    ops = op_specs or _OP_SPECS
    served = frozenset(TYPED_ROUTE_BY_OP) & frozenset(ops)
    allowed = served | (UNTYPEABLE_OPS & frozenset(ops))
    return sorted(reachable_ops - allowed)
