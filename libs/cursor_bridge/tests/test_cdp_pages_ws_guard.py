"""Websocket dial guard for cursor_glass edge path (allowlist supplied by caller)."""

from __future__ import annotations

import pytest
from cursor_bridge.cdp_pages import (
    CdpWebSocketRefusedError,
    assert_loopback_cdp_http_origin,
    assert_web_socket_debugger_url_allowed,
)
from cursor_bridge.grok_window_bindings import configured_grok_cdp_ports

pytestmark = pytest.mark.offline

_EDGE_PORTS = configured_grok_cdp_ports()


def test_http_origin_must_be_loopback() -> None:
    with pytest.raises(CdpWebSocketRefusedError, match="not loopback"):
        assert_loopback_cdp_http_origin("http://10.0.0.5:9339")


@pytest.mark.parametrize(
    "origin",
    ["http://127.0.0.1:9339", "http://localhost:9340", "http://[::1]:9339"],
)
def test_loopback_http_origins_pass(origin: str) -> None:
    assert_loopback_cdp_http_origin(origin)


def test_ws_port_must_match_http_port_on_edge_entry() -> None:
    with pytest.raises(CdpWebSocketRefusedError, match="does not match"):
        assert_web_socket_debugger_url_allowed(
            "ws://127.0.0.1:9340/devtools/page/1",
            http_origin="http://127.0.0.1:9339",
            allowed_ports=_EDGE_PORTS,
        )


def test_ws_non_loopback_host_refused_on_edge_entry() -> None:
    with pytest.raises(CdpWebSocketRefusedError, match="not loopback"):
        assert_web_socket_debugger_url_allowed(
            "ws://example.com:9339/devtools/page/1",
            http_origin="http://127.0.0.1:9339",
            allowed_ports=_EDGE_PORTS,
        )


def test_ws_port_not_on_allowlist_refused_on_edge_entry() -> None:
    with pytest.raises(CdpWebSocketRefusedError, match="not allowlisted"):
        assert_web_socket_debugger_url_allowed(
            "ws://127.0.0.1:9338/devtools/page/1",
            http_origin="http://127.0.0.1:9338",
            allowed_ports=_EDGE_PORTS,
        )


def test_matching_allowlisted_ws_passes_on_edge_entry() -> None:
    assert_web_socket_debugger_url_allowed(
        "ws://127.0.0.1:9339/devtools/page/1",
        http_origin="http://127.0.0.1:9339",
        allowed_ports=_EDGE_PORTS,
    )


@pytest.mark.parametrize(
    ("ws_url", "http_origin", "pattern"),
    [
        (
            "ws://127.0.0.1.evil:9339/devtools/page/1",
            "http://127.0.0.1:9339",
            "not loopback",
        ),
        (
            "ws://0.0.0.0:9339/devtools/page/1",
            "http://127.0.0.1:9339",
            "not loopback",
        ),
        (
            "ws://127.0.0.1:9339/devtools/page/1",
            "http://127.0.0.1:9340",
            "does not match",
        ),
        (
            "ws://127.0.0.1/devtools/page/1",
            "http://127.0.0.1:9339",
            "has no port",
        ),
    ],
)
def test_edge_falsifier_urls_refused(
    ws_url: str, http_origin: str, pattern: str
) -> None:
    with pytest.raises(CdpWebSocketRefusedError, match=pattern):
        assert_web_socket_debugger_url_allowed(
            ws_url,
            http_origin=http_origin,
            allowed_ports=_EDGE_PORTS,
        )
