"""Cursor Glass host labels (config table only; no CDP or bridge GUI)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_SCHEMA = 1


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


def row_for_gui_host(
    gui_host: str | None, *, config: dict[str, Any] | None = None
) -> dict[str, Any] | None:
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


def is_cursor_xvfb_host(gui_host: str | None) -> bool:
    """True when ``gui_host`` names a Glass Cursor Agents CDP target.

    ``None`` and ``''`` are treated as the default ``:8`` row (both return True
    when that row is present in the window table).
    """
    return row_for_gui_host(gui_host) is not None


def all_gui_host_aliases() -> frozenset[str]:
    """Every gui_host label and alias the window table accepts."""
    cfg = load_config()
    idx = _alias_index(cfg)
    return frozenset(idx.keys())


__all__ = [
    "all_gui_host_aliases",
    "config_path",
    "is_cursor_xvfb_host",
    "load_config",
    "row_for_gui_host",
]
