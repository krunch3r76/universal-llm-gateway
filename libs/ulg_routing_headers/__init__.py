"""Fleet routing headers for served typed HTTP routes.

Declaration mechanism: one FastAPI dependency, ``ulg_routing_headers``,
attached with ``include_router(..., dependencies=[Depends(...)])`` on every
router that carries an ``x-mcp`` stamp. ``Idempotency-Key`` is a second
dependency, attached only to non-GET stamped operations.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Header
from fastapi.params import Depends
from fastapi.routing import APIRoute

ROUTING_HEADER_NAMES: tuple[str, ...] = (
    "X-ULG-Surface",
    "X-ULG-Seat",
    "X-ULG-Caller",
    "X-ULG-Adapter",
    "X-ULG-Thread",
    "X-ULG-Session",
    "traceparent",
)
IDEMPOTENCY_HEADER_NAME = "Idempotency-Key"

RoutingSource = Literal["header", "body", "none"]

_READ_METHODS = frozenset({"GET", "HEAD"})
_routing_context: ContextVar[RoutingContext | None] = ContextVar(
    "ulg_routing_context",
    default=None,
)


@dataclass(frozen=True)
class RoutingContext:
    """Routing facts read from request headers. All fields are optional."""

    surface: str | None = None
    seat: str | None = None
    caller: str | None = None
    adapter: str | None = None
    thread: str | None = None
    session: str | None = None
    traceparent: str | None = None


@dataclass(frozen=True)
class DispatchRouting:
    """Header-over-body resolution for POST /dispatch."""

    surface: str | None
    seat: str | None
    caller: str | None
    adapter: str | None
    thread: str | None
    session: str | None
    traceparent: str | None
    via_adapter: bool | None
    routing_source: RoutingSource
    routing_conflict: bool


def ulg_routing_headers(
    surface: str | None = Header(
        None, alias="X-ULG-Surface", convert_underscores=False
    ),
    seat: str | None = Header(None, alias="X-ULG-Seat", convert_underscores=False),
    caller: str | None = Header(None, alias="X-ULG-Caller", convert_underscores=False),
    adapter: str | None = Header(
        None, alias="X-ULG-Adapter", convert_underscores=False
    ),
    thread: str | None = Header(None, alias="X-ULG-Thread", convert_underscores=False),
    session: str | None = Header(
        None, alias="X-ULG-Session", convert_underscores=False
    ),
    traceparent: str | None = Header(
        None, alias="traceparent", convert_underscores=False
    ),
) -> RoutingContext:
    """Read fleet routing headers. Missing headers stay None (required: false)."""
    ctx = RoutingContext(
        surface=surface,
        seat=seat,
        caller=caller,
        adapter=adapter,
        thread=thread,
        session=session,
        traceparent=traceparent,
    )
    _routing_context.set(ctx)
    return ctx


def ulg_idempotency_key(
    idempotency_key: str | None = Header(
        None,
        alias=IDEMPOTENCY_HEADER_NAME,
        convert_underscores=False,
    ),
) -> str | None:
    """Declare ``Idempotency-Key``. Phase 1 records the header and does not enforce it."""
    return idempotency_key


def current_routing_context() -> RoutingContext | None:
    """Return the routing context set by ``ulg_routing_headers`` for this task."""
    return _routing_context.get()


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def resolve_dispatch_routing(
    *,
    header: RoutingContext,
    body_surface: str | None,
    body_seat: str | None,
    body_via_adapter: bool | None,
) -> DispatchRouting:
    """Prefer header values over body. Record conflict when both disagree.

    ``X-ULG-Adapter`` wins over body ``via_adapter``: a non-empty adapter name
    means the call was relayed (``via_adapter=True``). Conflict when that name
    is set and the body says ``via_adapter=False``, or when surface/seat are
    present on both sides and differ. Body-only traffic is not a conflict.
    """
    h_surface = _norm(header.surface)
    h_seat = _norm(header.seat)
    h_caller = _norm(header.caller)
    h_adapter = _norm(header.adapter)
    h_thread = _norm(header.thread)
    h_session = _norm(header.session)
    h_trace = _norm(header.traceparent)
    b_surface = _norm(body_surface)
    b_seat = _norm(body_seat)

    header_present = any(
        (h_surface, h_seat, h_caller, h_adapter, h_thread, h_session, h_trace)
    )
    body_present = (
        b_surface is not None or b_seat is not None or body_via_adapter is not None
    )
    conflict = False
    if h_surface is not None and b_surface is not None and h_surface != b_surface:
        conflict = True
    if h_seat is not None and b_seat is not None and h_seat != b_seat:
        conflict = True
    if h_adapter is not None and body_via_adapter is False:
        conflict = True

    if header_present:
        source: RoutingSource = "header"
    elif body_present:
        source = "body"
    else:
        source = "none"

    if h_adapter is not None:
        via_adapter: bool | None = True
    else:
        via_adapter = body_via_adapter

    return DispatchRouting(
        surface=h_surface if h_surface is not None else b_surface,
        seat=h_seat if h_seat is not None else b_seat,
        caller=h_caller,
        adapter=h_adapter,
        thread=h_thread,
        session=h_session,
        traceparent=h_trace,
        via_adapter=via_adapter,
        routing_source=source,
        routing_conflict=conflict,
    )


def _walk_routes(routes: Iterable[object]) -> Iterable[object]:
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _walk_routes(nested.routes)
            continue
        yield route


def _is_stamped(route: object) -> bool:
    extra = getattr(route, "openapi_extra", None)
    return isinstance(extra, dict) and "x-mcp" in extra


def router_carries_xmcp_stamp(router: APIRouter) -> bool:
    """True when this router or a router it includes has an ``x-mcp`` stamp."""
    return any(_is_stamped(route) for route in _walk_routes(router.routes))


def attach_idempotency_to_non_get_stamped_routes(router: APIRouter) -> None:
    """Append the idempotency dependency once, on non-GET stamped operations.

    Safe to call from ``create_app()`` more than once: the module-level routers
    are shared, so a second append would declare ``Idempotency-Key`` twice.
    """
    for route in _walk_routes(router.routes):
        if not isinstance(route, APIRoute) or not _is_stamped(route):
            continue
        methods = {method.upper() for method in (route.methods or set())}
        if methods <= _READ_METHODS:
            continue
        if any(
            getattr(dep, "dependency", None) is ulg_idempotency_key
            for dep in route.dependencies
        ):
            continue
        route.dependencies.append(Depends(ulg_idempotency_key))
