"""GET /v1/grok/window-routing — host-resident read-only routing snapshot."""

from __future__ import annotations

from typing import Any

from cursor_bridge.grok_routing_snapshot import build_grok_routing_read_snapshot


def grok_window_routing_http_response() -> tuple[int, dict[str, Any]]:
    """Build status code and JSON body for the grok window routing read route."""
    payload = build_grok_routing_read_snapshot()
    if payload.get("ok"):
        return 200, payload
    return 422, payload
