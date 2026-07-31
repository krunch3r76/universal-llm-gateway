"""Derive MCP op→route bindings from OpenAPI ``x-mcp`` extensions.

Routes declare identity via::

    openapi_extra={"x-mcp": {"tool": "cortex", "op": "assert", "readonly": False}}

Until every served route carries a native stamp, ``inject_x_mcp`` is the
migration bridge: it writes the same extension onto a live OpenAPI document
from a seed of ``(method, path)`` pairs. ``operationId`` is always taken from
the served document — never from the seed — so id renames cannot desync the
adapter manifest.
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
            raise RuntimeError(
                f"OpenAPI missing {method} {path} for op {op!r}"
            )
        payload: dict[str, Any] = {"tool": tool, "op": op}
        if op in ro_map:
            payload["readonly"] = ro_map[op]
        spec["x-mcp"] = payload
    return schema


def stamp_fastapi_routes(
    app: Any,
    seed: Mapping[str, tuple[Method, str]],
    *,
    tool: str,
    readonly_by_op: Mapping[str, bool] | None = None,
) -> int:
    """Write ``openapi_extra['x-mcp']`` onto matching FastAPI routes.

    Returns the number of routes stamped. Used so ``app.openapi()`` carries
    bindings without editing every route decorator source file.
    """
    ro_map = readonly_by_op or {}
    by_key = {(method, path): op for op, (method, path) in seed.items()}
    stamped = 0
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not isinstance(path, str) or not methods:
            continue
        for method in methods:
            op = by_key.get((method.upper(), path))
            if op is None:
                continue
            extra = dict(getattr(route, "openapi_extra", None) or {})
            payload: dict[str, Any] = {"tool": tool, "op": op}
            if op in ro_map:
                payload["readonly"] = ro_map[op]
            extra["x-mcp"] = payload
            route.openapi_extra = extra
            stamped += 1
    return stamped
