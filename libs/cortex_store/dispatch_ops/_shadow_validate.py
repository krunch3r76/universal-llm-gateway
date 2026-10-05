"""Shadow-check dispatch args against the stamped route. Never affects the result.

The route table is the process's existing ``cortex_store.main.app`` (built once
at import by ``create_app()``). Lookup reads included routers' route objects.
It does not call ``create_app()`` again and does not enter the app lifespan, so
a live request does not open a second database, start the drift monitor, or
write config. Contracts are cached by OpenAPI operationId on first use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError
from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory

from ._shared import record

_MISSING = object()

# tool → operationId. None means the index has been built.
_tool_to_oid: dict[str, str] | None = None
_routes_by_oid: dict[str, APIRoute] = {}
_contracts: dict[str, _RouteContract] = {}
_index_builds = 0
_contract_builds = 0


def reset_shadow_cache() -> None:
    """Drop the operationId cache. Tests use this to observe first-use cost."""
    global _tool_to_oid, _index_builds, _contract_builds
    _tool_to_oid = None
    _routes_by_oid.clear()
    _contracts.clear()
    _index_builds = 0
    _contract_builds = 0


@dataclass(frozen=True)
class _Param:
    """One path or query parameter, accepted under any of ``names``."""

    names: tuple[str, ...]
    required: bool
    field: Any


@dataclass(frozen=True)
class _RouteContract:
    operation_id: str
    params: tuple[_Param, ...]
    body_model: type[BaseModel] | None
    body_names: frozenset[str]
    allowed: frozenset[str]

    def error_locs(self, parsed: dict[str, Any]) -> list[str]:
        locs: list[str] = []
        for key in parsed:
            if key not in self.allowed:
                locs.append(str(key))
        for param in self.params:
            present = _first_present(parsed, param.names)
            if present is _MISSING:
                if param.required:
                    locs.append(param.names[0])
                continue
            _value, errors = param.field.validate(present, {}, loc=(param.names[0],))
            for err in errors:
                locs.append(_dot(err.get("loc"), param.names[0]))
        if self.body_model is not None:
            subset = {k: v for k, v in parsed.items() if k in self.body_names}
            try:
                self.body_model.model_validate(subset)
            except ValidationError as exc:
                for err in exc.errors():
                    locs.append(_dot(err.get("loc"), "body"))
        return _dedupe(locs)


def shadow_validate(tool: str, parsed: object, caller: str | None) -> None:
    """Record match, would_reject, or unmapped. Swallows every exception."""
    try:
        if not isinstance(parsed, dict):
            _emit(tool, "would_reject", ["arguments"], caller)
            return
        oid = _operation_id_for(tool)
        if oid is None:
            _emit(tool, "unmapped", [], caller)
            return
        locs = _contract_for(oid).error_locs(parsed)
        _emit(tool, "match" if not locs else "would_reject", locs, caller)
    except Exception:
        return


def _operation_id_for(tool: str) -> str | None:
    index = _load_tool_index()
    return index.get(tool)


def _contract_for(operation_id: str) -> _RouteContract:
    global _contract_builds
    cached = _contracts.get(operation_id)
    if cached is not None:
        return cached
    _contract_builds += 1
    contract = _contract_from_route(_routes_by_oid[operation_id], operation_id)
    _contracts[operation_id] = contract
    return contract


def _load_tool_index() -> dict[str, str]:
    global _tool_to_oid, _index_builds
    if _tool_to_oid is not None:
        return _tool_to_oid
    _index_builds += 1
    from cortex_store.main import app as live_app

    mapping: dict[str, str] = {}
    for op, oid, route in _iter_stamped_routes(live_app):
        if op in mapping:
            continue
        mapping[op] = oid
        _routes_by_oid.setdefault(oid, route)
    _tool_to_oid = mapping
    return mapping


def _iter_stamped_routes(app: Any):
    """Yield ``(mcp op, operationId, route)`` from the already-built app.

    Included routers stay on ``app.routes`` as wrappers; their original route
    objects carry ``x-mcp`` and the path/query/body dependant. Header
    dependencies added at include time (fleet routing, idempotency) are not
    part of the dispatch argument object and are not validated here.
    """
    seen: set[int] = set()
    for route in getattr(app, "routes", ()):
        original = getattr(route, "original_router", None)
        candidates = list(getattr(original, "routes", ()))
        if isinstance(route, APIRoute):
            candidates.append(route)
        for sub in candidates:
            if not isinstance(sub, APIRoute) or id(sub) in seen:
                continue
            seen.add(id(sub))
            extra = sub.openapi_extra or {}
            xm = extra.get("x-mcp") if isinstance(extra, dict) else None
            if not isinstance(xm, dict):
                continue
            op = xm.get("op")
            oid = sub.operation_id or sub.unique_id
            if not isinstance(op, str) or not op or not isinstance(oid, str) or not oid:
                continue
            yield op, oid, sub


def _contract_from_route(route: APIRoute, operation_id: str) -> _RouteContract:
    dependant = route.dependant
    params: list[_Param] = []
    allowed: set[str] = set()
    for field in (*dependant.path_params, *dependant.query_params):
        names = _field_names(field)
        allowed.update(names)
        params.append(
            _Param(
                names=names,
                required=bool(field.field_info.is_required()),
                field=field,
            )
        )
    body_model, body_names = _body_spec(dependant.body_params)
    allowed.update(body_names)
    return _RouteContract(
        operation_id=operation_id,
        params=tuple(params),
        body_model=body_model,
        body_names=body_names,
        allowed=frozenset(allowed),
    )


def _body_spec(body_params: list[Any]) -> tuple[type[BaseModel] | None, frozenset[str]]:
    if len(body_params) != 1:
        names: set[str] = set()
        for field in body_params:
            names.update(_field_names(field))
        return None, frozenset(names)
    field = body_params[0]
    model = _as_model(field.field_info.annotation)
    if model is None:
        return None, frozenset(_field_names(field))
    names = set()
    for fname, finfo in model.model_fields.items():
        names.add(fname)
        alias = getattr(finfo, "alias", None)
        if isinstance(alias, str) and alias:
            names.add(alias)
        validation_alias = getattr(finfo, "validation_alias", None)
        if isinstance(validation_alias, str) and validation_alias:
            names.add(validation_alias)
    return model, frozenset(names)


def _as_model(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _field_names(field: Any) -> tuple[str, ...]:
    names = [field.name]
    alias = getattr(field, "alias", None)
    if isinstance(alias, str) and alias and alias not in names:
        names.append(alias)
    validation_alias = getattr(field, "validation_alias", None)
    if (
        isinstance(validation_alias, str)
        and validation_alias
        and validation_alias not in names
    ):
        names.append(validation_alias)
    return tuple(names)


def _first_present(parsed: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in parsed:
            return parsed[name]
    return _MISSING


def _dot(loc: object, fallback: str) -> str:
    if not isinstance(loc, tuple | list) or not loc:
        return fallback
    parts = [str(part) for part in loc if part != "body"]
    return ".".join(parts) if parts else fallback


def _dedupe(locs: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for loc in locs:
        if loc in seen:
            continue
        seen.add(loc)
        ordered.append(loc)
    return ordered


@event_factory
def mcp_cortex_dispatch_shadow(
    *,
    tool: str,
    outcome: str,
    error_locs: list[str],
    caller: str | None,
) -> Event:
    """mcp.cortex.dispatch.shadow — dispatch args vs the stamped route; result unchanged."""
    ev = Event(
        signal="mcp.cortex.dispatch.shadow",
        role="observation",
        scope="global",
        payload={
            "tool": tool,
            "outcome": outcome,
            "error_locs": error_locs,
            "caller": caller,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


def _emit(tool: str, outcome: str, error_locs: list[str], caller: str | None) -> None:
    mcp_cortex_dispatch_shadow(
        tool=tool,
        outcome=outcome,
        error_locs=error_locs,
        caller=caller,
    )
