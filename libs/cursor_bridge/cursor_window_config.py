"""Tracked Cursor Glass window targets (:8, orion-node) for CDP and wake routing.

Host and Stargate callers keep the loopback origins in ``cursor-windows.json``.
A process on the mcp docker bridge rewrites ports 9338, 9339, and 19338 to the
gateway forwarder (orion-node Glass CDP on the host).
"""

from __future__ import annotations

from typing import Any

from host_predicates.cursor_hosts import (
    all_gui_host_aliases as _all_gui_host_aliases,
)
from host_predicates.cursor_hosts import (
    config_path,
    load_config,
    row_for_gui_host,
)

from cursor_bridge.cdp_gateway_forwarder import container_cdp_origin

_DEFAULT_ORIGIN = "http://127.0.0.1:9338"


def all_gui_host_aliases() -> frozenset[str]:
    """Every gui_host label and alias the window table accepts.

    Callers use the set to reject an unknown host before opening a debug port.
    """
    return _all_gui_host_aliases()


def cdp_origin_for_gui_host(gui_host: str | None) -> str:
    """Debug origin for one Glass host.

    The configured value stays loopback for host and Stargate callers. Inside
    the mcp container, ports 9338, 9339, and 19338 are rewritten to the docker
    gateway forwarder. The rewrite does not connect to those ports.
    """
    row = row_for_gui_host(gui_host)
    if row:
        origin = str(row.get("cdp_origin") or _DEFAULT_ORIGIN)
    else:
        origin = _DEFAULT_ORIGIN
    return container_cdp_origin(origin)


def subscription_display_for_gui_host(gui_host: str | None) -> str:
    """Display token stored on a cursor endpoint subscription for this host.

    ``:8`` maps to ``8``. An unlisted host other than orion-node also returns ``8``.
    """
    row = row_for_gui_host(gui_host)
    if row:
        return str(row.get("subscription_display") or "8")
    key = str(gui_host or "").strip().lstrip(":").lower()
    if key == "orion-node":
        return "orion-node"
    return "8"


def normalize_gui_host(gui_host: str | None) -> str:
    """Canonical gui_host label from the window table.

    An unlisted host returns ``:8``, except orion-node which stays ``orion-node``.
    """
    row = row_for_gui_host(gui_host)
    if row:
        return str(row.get("gui_host") or ":8")
    key = str(gui_host or "").strip().lstrip(":").lower()
    if key == "orion-node":
        return "orion-node"
    return ":8"


def is_configured_cursor_glass_host(gui_host: str | None) -> bool:
    """Whether ``gui_host`` is a row or alias in the tracked Glass window table."""
    return row_for_gui_host(gui_host) is not None


def unknown_gui_host_error(gui_host: str | None) -> dict[str, Any]:
    """Structured refusal for hosts outside the Cursor Glass window table.

    Grok displays (:9 / :10) and other labels must not fall back to ``:8``.
    """
    key = str(gui_host or "").strip() or ":8"
    if is_configured_cursor_glass_host(key):
        row = row_for_gui_host(key) or {}
        return {
            "ok": True,
            "gui_host": str(row.get("gui_host") or key),
            "cdp_origin": str(row.get("cdp_origin") or _DEFAULT_ORIGIN),
        }
    return {
        "ok": False,
        "code": "unknown_gui_host",
        "reason": f"unknown gui_host {key!r}",
        "gui_host": key,
    }


__all__ = [
    "all_gui_host_aliases",
    "cdp_origin_for_gui_host",
    "config_path",
    "is_configured_cursor_glass_host",
    "load_config",
    "normalize_gui_host",
    "subscription_display_for_gui_host",
    "unknown_gui_host_error",
]
