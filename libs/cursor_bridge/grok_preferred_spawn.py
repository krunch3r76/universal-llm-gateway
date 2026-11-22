"""Resolve ``preferred_spawn`` from live grok window routing state."""

from __future__ import annotations

import json
from typing import Any

from cursor_bridge import grok_window_bindings as _bindings
from cursor_bridge.grok_window_bindings import _window_ids, load_config, load_routing

_ROUTING_READ_ERRORS = (ValueError, OSError, json.JSONDecodeError)


def _blocked(reason: str) -> dict[str, Any]:
    return {"ok": False, "code": "BLOCKED", "reason": reason}


def _routing_failure_reason(config: dict[str, Any], exc: BaseException) -> str:
    if isinstance(exc, (OSError, json.JSONDecodeError)):
        return "routing unreadable"
    if isinstance(exc, ValueError):
        path = _bindings.state_path()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                return "routing unreadable"
            if isinstance(raw, dict) and raw.get("preferred_spawn") is not None:
                disp = str(raw["preferred_spawn"]).strip().lstrip(":")
                if disp and disp not in _window_ids(config):
                    return "preferred_spawn invalid"
        return "routing unreadable"
    return "routing unreadable"


def _preferred_spawn_raw(state: dict[str, Any]) -> str | None:
    if "preferred_spawn" not in state:
        return None
    raw = state.get("preferred_spawn")
    if raw is None:
        return None
    disp = str(raw).strip().lstrip(":")
    return disp or None


def preferred_spawn_for(_agent: str | None = None) -> dict[str, Any]:
    """Return display/cdp_port for counting spawn; BLOCKED when unset or invalid."""
    try:
        config = load_config()
    except _ROUTING_READ_ERRORS:
        return _blocked("routing unreadable")
    try:
        state = load_routing()
    except _ROUTING_READ_ERRORS as exc:
        return _blocked(_routing_failure_reason(config, exc))
    ports = _window_ids(config)
    display = _preferred_spawn_raw(state)
    if display is None:
        return {
            "ok": False,
            "code": "BLOCKED",
            "reason": "preferred_spawn unset",
        }
    if display not in ports:
        return {
            "ok": False,
            "code": "BLOCKED",
            "reason": "preferred_spawn invalid",
        }
    return {
        "ok": True,
        "display": display,
        "cdp_port": ports[display],
        "epoch": state["epoch"],
        "as_of": state["as_of"],
        "source": state["source"],
        "scope": state["scope"],
    }


__all__ = ["preferred_spawn_for"]