"""Derive MCP op→route bindings from OpenAPI ``x-mcp`` extensions.

Routes declare identity natively at the decorator::

    @router.post("/assertions", openapi_extra=x_mcp("assert"))

The served document is therefore the source of truth: there is no table of
``(method, path)`` pairs to keep in step with the routes. Both ``path`` and
``operationId`` are read out of the document, so a route rename cannot desync
the adapter manifest, and an op whose route carries no stamp is *absent* from
the derived manifest — which the committed manifest + ``--check`` turns into a
non-zero exit rather than a silent omission.

``inject_x_mcp`` remains for services that have not yet stamped natively
(dry-run seeding in :mod:`openapi_mcp.registry`); cortex no longer uses it.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


@dataclass(frozen=True, slots=True)
class TypedRoute:
    """One MCP-reachable operation bound to a served OpenAPI path."""

    method: Method
    path: str
    operation_id: str
    tool: str = "cortex"
    readonly: bool | None = None


def x_mcp(
    op: str,
    *,
    tool: str = "cortex",
    readonly: bool | None = None,
) -> dict[str, Any]:
    """Return the ``openapi_extra`` payload binding a route to an MCP op.

    Use at the route decorator so the served OpenAPI document carries the
    binding: ``@router.post("/assertions", openapi_extra=x_mcp("assert"))``.
    """
    if not op:
        raise ValueError("x_mcp() requires a non-empty op")
    payload: dict[str, Any] = {"tool": tool, "op": op}
    if readonly is not None:
        payload["readonly"] = readonly
    return {"x-mcp": payload}


def extract_typed_routes(openapi_schema: Mapping[str, Any]) -> dict[str, TypedRoute]:
    """Return ``op → TypedRoute`` for every path operation carrying ``x-mcp``.

    Raises ``ValueError`` on duplicate ``op`` values or missing ``operationId``.
    """
    served: dict[str, TypedRoute] = {}
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
                raise ValueError(f"x-mcp.op missing on {method.upper()} {path}")
            oid = spec.get("operationId")
            if not isinstance(oid, str) or not oid:
                raise ValueError(
                    f"operationId missing on x-mcp op {op!r} ({method.upper()} {path})"
                )
            tool = xm.get("tool")
            if not isinstance(tool, str) or not tool:
                raise ValueError(f"x-mcp.tool missing on op {op!r}")
            readonly = xm.get("readonly")
            if readonly is not None and not isinstance(readonly, bool):
                raise ValueError(f"x-mcp.readonly must be bool on op {op!r}")
            route = TypedRoute(
                method=method.upper(),  # type: ignore[arg-type]
                path=path,
                operation_id=oid,
                tool=tool,
                readonly=readonly,
            )
            if op in served:
                prior = served[op]
                raise ValueError(
                    f"duplicate x-mcp.op {op!r}: "
                    f"{prior.method} {prior.path} vs {route.method} {route.path}"
                )
            served[op] = route
    return served


def inject_x_mcp(
    openapi_schema: Mapping[str, Any],
    seed: Mapping[str, tuple[Method, str]],
    *,
    tool: str,
    readonly_by_op: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Stamp ``x-mcp`` onto matching path ops; return a deep-copied schema.

    ``seed`` maps MCP op → ``(METHOD, path)``. Raises ``RuntimeError`` when a
    seed entry has no matching OpenAPI path operation (drift / missing route).
    """
    schema = deepcopy(dict(openapi_schema))
    paths: dict[str, Any] = schema.setdefault("paths", {})
    ro_map = readonly_by_op or {}
    for op, (method, path) in sorted(seed.items()):
        path_item = paths.get(path)
        if not isinstance(path_item, dict):
            raise RuntimeError(f"OpenAPI missing path {path!r} for op {op!r}")
        spec = path_item.get(method.lower())
        if not isinstance(spec, dict):
            raise RuntimeError(f"OpenAPI missing {method} {path} for op {op!r}")
        payload: dict[str, Any] = {"tool": tool, "op": op}
        if op in ro_map:
            payload["readonly"] = ro_map[op]
        spec["x-mcp"] = payload
    return schema
