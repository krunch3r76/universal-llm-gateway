"""Websocket dial guard for cursor_glass (matches grok-window-edge allowlist)."""

from __future__ import annotations

import pytest
from cursor_bridge.cdp_pages import (
    CdpWebSocketRefusedError,
    assert_loopback_cdp_http_origin,
    assert_web_socket_debugger_url_allowed,
)

pytestmark = pytest.mark.offline


def test_http_origin_must_be_loopback() -> None:
    with pytest.raises(CdpWebSocketRefusedError, match="not loopback"):
        assert_loopback_cdp_http_origin("http://10.0.0.5:9339")


@pytest.mark.parametrize("origin", ["http://127.0.0.1:9339", "http://localhost:9340", "http://[::1]:9339"])
def test_loopback_http_origins_pass(origin: str) -> None:
    assert_loopback_cdp_http_origin(origin)


def test_ws_port_must_match_http_port() -> None:
    with pytest.raises(CdpWebSocketRefusedError, match="does not match"):
        assert_web_socket_debugger_url_allowed(
            "ws://127.0.0.1:9340/devtools/page/1",
            http_origin="http://127.0.0.1:9339",
        )


def test_ws_non_loopback_host_refused() -> None:
    with pytest.raises(CdpWebSocketRefusedError, match="not loopback"):
        assert_web_socket_debugger_url_allowed(
            "ws://example.com:9339/devtools/page/1",
            http_origin="http://127.0.0.1:9339",
        )


def test_ws_port_not_on_allowlist_refused() -> None:
    with pytest.raises(CdpWebSocketRefusedError, match="not allowlisted"):
        assert_web_socket_debugger_url_allowed(
            "ws://127.0.0.1:9338/devtools/page/1",
            http_origin="http://127.0.0.1:9338",
        )


def test_matching_allowlisted_ws_passes() -> None:
    assert_web_socket_debugger_url_allowed(
        "ws://127.0.0.1:9339/devtools/page/1",
        http_origin="http://127.0.0.1:9339",
    )
