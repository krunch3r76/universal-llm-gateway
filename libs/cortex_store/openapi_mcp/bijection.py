"""Reachable enum ↔ served operationId bijection helpers (A1 / AC7)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_store.dispatch_ops import _OP_SPECS
from openapi_mcp.binding import TypedRoute

from ._route_map import UNTYPEABLE_OPS, served_ops, typed_routes_from_openapi, unbound_dispatch_ops

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


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


def assert_served_bijection(
    openapi_schema: dict[str, Any],
    *,
    op_specs: dict[str, str] | None = None,
    served_ops_committed: dict[str, dict[str, str]] | None = None,
) -> None:
    """Assert every ``x-mcp`` stamp bijects with the committed served manifest."""
    from cortex_store.openapi_mcp import generated_adapter_manifest

    specs = op_specs or _OP_SPECS
    committed = (
        served_ops_committed
        if served_ops_committed is not None
        else generated_adapter_manifest.SERVED_OPS
    )
    violations: list[str] = []
    stamped_ops: dict[str, TypedRoute] = {}
    operation_id_to_op: dict[str, str] = {}

    paths = openapi_schema.get("paths") or {}
    for path, methods in paths.items():
        if not isinstance(methods, Mapping):
            continue
        for method, spec in methods.items():
            if method not in _HTTP_METHODS or not isinstance(spec, Mapping):
                continue
            xm = spec.get("x-mcp")
            if not isinstance(xm, Mapping):
                continue
            op = xm.get("op")
            if not isinstance(op, str) or not op:
                violations.append(f"x-mcp.op missing on {method.upper()} {path}")
                continue
            oid = spec.get("operationId")
            if not isinstance(oid, str) or not oid:
                violations.append(
                    f"operationId missing on x-mcp op {op!r} ({method.upper()} {path})"
                )
                continue
            tool = xm.get("tool")
            if not isinstance(tool, str) or not tool:
                violations.append(f"x-mcp.tool missing on op {op!r}")
                continue
            readonly = xm.get("readonly")
            if readonly is not None and not isinstance(readonly, bool):
                violations.append(f"x-mcp.readonly must be bool on op {op!r}")
                continue
            route = TypedRoute(
                method=method.upper(),  # type: ignore[arg-type]
                path=path,
                operation_id=oid,
                tool=tool,
                readonly=readonly,
            )
            if op in stamped_ops:
                prior = stamped_ops[op]
                violations.append(
                    f"duplicate x-mcp.op {op!r}: "
                    f"{prior.method} {prior.path} vs {route.method} {route.path}"
                )
            else:
                stamped_ops[op] = route

            if op not in specs:
                violations.append(f"phantom x-mcp op {op!r} not in _OP_SPECS")
            if op in UNTYPEABLE_OPS:
                violations.append(
                    f"untypeable op {op!r} must not carry an x-mcp stamp"
                )
            if oid != route.operation_id:
                violations.append(
                    f"{op}: operationId drift in document: "
                    f"expected {route.operation_id!r}, got {oid!r}"
                )
            prior_oid_op = operation_id_to_op.get(oid)
            if prior_oid_op is not None and prior_oid_op != op:
                violations.append(
                    f"duplicate operationId {oid!r}: ops {prior_oid_op!r} and {op!r}"
                )
            else:
                operation_id_to_op[oid] = op

            row = committed.get(op)
            if row is None:
                violations.append(f"stamped op {op!r} missing from SERVED_OPS manifest")
                continue
            expected = (row["method"], row["path"], row["operation_id"])
            actual = (route.method, route.path, route.operation_id)
            if actual != expected:
                violations.append(
                    f"{op}: manifest mismatch: document {actual!r} vs SERVED_OPS {expected!r}"
                )

    stamped_set = frozenset(stamped_ops)
    for op in sorted(frozenset(committed) - stamped_set):
        violations.append(f"manifest op {op!r} has no x-mcp stamp in OpenAPI document")

    if violations:
        raise AssertionError("\n".join(violations))


def unbound_ratchet_violations(
    openapi_schema: dict[str, Any],
    baseline: frozenset[str],
    *,
    op_specs: dict[str, str] | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(unbound − baseline, baseline − unbound)`` for ratchet tests."""
    unbound = frozenset(unbound_dispatch_ops(openapi_schema, op_specs=op_specs))
    return unbound - baseline, baseline - unbound


def assert_unbound_ratchet_no_new_unbound(
    openapi_schema: dict[str, Any],
    baseline: frozenset[str],
    *,
    op_specs: dict[str, str] | None = None,
) -> None:
    new_unbound, _ = unbound_ratchet_violations(
        openapi_schema, baseline, op_specs=op_specs
    )
    if new_unbound:
        raise AssertionError("\n".join(sorted(new_unbound)))


def assert_unbound_ratchet_baseline_not_stale(
    openapi_schema: dict[str, Any],
    baseline: frozenset[str],
    *,
    op_specs: dict[str, str] | None = None,
) -> None:
    _, stale = unbound_ratchet_violations(openapi_schema, baseline, op_specs=op_specs)
    if stale:
        raise AssertionError(
            "\n".join(
                f"{op} is now bound — delete it from UNBOUND_BASELINE"
                for op in sorted(stale)
            )
        )


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
