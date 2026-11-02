"""Git proxy resolves GIW base URL at request time from env."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.proxy.routers.api import router as api_v1_router


def test_git_proxy_resolves_giw_url_after_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import systems.proxy.routers.api.git as git_mod

    assert git_mod is not None
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:59998")

    mock_response = httpx.Response(
        200,
        json={"ok": True},
        request=httpx.Request("GET", "http://worker/api/v1/git/admin/status"),
    )
    mock_client = MagicMock()
    mock_client.build_request.return_value = httpx.Request(
        "GET", "http://127.0.0.1:59998/api/v1/git/admin/status"
    )
    mock_client.send = AsyncMock(return_value=mock_response)
    mock_client.aclose = AsyncMock()
    captured_bases: list[str] = []

    def _capture_make(base_url: str, **kwargs: object) -> MagicMock:
        captured_bases.append(base_url)
        return mock_client

    app = FastAPI()
    app.include_router(api_v1_router)
    client = TestClient(app)
    with patch(
        "systems.proxy.routers.api.git.make_async_client",
        side_effect=_capture_make,
    ):
        resp = client.get("/api/v1/git/admin/status")

    assert resp.status_code == 200
    assert captured_bases == ["http://127.0.0.1:59998"]
