"""Stargate capability index, relay, and bare-id alias."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.proxy.dependencies import get_auth_dependency
from systems.proxy.routers.api import capabilities as capability_routes
from systems.proxy.routers.api.capabilities import router
from systems.proxy.routers.api.capability_vocabulary import reset_vocabulary_cache


class _Bus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish_nowait(self, event: object) -> None:
        self.events.append(event)


class _Upstream:
    def __init__(self, responder) -> None:  # type: ignore[no-untyped-def]
        self._responder = responder

    def build_request(self, **kwargs: object) -> httpx.Request:
        method = str(kwargs["method"])
        url = str(kwargs["url"])
        return httpx.Request(method, f"http://localhost{url}")

    async def send(self, request: httpx.Request) -> httpx.Response:
        return self._responder(request)

    async def get(self, url: str) -> httpx.Response:
        return self._responder(httpx.Request("GET", f"http://localhost{url}"))

    async def aclose(self) -> None:
        return None


def _app(tmp_path, body: str, monkeypatch, responder) -> tuple[TestClient, _Bus]:  # type: ignore[no-untyped-def]
    path = tmp_path / "capabilities.yaml"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("CAPABILITIES_YAML", str(path))
    reset_vocabulary_cache()
    monkeypatch.setattr(
        capability_routes,
        "make_async_client",
        lambda *args, **kwargs: _Upstream(responder),
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_auth_dependency] = lambda: {}
    bus = _Bus()
    app.state.event_bus = bus
    return TestClient(app), bus


_GOOD = """
categories:
  observability:
    description: telemetry
    implementation: satellite
    upstream: events_query
    origin_prefix: /api/v1/observability
  broken:
    description: bad
    implementation: satellite
    upstream: no_such_upstream
    origin_prefix: /api/v1/nope
"""


def test_index_relay_alias_and_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = {
        "category": "observability",
        "members": [
            {
                "name": "recent-failures",
                "description": "",
                "params": {},
                "returns": "",
                "method": "GET",
            },
            {
                "name": "sql",
                "description": "",
                "params": {},
                "returns": "",
                "method": "POST",
            },
        ],
    }

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/recent-failures"):
            return httpx.Response(200, content=b'{"operation":"recent-failures"}')
        if request.url.path == "/api/v1/observability":
            return httpx.Response(200, json=listing)
        if "down" in request.url.path:
            raise httpx.ConnectError("upstream down", request=request)
        return httpx.Response(400, content=b'{"code":"INVALID_PARAMS"}')

    client, bus = _app(tmp_path, _GOOD, monkeypatch, responder)
    index = client.get("/api/v1/capabilities")
    assert index.status_code == 200
    names = {row["name"] for row in index.json()["categories"]}
    assert names == {"observability"}
    assert index.json()["catalog_skips"][0]["category"] == "broken"
    rejected = [
        event
        for event in bus.events
        if getattr(event, "signal", "") == "capability.vocabulary.rejected"
    ]
    assert rejected

    listed = client.get("/api/v1/capabilities/observability")
    assert listed.status_code == 200
    href = listed.json()["members"][0]["href"]
    assert href.endswith("/observability/recent-failures")

    alias = client.get(
        "/api/v1/capabilities/recent-failures?limit=3", follow_redirects=False
    )
    assert alias.status_code == 308
    assert alias.headers["location"].endswith(
        "/api/v1/capabilities/observability/recent-failures?limit=3"
    )
    posted = client.post("/api/v1/capabilities/sql", follow_redirects=False)
    assert posted.status_code == 308
    unknown = client.get("/api/v1/capabilities/no-such-id")
    assert unknown.status_code == 404

    for status, payload in (
        (200, b'{"operation":"recent-failures"}'),
        (400, b'{"code":"INVALID_PARAMS"}'),
        (404, b'{"code":"UNKNOWN_MEMBER"}'),
        (422, b'{"code":"OPERATION_REJECTED"}'),
        (503, b'{"code":"LOCK_WAIT"}'),
    ):
        def one(
            request: httpx.Request, status=status, payload=payload
        ) -> httpx.Response:
            del request
            return httpx.Response(
                status,
                content=payload,
                headers={"content-type": "application/json"},
            )

        monkeypatch.setattr(
            capability_routes,
            "make_async_client",
            lambda *a, **k: _Upstream(one),
        )
        relayed = client.get("/api/v1/capabilities/observability/recent-failures")
        assert relayed.status_code == status
        assert relayed.content == payload

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream down", request=request)

    monkeypatch.setattr(
        capability_routes,
        "make_async_client",
        lambda *a, **k: _Upstream(down),
    )
    failed = client.get("/api/v1/capabilities/observability/recent-failures")
    assert failed.status_code == 503
    assert failed.json()["code"] == "UPSTREAM_UNAVAILABLE"
    failed_events = [
        event
        for event in bus.events
        if getattr(event, "signal", "") == "capability.relay.failed"
    ]
    assert failed_events


def test_alias_uses_origin_listing(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"members": [{"name": "sql", "method": "POST"}]},
        )

    client, _bus = _app(tmp_path, _GOOD, monkeypatch, responder)
    posted = client.post(
        "/api/v1/capabilities/sql", content=b"{}", follow_redirects=False
    )
    assert posted.status_code == 308
