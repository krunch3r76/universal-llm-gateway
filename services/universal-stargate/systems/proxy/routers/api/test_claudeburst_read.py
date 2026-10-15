"""K38 Step 1 — claudeburst GET-only Stargate relay."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.proxy.dependencies import get_auth_dependency, get_proxy
from systems.proxy.routers.api import claudeburst_read as claudeburst_read_routes
from systems.proxy.routers.api.claudeburst_read import (
    allowed_get_prefixes,
    router,
)


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

    async def send(self, request: httpx.Request, **kwargs: object) -> httpx.Response:
        del kwargs
        response = self._responder(request)
        response._request = request  # type: ignore[attr-defined]
        return response

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    mock_proxy = MagicMock()
    app.dependency_overrides[get_proxy] = lambda: mock_proxy
    app.dependency_overrides[get_auth_dependency] = lambda: {"authenticated": True}
    return TestClient(app)


def test_allowlist_covers_step1_verbs() -> None:
    prefixes = set(allowed_get_prefixes())
    for path in (
        "/trades",
        "/pnl",
        "/risk",
        "/signals",
        "/heartbeats",
        "/position-geometry",
        "/price-history/ohlcv",
        "/price-history/health",
        "/ledger/integrity-audit",
    ):
        assert path in prefixes


def test_rejects_non_get(client: TestClient) -> None:
    resp = client.post("/api/v1/claudeburst/read/trades")
    assert resp.status_code == 405


def test_rejects_disallowed_path(client: TestClient) -> None:
    resp = client.get("/api/v1/claudeburst/read/health")
    assert resp.status_code == 404


def test_proxies_allowed_get(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, content=b'{"ok":true}')

    monkeypatch.setattr(
        claudeburst_read_routes,
        "make_async_client",
        lambda *args, **kwargs: _Upstream(responder),
    )
    resp = client.get("/api/v1/claudeburst/read/trades?limit=1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert seen == ["/trades"]
