"""On-disk CDP registry persistence — active.json, jsonl log, ports.lock."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

REGISTRY_DIR = Path.home() / ".gateway" / "cdp-registry"
REGISTRY_LOG = REGISTRY_DIR / "registry.jsonl"
ACTIVE_JSON = REGISTRY_DIR / "active.json"
SESSIONS_JSON = REGISTRY_DIR / "sessions.json"
SESSION_TRANSITIONS_JSONL = REGISTRY_DIR / "session_transitions.jsonl"
PORTS_LOCK = REGISTRY_DIR / "ports.lock"
REGISTRATIONS_DIR = REGISTRY_DIR / "registrations"


class RegistryStoreError(RuntimeError):
    """Corrupt or invalid registry on-disk state."""


def ensure_dirs() -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRATIONS_DIR.mkdir(parents=True, exist_ok=True)


def open_lock(path: Path) -> int:
    ensure_dirs()
    return os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)


@contextlib.contextmanager
def ports_lock() -> Any:
    fd = open_lock(PORTS_LOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_active() -> dict[str, dict[str, Any]]:
    if not ACTIVE_JSON.exists():
        return {}
    try:
        data = json.loads(ACTIVE_JSON.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise RegistryStoreError(f"corrupt active.json: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryStoreError("active.json must be a JSON object")
    return data


def write_active(active: dict[str, dict[str, Any]]) -> None:
    ensure_dirs()
    tmp = ACTIVE_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(active, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, ACTIVE_JSON)


def append_log(event: str, record: dict[str, Any]) -> None:
    ensure_dirs()
    line = json.dumps({"event": event, "ts": time.time(), **record}, sort_keys=True)
    with REGISTRY_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_sessions() -> dict[str, dict[str, Any]]:
    """Load obligation projection — empty dict when absent."""
    if not SESSIONS_JSON.exists():
        return {}
    try:
        data = json.loads(SESSIONS_JSON.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise RegistryStoreError(f"corrupt sessions.json: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryStoreError("sessions.json must be a JSON object")
    return data


def write_sessions(sessions: dict[str, dict[str, Any]]) -> None:
    """Atomic replace for obligation projection."""
    ensure_dirs()
    tmp = SESSIONS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sessions, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, SESSIONS_JSON)


def append_session_transition(record: dict[str, Any]) -> None:
    """Append one fsync'd transition line — raises on I/O failure."""
    ensure_dirs()
    line = json.dumps(record, sort_keys=True)
    with SESSION_TRANSITIONS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_session_transitions() -> list[dict[str, Any]]:
    """Read all transition records from the durable log."""
    if not SESSION_TRANSITIONS_JSONL.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in SESSION_TRANSITIONS_JSONL.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def registration_lock_path(registration_id: str) -> Path:
    return REGISTRATIONS_DIR / f"{registration_id}.lock"
