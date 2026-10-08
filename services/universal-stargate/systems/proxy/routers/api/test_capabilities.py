"""Stargate capability index, relay, and bare-id alias."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.proxy.dependencies import get_auth_dependency, get_proxy
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
        headers = kwargs.get("headers") or {}
        return httpx.Request(
            method,
            f"http://localhost{url}",
            headers=headers if isinstance(headers, dict) else {},
        )

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
    app.dependency_overrides[get_proxy] = lambda: SimpleNamespace(
        pipeline_registry=None,
        is_pipeline_system_ready=False,
        event_bus=bus,
    )
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


def test_satellite_category_relays_ahead_of_local_member(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A satellite category relays even when the member id exists locally."""
    from types import SimpleNamespace

    from systems.pipeline.core.handlers.registry import HandlerRegistry
    from systems.proxy.dependencies import get_proxy
    from systems.proxy.routers.api.test_capability_resource_urls import (
        _registry,
        _write_root,
    )

    HandlerRegistry._ensure_initialized()
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(201, content=b'{"operation":"relayed"}')

    client, _bus = _app(tmp_path, _GOOD, monkeypatch, responder)
    root = tmp_path / "pipelines"
    _write_root(root)
    registry = _registry(root)
    client.app.dependency_overrides[get_proxy] = lambda: SimpleNamespace(
        pipeline_registry=registry,
        is_pipeline_system_ready=True,
    )
    relayed = client.get("/api/v1/capabilities/observability/demo-pipe")
    assert relayed.status_code == 201, relayed.text
    assert seen[-1].endswith("/demo-pipe")

    client.app.dependency_overrides[get_proxy] = lambda: SimpleNamespace(
        pipeline_registry=registry,
        is_pipeline_system_ready=False,
    )
    not_ready = client.get("/api/v1/capabilities/observability/demo-pipe")
    assert not_ready.status_code == 201, not_ready.text
    assert seen[-1].endswith("/demo-pipe")


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


_JOBS = """
categories:
  jobs:
    description: jobs
    implementation: satellite
    upstream: jobs
    origin_prefix: /api/v1/jobs
    auth_env: JOBS_TOKEN
"""


def test_location_prefix_rewrite(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_TOKEN", "secret-token")

    def responder(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(
            202,
            json={"href": "/api/v1/jobs/article-fetch/runs/1"},
            headers={
                "location": "/api/v1/jobs/article-fetch/runs/1",
                "link": '</api/v1/jobs/article-fetch/runs/1>; rel="monitor"',
                "content-type": "application/json",
            },
        )

    client, _bus = _app(tmp_path, _JOBS, monkeypatch, responder)
    created = client.post(
        "/api/v1/capabilities/jobs/article-fetch",
        headers={"Authorization": "Bearer inbound"},
        json={"args": {}},
    )
    assert created.status_code == 202
    assert (
        created.headers["location"]
        == "/api/v1/capabilities/jobs/article-fetch/runs/1"
    )
    assert "/api/v1/capabilities/jobs/article-fetch/runs/1" in created.headers["link"]
    assert created.json()["href"] == "/api/v1/capabilities/jobs/article-fetch/runs/1"


def test_foreign_location_is_502(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_TOKEN", "secret-token")

    def responder(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            202,
            content=b"{}",
            headers={
                "location": "https://evil.example/run",
                "content-type": "application/json",
            },
        )

    client, bus = _app(tmp_path, _JOBS, monkeypatch, responder)
    created = client.post("/api/v1/capabilities/jobs/article-fetch", json={"args": {}})
    assert created.status_code == 502
    assert created.json()["code"] == "UPSTREAM_LOCATION_INVALID"
    failed = [
        event
        for event in bus.events
        if getattr(event, "signal", "") == "capability.relay.failed"
    ]
    assert any(
        (getattr(event, "payload", {}) or {}).get("error") == "upstream_location_invalid"
        for event in failed
    )


def test_auth_env_unset_is_503(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBS_TOKEN", raising=False)

    def responder(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    client, _bus = _app(tmp_path, _JOBS, monkeypatch, responder)
    missing = client.get("/api/v1/capabilities/jobs")
    assert missing.status_code == 503
    assert "auth_env JOBS_TOKEN not configured" in missing.json()["message"]


def test_delete_non_satellite_is_405(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"ok": True})

    client, _bus = _app(tmp_path, _GOOD, monkeypatch, responder)
    denied = client.delete("/api/v1/capabilities/not-a-category/member")
    assert denied.status_code == 405
    assert denied.json()["code"] == "method_not_allowed"


def test_nested_member_non_satellite_is_404(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"ok": True})

    client, _bus = _app(tmp_path, _GOOD, monkeypatch, responder)
    missing = client.get("/api/v1/capabilities/not-a-category/member/extra")
    assert missing.status_code == 404
    assert missing.json()["code"] == "capability_not_found"


def test_relay_events_reach_proxy_bus_for_one_run(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOBS_TOKEN", "dummy-token")
    run_path = "/api/v1/jobs/bus-reply-watch/runs/r1"

    def responder(request: httpx.Request) -> httpx.Response:
        if (
            request.method == "POST"
            and request.url.path == "/api/v1/jobs/bus-reply-watch"
        ):
            return httpx.Response(
                202,
                json={"run_id": "r1"},
                headers={"location": run_path, "content-type": "application/json"},
            )
        if request.method == "GET" and request.url.path == run_path:
            return httpx.Response(200, json={"run_id": "r1", "status": "completed"})
        return httpx.Response(500, content=b"unexpected")

    client, bus = _app(tmp_path, _JOBS, monkeypatch, responder)
    created = client.post(
        "/api/v1/capabilities/jobs/bus-reply-watch",
        json={"args": {}},
    )
    assert created.status_code == 202, created.text
    fetched = client.get(created.headers["location"])
    assert fetched.status_code == 200, fetched.text
    assert not hasattr(client.app.state, "event_bus")
    completed = [
        event
        for event in bus.events
        if getattr(event, "signal", "") == "capability.relay.completed"
    ]
    assert len(completed) == 2
    payloads = [getattr(event, "payload", {}) or {} for event in completed]
    assert payloads[0]["category"] == "jobs"
    assert payloads[0]["member"] == "bus-reply-watch"
    assert payloads[0]["method"] == "POST"
    assert payloads[1]["category"] == "jobs"
    assert payloads[1]["member"] == "bus-reply-watch/runs/r1"
    assert payloads[1]["method"] == "GET"
