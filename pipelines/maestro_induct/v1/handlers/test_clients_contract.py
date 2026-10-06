"""_clients route contract tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from . import _clients

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_transport(monkeypatch):
    calls: list[tuple[str, dict[str, Any] | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"turns": []})

    class Holder:
        async def __aenter__(self):
            self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return self.client

        async def __aexit__(self, *exc):
            await self.client.aclose()

    monkeypatch.setattr(_clients, "make_async_client", lambda *a, **k: Holder())
    return calls


async def test_deadline_exceeded_skips_request(mock_transport) -> None:
    payload, status = await _clients.bus_get("/turns", deadline_epoch=_clients.now_epoch() - 1)
    assert status == 504
    assert payload["error"]["code"] == "deadline_exceeded"
    assert mock_transport == []


async def test_files_root_unset(monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_FILES_ROOT", raising=False)
    res = _clients.read_cortex_file("cortex://notes/x.md")
    assert isinstance(res, dict)
    assert res["error"]["kind"] == "files_root_unset"


async def test_read_cortex_file_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    res = _clients.read_cortex_file("cortex://notes/missing.md")
    assert res["error"]["kind"] == "file_missing"


async def test_client_timeouts(mock_transport, monkeypatch) -> None:
    recorded: list[float | None] = []

    class Holder:
        def __init__(self, timeout):
            recorded.append(timeout)

        async def __aenter__(self):
            self.client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
            )
            return self.client

        async def __aexit__(self, *exc):
            await self.client.aclose()

    monkeypatch.setattr(_clients, "make_async_client", lambda *a, timeout=None, **k: Holder(timeout))
    await _clients.bus_get("/turns", deadline_epoch=_clients.now_epoch() + 100)
    assert recorded[-1] == 3.0
    await _clients.bus_get("/turns", deadline_epoch=_clients.now_epoch() + 0.5)
    assert recorded[-1] <= 1.0
