"""Read-only grok window routing projection for host GET and MCP relay."""

from __future__ import annotations

import json
from typing import Any

from admission_common.qualified_scalar import AuthorityClass, SurfaceDecl, seal

from cursor_bridge import grok_window_bindings as _bindings
from cursor_bridge.grok_window_bindings import (
    _builtin_state,
    _window_ids,
    binding_for_from_state,
    load_config,
    validate_state,
)

_SNAPSHOT_SOURCE = "grok_routing_snapshot"
_SURFACE = "grok_routing_read"


def _error(
    *,
    code: str,
    message: str,
    retryable: bool = False,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "source": _SNAPSHOT_SOURCE,
            "retryable": retryable,
            "data": data or {},
        },
    }


def _agent_window_row(
    *,
    config: dict[str, Any],
    display: str,
    prov: dict[str, Any],
    field_prefix: str,
) -> dict[str, Any]:
    ports = _window_ids(config)
    row = config["windows"][display]
    payload: dict[str, Any] = {
        "display": display,
        "display_scope": f"{prov['scope']}/{field_prefix}",
        "display_authority": AuthorityClass.RECORDED.value,
        "cdp_port": ports[display],
        "cdp_port_scope": "config/grok-windows.json",
        "cdp_port_authority": AuthorityClass.OBSERVED.value,
        "slot": row.get("slot"),
    }
    decl = SurfaceDecl(_SURFACE)
    decl.plain("slot", reason="config row slot label for the window")
    return seal(payload, decl)


def build_grok_routing_read_snapshot() -> dict[str, Any]:
    """Return one-epoch routing projection or a structured error envelope."""
    path = _bindings.state_path()
    state_file_present = path.is_file()
    try:
        config = load_config()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _error(code="grok_windows_config_invalid", message=str(exc))

    if state_file_present:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return _error(code="routing_state_corrupt", message=str(exc))
        try:
            state = validate_state(raw, config=config)
        except ValueError as exc:
            return _error(code="routing_state_invalid", message=str(exc))
    else:
        try:
            state = validate_state(_builtin_state(config), config=config)
        except ValueError as exc:
            return _error(code="routing_state_invalid", message=str(exc))

    prov = {
        "scope": state["scope"],
        "schema_version": state["schema_version"],
        "epoch": state["epoch"],
        "as_of": state["as_of"],
        "source": state["source"],
        "updated_at": state["updated_at"],
        "updated_by": state["updated_by"],
        "state_file_present": state_file_present,
    }

    home = binding_for_from_state(state, "grok-bot")
    if home is None:
        return _error(
            code="grok_bot_binding_missing",
            message="binding_for('grok-bot') returned no row",
        )

    preferred_spawn_present = state.get("preferred_spawn") is not None
    body: dict[str, Any] = {
        "ok": True,
        "provenance": prov,
        "default_window": _agent_window_row(
            config=config,
            display=str(state["default_window"]),
            prov=prov,
            field_prefix="default_window",
        ),
        "grok_bot_home": {
            **_agent_window_row(
                config=config,
                display=str(home["display"]),
                prov=prov,
                field_prefix="grok_bot_home",
            ),
            "agent": home["agent"],
            "surface": home["surface"],
        },
        "preferred_spawn_present": preferred_spawn_present,
    }
    if preferred_spawn_present:
        body["preferred_spawn"] = _agent_window_row(
            config=config,
            display=str(state["preferred_spawn"]),
            prov=prov,
            field_prefix="preferred_spawn",
        )
    return body


__all__ = ["build_grok_routing_read_snapshot"]
