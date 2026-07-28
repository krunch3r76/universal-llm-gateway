"""Durable tick-hold control — pause between ``_tick_once`` passes across restart.

Host-layer control plane (not a RootStatus / Transition / EnvSnapshot fact).
File under ``charter_runner_data_dir()`` so manage.sock pause survives quit/start
without depending on RootLedger sole-writer rules.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from libs.charter_runner_store.db import charter_runner_data_dir

HOLD_FILENAME = "tick-hold.json"
HOLD_SCHEMA_VERSION = 1
UNPARSEABLE_REASON = "unparseable"


@dataclass(frozen=True)
class Hold:
    """Parsed hold payload (or fail-closed stand-in for corrupt files)."""

    reason: str
    set_by: str
    set_at: float
    schema_version: int = HOLD_SCHEMA_VERSION


def hold_path(*, data_dir: Path | None = None) -> Path:
    """Return the durable hold file path."""
    base = data_dir if data_dir is not None else charter_runner_data_dir()
    return base / HOLD_FILENAME


def read_hold(*, data_dir: Path | None = None) -> Hold | None:
    """Return active hold, or None when the file is absent.

    Corrupt / truncated JSON is treated as held (fail-closed) with
    ``reason=unparseable`` so a bad write cannot silently resume admits.
    """
    path = hold_path(data_dir=data_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return Hold(
            reason=UNPARSEABLE_REASON,
            set_by="hold.read",
            set_at=path.stat().st_mtime if path.exists() else time.time(),
        )
    if not isinstance(raw, dict):
        return Hold(
            reason=UNPARSEABLE_REASON,
            set_by="hold.read",
            set_at=time.time(),
        )
    reason = str(raw.get("reason") or "").strip() or UNPARSEABLE_REASON
    set_by = str(raw.get("set_by") or "").strip() or "unknown"
    try:
        set_at = float(raw.get("set_at"))
    except (TypeError, ValueError):
        set_at = time.time()
    try:
        schema_version = int(raw.get("schema_version", HOLD_SCHEMA_VERSION))
    except (TypeError, ValueError):
        schema_version = HOLD_SCHEMA_VERSION
    return Hold(
        reason=reason,
        set_by=set_by,
        set_at=set_at,
        schema_version=schema_version,
    )


def set_hold(
    reason: str,
    set_by: str,
    *,
    data_dir: Path | None = None,
) -> Hold:
    """Write (or overwrite) the durable hold file and return the payload."""
    path = hold_path(data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = Hold(
        reason=str(reason or "").strip() or "unspecified",
        set_by=str(set_by or "").strip() or "unknown",
        set_at=time.time(),
        schema_version=HOLD_SCHEMA_VERSION,
    )
    body = {
        "schema_version": payload.schema_version,
        "reason": payload.reason,
        "set_by": payload.set_by,
        "set_at": payload.set_at,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return payload


def clear_hold(*, data_dir: Path | None = None) -> bool:
    """Remove the hold file. Returns True if a file was present."""
    path = hold_path(data_dir=data_dir)
    if not path.is_file():
        return False
    path.unlink()
    return True


def hold_as_dict(held: Hold | None) -> dict | None:
    """Serialize hold for manage.sock / busy_status payloads."""
    if held is None:
        return None
    return {
        "reason": held.reason,
        "set_by": held.set_by,
        "set_at": held.set_at,
        "schema_version": held.schema_version,
    }


HELD_HEARTBEAT_INTERVAL_S = 300.0


async def emit_held_if_due(
    held: Hold,
    *,
    last_emitted_at: float,
    force: bool = False,
    interval_s: float = HELD_HEARTBEAT_INTERVAL_S,
) -> float:
    """Emit ``manage.charter.tick.held`` when due; return updated last-emit ts."""
    from scripts.model_manager import observation_event as events

    now = time.time()
    if not force and (now - last_emitted_at) < interval_s:
        return last_emitted_at
    await events.emit_manage_charter_tick_held(
        reason=held.reason,
        set_by=held.set_by,
        set_at=held.set_at,
    )
    return now

