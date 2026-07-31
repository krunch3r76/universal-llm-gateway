"""Stargate triggers proxy — route registration and forward behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from systems.proxy.routers.api import router as api_v1_router
from systems.proxy.routers.api.triggers import router as triggers_router


def _route_paths(router) -> set[str]:
    return {route.path for route in router.routes}


def test_triggers_proxy_routes_registered() -> None:
    paths = _route_paths(triggers_router)
    assert "/triggers" in paths
    assert "/triggers/{path:path}" in paths


def test_api_v1_router_includes_triggers_proxy() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(api_v1_router)
    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/triggers" in paths
    assert "/api/v1/triggers/{path}" in paths


def test_triggers_proxy_forwards_list_to_worker() -> None:
    mock_response = httpx.Response(
        200,
        json={"triggers": [], "count": 0},
        request=httpx.Request("GET", "http://worker/api/v1/triggers"),
    )
    mock_client = MagicMock()
    mock_client.build_request.return_value = httpx.Request(
        "GET",
        "http://127.0.0.1:8091/api/v1/triggers",
    )
    mock_client.send = AsyncMock(return_value=mock_response)
    mock_client.aclose = AsyncMock()

    with patch(
        "systems.proxy.routers.api.triggers.make_async_client",
        return_value=mock_client,
    ):
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(api_v1_router)
        client = TestClient(app)
        resp = client.get("/api/v1/triggers?limit=5")

    assert resp.status_code == 200
    assert resp.json() == {"triggers": [], "count": 0}
    build_kwargs = mock_client.build_request.call_args.kwargs
    assert build_kwargs["url"] == "/api/v1/triggers"
    assert build_kwargs["params"]["limit"] == "5"
