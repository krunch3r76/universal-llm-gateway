"""Tracked Cursor Glass window targets (:8, orion-node) for CDP and wake routing."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_SCHEMA = 1
_DEFAULT_ORIGIN = "http://127.0.0.1:9338"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
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
    cfg = load_config()
    idx = _alias_index(cfg)
    return frozenset(idx.keys())


def cdp_origin_for_gui_host(gui_host: str | None) -> str:
    row = _row_for_gui_host(gui_host)
    if row:
        return str(row.get("cdp_origin") or _DEFAULT_ORIGIN)
    return _DEFAULT_ORIGIN


def subscription_display_for_gui_host(gui_host: str | None) -> str:
    row = _row_for_gui_host(gui_host)
    if row:
        return str(row.get("subscription_display") or "8")
    key = str(gui_host or "").strip().lstrip(":").lower()
    if key == "orion-node":
        return "orion-node"
    return "8"


def normalize_gui_host(gui_host: str | None) -> str:
    row = _row_for_gui_host(gui_host)
    if row:
        return str(row.get("gui_host") or ":8")
    key = str(gui_host or "").strip().lstrip(":").lower()
    if key == "orion-node":
        return "orion-node"
    return ":8"


def is_configured_cursor_glass_host(gui_host: str | None) -> bool:
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
