"""CatalogMissError on chat completions is a typed 4xx, not Internal Server Error."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from llm_adapters.capability_dispatch import CatalogMissError, resolve, to_wire_dict
from starlette.requests import Request

from services.universal_cloud_proxy.catalog import (
    CatalogManager,
    CatalogModel,
    ProviderCatalog,
)
from services.universal_cloud_proxy.cloud_proxy import (
    catalog_miss_http_detail,
    chat_completions,
    raise_catalog_miss_http,
)

_MISS = CatalogMissError("anthropic/claude-fictional-99", "no_capability_card")


def test_catalog_miss_detail_is_typed_4xx_body() -> None:
    detail = catalog_miss_http_detail(_MISS)
    assert detail["miss_key"] == "anthropic/claude-fictional-99"
    assert detail["miss_reason"] == "no_capability_card"
    assert "no_capability_card" in detail["message"]
    assert detail["message"] != "Internal Server Error"
    assert "Internal Server Error" not in detail["message"]


def test_sonnet_5_dispatch_wire_facet() -> None:
    """Catalog ingest equivalent: resolve no longer omits the dispatch facet."""
    wire = to_wire_dict(resolve("anthropic/claude-sonnet-5"))
    assert wire["max_output"]["ceiling"] == 128000
    assert wire["max_output"]["over_ceiling"] == "clamp"
    assert wire["reasoning"]["value_kind"] == "adaptive"


@pytest.mark.asyncio
async def test_raise_catalog_miss_http_is_422() -> None:
    bus = SimpleNamespace(publish=AsyncMock())
    with pytest.raises(HTTPException) as caught:
        await raise_catalog_miss_http(
            exc=_MISS,
            event_bus=bus,
            provider="anthropic",
            model="anthropic/claude-fictional-99",
            adapter_type="anthropic",
        )
    assert caught.value.status_code == 422
    assert caught.value.detail["miss_reason"] == "no_capability_card"
    bus.publish.assert_awaited()


@pytest.mark.asyncio
async def test_chat_completions_catalog_miss_is_422_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.universal_cloud_proxy import cloud_proxy as cp

    mid = "anthropic/claude-fictional-99"
    mgr = CatalogManager([], {})
    mgr._catalogs["anthropic"] = ProviderCatalog(
        provider="anthropic",
        models=[CatalogModel(id=mid, provider="anthropic", max_concurrent=1)],
    )

    class _Forwarder:
        def adapter_type(self, _provider: str) -> str:
            return "anthropic"

        async def forward_chat_request(self, **_kwargs: object) -> dict[str, object]:
            raise CatalogMissError(mid, "no_capability_card")

    monkeypatch.setattr(cp, "_get_catalog", lambda: mgr)
    monkeypatch.setattr(cp, "_get_forwarder", lambda: _Forwarder())
    monkeypatch.setattr(cp, "_get_event_bus", lambda: None)
    monkeypatch.setattr(cp, "_get_mcp_executor", lambda: None)

    body = (
        b'{"model":"anthropic/claude-fictional-99",'
        b'"messages":[{"role":"user","content":"hi"}]}'
    )
    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    with pytest.raises(HTTPException) as caught:
        await chat_completions(Request(scope, receive))
    assert caught.value.status_code == 422
    assert caught.value.detail["miss_reason"] == "no_capability_card"
    assert caught.value.detail != "Internal Server Error"
    assert "Internal Server Error" not in str(caught.value.detail)
