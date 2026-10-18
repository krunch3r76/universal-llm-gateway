"""Grok window slots (tracked config) and live routing (state file).

Config: ``config/grok-windows.json`` names the two displays, CDP ports,
fixed roles, and the initial default/insyncerator pair.

State: ``~/.gateway/grok-window-routing.json`` is rewritten atomically.
Every document carries status-basis fields (``as_of``, ``source``,
``scope``, ``epoch``) plus ``schema_version``, ``updated_at``, and
``updated_by``. Wake routing reads this file on each turn. A missing
file behaves as ``initial_state`` from the config (epoch 0, source
``builtin``).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCOPE = "grok-window-routing"
_SCHEMA = 1


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    return _repo_root() / "config" / "grok-windows.json"


def _gateway_home() -> Path:
    """Real ``~/.gateway`` even when a dispatch overlay replaces ``HOME``."""
    home = Path.home()
    if (home / ".gateway" / "agent-bus.yaml").is_file():
        return home
    try:
        import pwd

        real = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        return home
    if (real / ".gateway").is_dir():
        return real
    return home


def state_path() -> Path:
    return _gateway_home() / ".gateway" / "grok-window-routing.json"


def _norm(slug: str) -> str:
    return slug.strip().lower().replace("_", "-").replace(" ", "-")


def load_config() -> dict[str, Any]:
    data = json.loads(config_path().read_text(encoding="utf-8"))
    if data.get("schema_version") != _SCHEMA:
        raise ValueError("grok-windows.json schema_version must be 1")
    windows = data.get("windows")
    if not isinstance(windows, dict) or not windows:
        raise ValueError("grok-windows.json windows missing")
    return data


def _window_ids(config: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, row in config["windows"].items():
        out[str(key).lstrip(":")] = int(row["cdp_port"])
    return out


def _require_window(config: dict[str, Any], value: object) -> str:
    display = str(value or "").strip().lstrip(":")
    if display not in _window_ids(config):
        raise ValueError(f"unknown grok window {value!r}")
    return display


def mailbox_canonical(slug: str) -> str | None:
    config = load_config()
    box = config["mailbox"]
    key = _norm(slug)
    members = {_norm(m) for m in box["members"]}
    if key in members:
        return str(box["canonical"])
    return None


def mailbox_members(slug: str) -> list[str]:
    config = load_config()
    box = config["mailbox"]
    key = _norm(slug)
    members = sorted({_norm(m) for m in box["members"]})
    if key in members or key == _norm(str(box["canonical"])):
        return members
    return [key] if key else []


def _builtin_state(config: dict[str, Any]) -> dict[str, Any]:
    initial = config["initial_state"]
    now = "1970-01-01T00:00:00+00:00"
    return {
        "schema_version": _SCHEMA,
        "epoch": 0,
        "as_of": now,
        "source": "builtin",
        "scope": _SCOPE,
        "updated_at": now,
        "updated_by": "builtin",
        "default_window": _require_window(config, initial["default_window"]),
        "insyncerator_window": _require_window(config, initial["insyncerator_window"]),
    }


def validate_state(raw: object, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a normalized state dict or raise ValueError."""
    cfg = config if config is not None else load_config()
    if not isinstance(raw, dict):
        raise ValueError("routing state must be an object")
    required = (
        "schema_version",
        "epoch",
        "as_of",
        "source",
        "scope",
        "updated_at",
        "updated_by",
        "default_window",
        "insyncerator_window",
    )
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"routing state missing {missing}")
    if int(raw["schema_version"]) != _SCHEMA:
        raise ValueError("routing state schema_version must be 1")
    if str(raw["scope"]) != _SCOPE:
        raise ValueError("routing state scope must be grok-window-routing")
    epoch = int(raw["epoch"])
    if epoch < 0:
        raise ValueError("routing state epoch must be >= 0")
    return {
        "schema_version": _SCHEMA,
        "epoch": epoch,
        "as_of": str(raw["as_of"]),
        "source": str(raw["source"]),
        "scope": _SCOPE,
        "updated_at": str(raw["updated_at"]),
        "updated_by": str(raw["updated_by"]),
        "default_window": _require_window(cfg, raw["default_window"]),
        "insyncerator_window": _require_window(cfg, raw["insyncerator_window"]),
    }


def load_routing() -> dict[str, Any]:
    """Validated live state, or the config's initial state when no file exists."""
    config = load_config()
    path = state_path()
    if not path.is_file():
        return validate_state(_builtin_state(config), config=config)
    return validate_state(json.loads(path.read_text(encoding="utf-8")), config=config)


def binding_for(agent: str) -> dict[str, Any] | None:
    """Window for one recipient. Fixed roles beat the moving seats."""
    config = load_config()
    state = load_routing()
    key = _norm(agent)
    ports = _window_ids(config)
    fixed_raw = config.get("fixed_roles") or {}
    fixed = {_norm(name): _require_window(config, disp) for name, disp in fixed_raw.items()}
    sync = {_norm(name) for name in config["sync_agents"]}
    default_agents = {_norm(name) for name in config["default_agents"]}
    if key in fixed:
        display = fixed[key]
    elif key in sync:
        display = state["insyncerator_window"]
    elif key in default_agents:
        display = state["default_window"]
    else:
        return None
    slot = config["windows"][display]
    return {
        "agent": key,
        "display": display,
        "cdp_port": ports[display],
        "slot": slot.get("slot"),
        "title": "",
        "surface": "grok",
        "epoch": state["epoch"],
        "as_of": state["as_of"],
        "source": state["source"],
        "scope": state["scope"],
    }


def write_routing(
    *,
    default_window: str,
    insyncerator_window: str,
    source: str,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Validate and atomically replace the state file. Epoch increments."""
    config = load_config()
    current_epoch = 0
    path = state_path()
    if path.is_file():
        try:
            current_epoch = int(validate_state(json.loads(path.read_text(encoding="utf-8")), config=config)["epoch"])
        except (ValueError, json.JSONDecodeError, OSError):
            current_epoch = 0
    now = datetime.now(UTC).isoformat()
    state = validate_state(
        {
            "schema_version": _SCHEMA,
            "epoch": current_epoch + 1,
            "as_of": now,
            "source": source,
            "scope": _SCOPE,
            "updated_at": now,
            "updated_by": updated_by or os.environ.get("USER") or "agent",
            "default_window": default_window,
            "insyncerator_window": insyncerator_window,
        },
        config=config,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return state


def _agent_bus_db_path() -> Path:
    return _gateway_home() / ".agent-bus" / "messages.db"


def sync_endpoint_subscriptions(*, updated_by: str | None = None) -> dict[str, Any]:
    """Append subscription rows so DB display matches live grok window routing."""
    from agent_bus_store.db.endpoint_subscriptions import (
        list_subscriptions,
        subscribe_endpoint,
    )
    from agent_seat.registry import STAGE_MANAGER_ADDRESSES

    state = load_routing()
    config = load_config()
    default_home = state["default_window"]
    insync_home = state["insyncerator_window"]
    sync_agents = {_norm(name) for name in config["sync_agents"]}
    stage_agents = {_norm(a) for a in STAGE_MANAGER_ADDRESSES} | {"grok-bot"}
    evidence = f"grok-window-routing epoch={state['epoch']}"
    bound_by = updated_by or os.environ.get("USER") or "promote"

    db_path = _agent_bus_db_path()
    prev_db = os.environ.get("AGENT_BUS_DB_PATH")
    os.environ["AGENT_BUS_DB_PATH"] = str(db_path)
    updated: list[str] = []
    try:
        for row in list_subscriptions():
            if row.get("state") != "subscribed":
                continue
            agent = str(row.get("agent") or "")
            key = _norm(agent)
            if key in stage_agents:
                display = default_home
                title = "Stage Manager (gc1)"
            elif key in sync_agents:
                display = insync_home
                title = row.get("title") or "insyncerator"
            else:
                continue
            result = subscribe_endpoint(
                agent=agent,
                thread_scope=str(row["thread_scope"]),
                surface=str(row["surface"]),
                display=display,
                title=title,
                agent_id=row.get("agent_id"),
                bound_by=bound_by,
                evidence=evidence,
            )
            if result.get("appended"):
                updated.append(f"{agent}:{row['thread_scope']}")
    finally:
        if prev_db is None:
            os.environ.pop("AGENT_BUS_DB_PATH", None)
        else:
            os.environ["AGENT_BUS_DB_PATH"] = prev_db
    return {"updated": updated, "default_window": default_home, "insyncerator_window": insync_home}


def promote(display: str, *, updated_by: str | None = None) -> dict[str, Any]:
    """Make ``display`` the grok-bot home and park insyncerator on the other."""
    config = load_config()
    home = _require_window(config, display)
    ids = [key for key in _window_ids(config) if key != home]
    if len(ids) != 1:
        raise ValueError("promote expects exactly two windows")
    state = write_routing(
        default_window=home,
        insyncerator_window=ids[0],
        source="promote",
        updated_by=updated_by,
    )
    sync_endpoint_subscriptions(updated_by=updated_by)
    return state


def status() -> dict[str, Any]:
    """Config slots plus the live routing state."""
    config = load_config()
    state = load_routing()
    return {
        "config_path": str(config_path()),
        "state_path": str(state_path()),
        "windows": config["windows"],
        "fixed_roles": config["fixed_roles"],
        "state": state,
        "routes": {
            "grok-bot": binding_for("grok-bot"),
            "impresario": binding_for("impresario"),
            "insyncerator": binding_for("insyncerator"),
            "grok-bot-gc1": binding_for("grok-bot-gc1"),
            "grok-bot-gc2": binding_for("grok-bot-gc2"),
            "grok-coordinator-1": binding_for("grok-coordinator-1"),
            "stage-manager": binding_for("stage-manager"),
            "syncerator": binding_for("syncerator"),
        },
    }
