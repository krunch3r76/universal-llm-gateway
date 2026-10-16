"""Dict-form Stargate keys project client_id onto the cloud chat header."""

from __future__ import annotations

import httpx
import pytest

from systems.cloud.forwarder import (
    OPEN_WEBUI_CLIENT_ID,
    ULG_CLIENT_HEADER,
    CloudProxyClient,
    cloud_chat_forward_headers,
)
from fastapi import Request

from systems.proxy.authorization import (
    APIKeyManager,
    bind_admitted_client_id,
    create_auth_dependency,
    current_admitted_client_id,
)


def test_dict_key_records_client_id_string_and_hash_do_not() -> None:
    manager = APIKeyManager(
        {
            "enabled": True,
            "ip_whitelist": {"enabled": False},
            "api_keys": [
                {"key": "dict-key-fixture", "client_id": "open-webui"},
                "plain-key-fixture",
                {"key": "other-key-fixture", "client_id": "cursor"},
            ],
            "hashed_api_keys": ["abc123hash"],
        }
    )
    assert manager.client_id_for_key("dict-key-fixture") == "open-webui"
    assert manager.client_id_for_key("plain-key-fixture") is None
    assert manager.client_id_for_key("other-key-fixture") == "cursor"
    assert manager.client_id_for_key(None) is None


def test_header_only_for_open_webui_and_not_in_body_shape() -> None:
    headers = cloud_chat_forward_headers(OPEN_WEBUI_CLIENT_ID)
    assert headers[ULG_CLIENT_HEADER] == "open-webui"
    assert "open-webui" not in {"Content-Type"}
    assert ULG_CLIENT_HEADER not in cloud_chat_forward_headers(None)
    assert ULG_CLIENT_HEADER not in cloud_chat_forward_headers("cursor")
    assert ULG_CLIENT_HEADER not in cloud_chat_forward_headers("")


@pytest.mark.asyncio
async def test_forward_request_emits_header_without_body_field() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"id": "ok"})

    client = CloudProxyClient("http://cloud-proxy.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://cloud-proxy.test",
    )
    body = {"model": "anthropic/claude-fictional-99", "messages": []}
    response = await client.forward_request(body, "req-1", client_id="open-webui")
    assert response.status_code == 200
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-ulg-client"] == "open-webui"
    raw = str(captured["body"])
    assert "X-ULG-Client" not in raw
    assert "oauth_client_id" not in raw
    await client.close()


@pytest.mark.asyncio
async def test_string_key_forward_omits_header() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["client"] = request.headers.get("x-ulg-client", "")
        return httpx.Response(200, json={"id": "ok"})

    client = CloudProxyClient("http://cloud-proxy.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://cloud-proxy.test",
    )
    bind_admitted_client_id(None)
    response = await client.forward_request(
        {"model": "anthropic/claude-fictional-99"}, "req-2"
    )
    assert response.status_code == 200
    assert captured["client"] == ""
    await client.close()


def _whitelist_request(host: str, bearer: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if bearer is not None:
        headers.append((b"authorization", f"Bearer {bearer}".encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/chat/completions",
            "raw_path": b"/v1/chat/completions",
            "query_string": b"",
            "headers": headers,
            "client": (host, 12345),
            "server": ("test", 80),
        }
    )


@pytest.mark.asyncio
async def test_whitelist_binds_labelled_key_and_allows_missing_or_unknown() -> None:
    """Whitelist still admits without a key check; a dict key binds client_id."""
    manager = APIKeyManager(
        {
            "enabled": True,
            "ip_whitelist": {"enabled": True},
            "api_keys": [{"key": "dict-key-fixture", "client_id": "open-webui"}],
        }
    )
    get_current_user = create_auth_dependency(manager)

    labelled = await get_current_user(
        _whitelist_request("127.0.0.1", "dict-key-fixture")
    )
    assert labelled["authenticated"] is True
    assert labelled["auth_method"] == "ip_whitelist"
    assert labelled["client_id"] == "open-webui"
    assert current_admitted_client_id() == "open-webui"

    missing = await get_current_user(_whitelist_request("127.0.0.1"))
    assert missing["authenticated"] is True
    assert missing["auth_method"] == "ip_whitelist"
    assert missing["client_id"] is None
    assert current_admitted_client_id() is None

    unknown = await get_current_user(
        _whitelist_request("127.0.0.1", "unknown-key-fixture")
    )
    assert unknown["authenticated"] is True
    assert unknown["auth_method"] == "ip_whitelist"
    assert unknown["client_id"] is None
    assert current_admitted_client_id() is None
