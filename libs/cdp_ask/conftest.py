"""Pytest fixtures for ``libs/cdp_ask`` offline route tests and shared helpers.

Network guards and HTTP recorders here apply only to ``test_display_chat_paste_routes``
so other package tests keep their existing CDP and socket behavior unchanged.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import pytest

_DISPLAY_CHAT_PASTE_ROUTES = "test_display_chat_paste_routes.py"

_GUARD_MSG = (
    "display_chat_paste_routes offline guard: non-loopback socket connect blocked"
)


def _host_from_address(address: object) -> str | None:
    if isinstance(address, tuple) and len(address) >= 1:
        return str(address[0])
    return None


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _assert_loopback_or_raise(address: object) -> None:
    host = _host_from_address(address)
    if host is None:
        return
    if not _is_loopback_host(host):
        raise OSError(_GUARD_MSG)


def _targets_display_chat_paste_routes(request: pytest.FixtureRequest) -> bool:
    node_path = getattr(request.node, "fspath", None)
    if node_path is None:
        return False
    return node_path.basename == _DISPLAY_CHAT_PASTE_ROUTES


@pytest.fixture(autouse=True)
def _clear_houseless_resolve_memo_autouse() -> None:
    from cdp_ask.houseless_resolve import clear_houseless_resolve_memo

    clear_houseless_resolve_memo()
    yield
    clear_houseless_resolve_memo()


@pytest.fixture(autouse=True)
def _display_chat_paste_routes_loopback_guard(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail fast on WAN/LAN connects from ``test_display_chat_paste_routes`` only."""
    if not _targets_display_chat_paste_routes(request):
        return

    real_connect = socket.socket.connect

    def _guarded_connect(self: socket.socket, address: object) -> object:
        _assert_loopback_or_raise(address)
        return real_connect(self, address)

    real_create_connection = socket.create_connection

    def _guarded_create_connection(
        address: object, *args: object, **kwargs: object
    ) -> socket.socket:
        _assert_loopback_or_raise(address)
        return real_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)


@pytest.fixture
def hub_paste_http_calls() -> list[dict[str, Any]]:
    """Recorded hub relay POST attempts (``test_display_chat_paste_routes``)."""
    return []


@pytest.fixture(autouse=True)
def _display_chat_paste_routes_hub_httpx_recorder(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    hub_paste_http_calls: list[dict[str, Any]],
) -> None:
    """Stub ``httpx`` hub relay; record method, URL, and JSON body if invoked."""
    if not _targets_display_chat_paste_routes(request):
        return

    class _RecordingAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _RecordingAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *args: object, **kwargs: object) -> object:
            hub_paste_http_calls.append(
                {
                    "method": "POST",
                    "url": str(url),
                    "json": kwargs.get("json"),
                }
            )
            raise AssertionError(
                f"unexpected live hub relay POST in offline route test: {url}"
            )

    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.httpx.AsyncClient",
        _RecordingAsyncClient,
    )
