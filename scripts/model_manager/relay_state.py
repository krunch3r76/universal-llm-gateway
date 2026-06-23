"""Desired-state persistence for relay edge lifecycle (manage-aware recovery)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

DesiredState = Literal["managed_up", "managed_down", "unmanaged"]

_GATEWAY_DIR = Path.home() / ".gateway"
_NODES_DIR = _GATEWAY_DIR / "nodes"
_DEFAULT_STATE: DesiredState = "managed_up"


def _state_path(node_id: str) -> Path:
    return _NODES_DIR / f"{node_id}.state.json"


def read_desired_state(node_id: str) -> DesiredState:
    """Return desired_state; missing or invalid files default to managed_up."""
    path = _state_path(node_id)
    if not path.is_file():
        return _DEFAULT_STATE
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return _DEFAULT_STATE
    desired = data.get("desired_state")
    if desired in ("managed_up", "managed_down", "unmanaged"):
        return desired
    return _DEFAULT_STATE


def write_desired_state(
    node_id: str,
    desired_state: DesiredState,
    reason: str,
    updated_by: str,
) -> None:
    """Atomically persist desired state for a relay node."""
    _NODES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "node_id": node_id,
        "desired_state": desired_state,
        "reason": reason,
        "updated_at": datetime.now(UTC).isoformat(),
        "updated_by": updated_by,
    }
    path = _state_path(node_id)
    fd, tmp_name = tempfile.mkstemp(dir=_NODES_DIR, suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
