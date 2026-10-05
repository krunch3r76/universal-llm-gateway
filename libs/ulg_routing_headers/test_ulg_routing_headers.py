"""Routing-header declaration and dispatch header-over-body resolution."""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from ulg_routing_headers import (
    RoutingContext,
    attach_idempotency_to_non_get_stamped_routes,
    resolve_dispatch_routing,
    ulg_routing_headers,
)

_ROUTING_HEADERS = {
    "X-ULG-Surface",
    "X-ULG-Seat",
    "X-ULG-Caller",
    "X-ULG-Adapter",
    "X-ULG-Thread",
    "X-ULG-Session",
    "traceparent",
}


def _mini_app() -> FastAPI:
    router = APIRouter()

    @router.get("/read", openapi_extra={"x-mcp": {"tool": "cortex", "op": "stats"}})
    def read_op(routing: RoutingContext = Depends(ulg_routing_headers)) -> dict:
        return {"surface": routing.surface}

    @router.post("/write", openapi_extra={"x-mcp": {"tool": "cortex", "op": "assert"}})
    def write_op(routing: RoutingContext = Depends(ulg_routing_headers)) -> dict:
        return {"adapter": routing.adapter}

    assert attach_idempotency_to_non_get_stamped_routes(router) is None
    app = FastAPI()
    app.include_router(router, dependencies=[Depends(ulg_routing_headers)])
    return app


def _header_names(spec: dict) -> set[str]:
    params = spec.get("parameters") or []
    return {p["name"] for p in params if p.get("in") == "header"}


def test_mini_app_declares_exact_header_names() -> None:
    schema = _mini_app().openapi()
    get_names = _header_names(schema["paths"]["/read"]["get"])
    post_names = _header_names(schema["paths"]["/write"]["post"])
    assert get_names == _ROUTING_HEADERS
    assert post_names == _ROUTING_HEADERS | {"Idempotency-Key"}
    for method_spec in (
        schema["paths"]["/read"]["get"],
        schema["paths"]["/write"]["post"],
    ):
        for param in method_spec["parameters"]:
            if param.get("in") == "header":
                assert param.get("required", False) is False


def test_missing_headers_do_not_reject() -> None:
    client = TestClient(_mini_app())
    response = client.post("/write", json={})
    assert response.status_code == 200
    assert response.json() == {"adapter": None}


def test_resolve_header_wins_and_conflict() -> None:
    decision = resolve_dispatch_routing(
        header=RoutingContext(surface="code", adapter="mcp-server"),
        body_surface="life",
        body_seat=None,
        body_via_adapter=False,
    )
    assert decision.surface == "code"
    assert decision.via_adapter is True
    assert decision.adapter == "mcp-server"
    assert decision.routing_source == "header"
    assert decision.routing_conflict is True


def test_resolve_body_only_and_none() -> None:
    body = resolve_dispatch_routing(
        header=RoutingContext(),
        body_surface="life",
        body_seat="cursor-sdk",
        body_via_adapter=True,
    )
    assert body.routing_source == "body"
    assert body.routing_conflict is False
    assert body.surface == "life"
    assert body.via_adapter is True

    none = resolve_dispatch_routing(
        header=RoutingContext(),
        body_surface=None,
        body_seat=None,
        body_via_adapter=None,
    )
    assert none.routing_source == "none"
    assert none.routing_conflict is False
    assert none.surface is None


def _dispatch_kwargs(monkeypatch, req, routing):
    from cortex_store.routes.dispatch import dispatch

    captured: list[dict] = []

    def _fake_execute_op(tool: str, arguments: object, **kwargs: object) -> dict:
        captured.append({"tool": tool, "arguments": arguments, **kwargs})
        return {"ok": True}

    monkeypatch.setattr("cortex_store.routes.dispatch.execute_op", _fake_execute_op)
    result = dispatch(req, routing=routing)
    assert result == {"ok": True}
    assert len(captured) == 1
    return captured[0]


def test_dispatch_header_wins_over_body(monkeypatch) -> None:
    from cortex_store.routes.dispatch import DispatchRequest

    recorded = _dispatch_kwargs(
        monkeypatch,
        DispatchRequest(
            tool="stats",
            arguments={},
            surface="life",
            seat="body-seat",
            via_adapter=True,
        ),
        RoutingContext(surface="code", seat="cursor-sdk", adapter="mcp-server"),
    )
    assert recorded["surface"] == "code"
    assert recorded["seat"] == "cursor-sdk"
    assert recorded["adapter"] == "mcp-server"
    assert recorded["via_adapter"] is True
    assert recorded["routing_source"] == "header"


def test_dispatch_routing_conflict_recorded(monkeypatch) -> None:
    from cortex_store.routes.dispatch import DispatchRequest

    recorded = _dispatch_kwargs(
        monkeypatch,
        DispatchRequest(tool="stats", arguments={}, surface="life", via_adapter=False),
        RoutingContext(
            surface="code",
            adapter="mcp-server",
            caller="static",
            thread="15152",
            traceparent="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
        ),
    )
    assert recorded["routing_conflict"] is True
    assert recorded["caller"] == "static"
    assert recorded["thread"] == "15152"
    assert recorded["trace"].startswith("00-")
    assert recorded["surface"] == "code"
    assert recorded["via_adapter"] is True


def test_dispatch_routing_source_none_without_headers(monkeypatch) -> None:
    from cortex_store.routes.dispatch import DispatchRequest

    recorded = _dispatch_kwargs(
        monkeypatch,
        DispatchRequest(tool="stats", arguments={}),
        RoutingContext(),
    )
    assert recorded["routing_source"] == "none"
    assert recorded["routing_conflict"] is False
    assert recorded["surface"] is None
    assert recorded["via_adapter"] is None


def test_dispatch_body_fields_flow_without_headers(monkeypatch) -> None:
    """Old mcp-server (body only) against new cortex_api during the restart window."""
    from cortex_store.routes.dispatch import DispatchRequest

    recorded = _dispatch_kwargs(
        monkeypatch,
        DispatchRequest(
            tool="stats",
            arguments={"entity_id": "todo:x"},
            surface="life",
            seat="cursor-sdk",
            via_adapter=True,
        ),
        RoutingContext(),
    )
    assert recorded["routing_source"] == "body"
    assert recorded["routing_conflict"] is False
    assert recorded["surface"] == "life"
    assert recorded["seat"] == "cursor-sdk"
    assert recorded["via_adapter"] is True
    assert recorded["arguments"] == {"entity_id": "todo:x"}
