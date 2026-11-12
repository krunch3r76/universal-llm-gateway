"""Debug-port page list for an X11 Electron app.

The bridge driver calls this to find the renderer websocket. ``/json/list``
returns browser pages, not sidebar chats. A page titled ``Grok Bot`` or
``Cursor Agents`` is the window. The active chat title comes from the page
DOM later and is what ``selection_verdict`` compares. No wake paste happens
here.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

# Must match ``services/grok-window-edge/grok_window_edge/config.py`` ``CDP_PORT_ALLOWLIST``.
_GLASS_EDGE_CDP_PORT_ALLOWLIST: frozenset[int] = frozenset({9339, 9340})
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


class CdpWebSocketRefusedError(ValueError):
    """Websocket debugger URL refused for cursor_glass edge dial guard."""


def assert_loopback_cdp_http_origin(http_origin: str) -> None:
    """Refuse non-loopback HTTP CDP origins before any dial on the glass edge path."""
    parsed = urlparse(http_origin.strip())
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise CdpWebSocketRefusedError(
            f"CDP HTTP origin host {host!r} is not loopback for grok-window-edge"
        )


def assert_web_socket_debugger_url_allowed(ws_url: str, *, http_origin: str) -> None:
    """Refuse a browser-reported websocket URL before connect (cursor_glass dial guard)."""
    assert_loopback_cdp_http_origin(http_origin)
    http = urlparse(http_origin.strip())
    http_port = http.port
    if http_port is None:
        raise CdpWebSocketRefusedError(
            f"CDP HTTP origin {http_origin!r} has no port for grok-window-edge"
        )
    if http_port not in _GLASS_EDGE_CDP_PORT_ALLOWLIST:
        allowed = ", ".join(
            str(port) for port in sorted(_GLASS_EDGE_CDP_PORT_ALLOWLIST)
        )
        raise CdpWebSocketRefusedError(
            f"CDP port {http_port} is not allowlisted for grok-window-edge "
            f"(allowed: {allowed})"
        )
    ws = urlparse(ws_url.strip())
    ws_host = (ws.hostname or "").lower()
    if ws_host not in _LOOPBACK_HOSTS:
        raise CdpWebSocketRefusedError(
            f"websocket debugger host {ws_host!r} is not loopback"
        )
    ws_port = ws.port
    if ws_port is None:
        raise CdpWebSocketRefusedError(f"websocket debugger URL {ws_url!r} has no port")
    if ws_port != http_port:
        raise CdpWebSocketRefusedError(
            f"websocket port {ws_port} does not match HTTP origin port {http_port}"
        )
    if ws_port not in _GLASS_EDGE_CDP_PORT_ALLOWLIST:
        allowed = ", ".join(
            str(port) for port in sorted(_GLASS_EDGE_CDP_PORT_ALLOWLIST)
        )
        raise CdpWebSocketRefusedError(
            f"CDP port {ws_port} is not allowlisted for grok-window-edge "
            f"(allowed: {allowed})"
        )


def list_pages(base_url: str, *, timeout_s: float = 3.0) -> list[dict[str, str]]:
    """Return ``type=page`` targets from ``{base_url}/json/list``.

    ``base_url`` is the debug port origin, for example
    ``http://127.0.0.1:9335``. Each row has ``id``, ``title``, ``url``, and
    ``web_socket_debugger_url``. Connection failures raise ``URLError``.
    Other target types (browser, worker) are dropped.
    """
    origin = base_url.rstrip("/")
    with urlopen(f"{origin}/json/list", timeout=timeout_s) as resp:
        payload = json.load(resp)
    if not isinstance(payload, list):
        raise URLError(f"json/list returned {type(payload).__name__}, expected list")
    pages: list[dict[str, str]] = []
    for row in payload:
        if not isinstance(row, dict) or row.get("type") != "page":
            continue
        pages.append(
            {
                "id": str(row.get("id") or ""),
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "web_socket_debugger_url": str(row.get("webSocketDebuggerUrl") or ""),
            }
        )
    return pages


def page_summary(pages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Titles and ids only, for logs that must not carry debugger URLs."""
    return [
        {"id": str(page.get("id") or ""), "title": str(page.get("title") or "")}
        for page in pages
    ]
