"""Tracked Cursor Glass window targets (:8, orion-node) for CDP and wake routing.

Host and Stargate callers keep the loopback origins in ``cursor-windows.json``.
A process on the mcp docker bridge rewrites ports 9338 and 9339 to the gateway
forwarder. Orion-node stays on its own loopback port.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from cursor_bridge.cdp_gateway_forwarder import container_cdp_origin

_SCHEMA = 1
_DEFAULT_ORIGIN = "http://127.0.0.1:9338"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    """Absolute path of the tracked Glass window table at ``config/cursor-windows.json``."""
    return _repo_root() / "config" / "cursor-windows.json"


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    data = json.loads(config_path().read_text(encoding="utf-8"))
    if data.get("schema_version") != _SCHEMA:
        raise ValueError("cursor-windows.json schema_version must be 1")
    windows = data.get("windows")
    if not isinstance(windows, dict) or not windows:
        raise ValueError("cursor-windows.json windows missing")
    return data


def _alias_index(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in config["windows"].values():
        if not isinstance(row, dict):
            continue
        gui_host = str(row.get("gui_host") or "").strip()
        if gui_host:
            out[gui_host] = row
        for alias in row.get("aliases") or []:
            key = str(alias).strip()
            if key:
                out[key] = row
    return out


def _row_for_gui_host(gui_host: str | None, *, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    cfg = config if config is not None else load_config()
    key = str(gui_host or "").strip() or ":8"
    idx = _alias_index(cfg)
    if key in idx:
        return idx[key]
    lowered = key.lstrip(":").lower()
    if lowered == "8":
        return idx.get(":8") or idx.get("8")
    if lowered == "orion-node":
        return idx.get("orion-node")
    return None


def all_gui_host_aliases() -> frozenset[str]:
    """Every gui_host label and alias the window table accepts.

    Callers use the set to reject an unknown host before opening a debug port.
    """
    cfg = load_config()
    idx = _alias_index(cfg)
    return frozenset(idx.keys())


def cdp_origin_for_gui_host(gui_host: str | None) -> str:
    """Debug origin for one Glass host.

    The configured value stays loopback for host and Stargate callers. Inside
    the mcp container, ports 9338 and 9339 are rewritten to the docker gateway
    forwarder. The rewrite does not connect to those ports.
    """
    row = _row_for_gui_host(gui_host)
    if row:
        origin = str(row.get("cdp_origin") or _DEFAULT_ORIGIN)
    else:
        origin = _DEFAULT_ORIGIN
    return container_cdp_origin(origin)


def subscription_display_for_gui_host(gui_host: str | None) -> str:
    """Display token stored on a cursor endpoint subscription for this host.

    ``:8`` maps to ``8``. An unlisted host other than orion-node also returns ``8``.
    """
    row = _row_for_gui_host(gui_host)
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
    row = _row_for_gui_host(gui_host)
    if row:
        return str(row.get("gui_host") or ":8")
    key = str(gui_host or "").strip().lstrip(":").lower()
    if key == "orion-node":
        return "orion-node"
    return ":8"


def is_configured_cursor_glass_host(gui_host: str | None) -> bool:
    """Whether ``gui_host`` is a row or alias in the tracked Glass window table."""
    return _row_for_gui_host(gui_host) is not None


__all__ = [
    "all_gui_host_aliases",
    "cdp_origin_for_gui_host",
    "config_path",
    "is_configured_cursor_glass_host",
    "load_config",
    "normalize_gui_host",
    "subscription_display_for_gui_host",
]
