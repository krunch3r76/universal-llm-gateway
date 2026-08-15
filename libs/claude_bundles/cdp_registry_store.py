"""On-disk CDP registry persistence — active.json, jsonl log, ports.lock."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REGISTRY_DIR = Path.home() / ".gateway" / "cdp-registry"
# Same directory name as cursor_home._default_dispatch_home_root. Libs must
# not import GIW; keep this fingerprint aligned if that root is renamed.
DISPATCH_HOME_MARKER = "cursor-dispatch-homes"
REGISTRY_LOG = REGISTRY_DIR / "registry.jsonl"
ACTIVE_JSON = REGISTRY_DIR / "active.json"
SESSIONS_JSON = REGISTRY_DIR / "sessions.json"
SESSION_TRANSITIONS_JSONL = REGISTRY_DIR / "session_transitions.jsonl"
PORTS_LOCK = REGISTRY_DIR / "ports.lock"
REGISTRATIONS_DIR = REGISTRY_DIR / "registrations"


class RegistryStoreError(RuntimeError):
    """Corrupt or invalid registry on-disk state."""


@dataclass(frozen=True)
class RegistryRead:
    """Scoped registry read — empty ``data`` is a scoped-null, not a global empty."""

    data: dict[str, dict[str, Any]]
    observed_home_kind: str
    observed_home: Path
    source_path: Path
    present: bool

    def miss_label(self) -> str:
        return (
            f"observed_home_kind={self.observed_home_kind} path={self.source_path}"
        )


def classify_observed_home_kind(home: Path | str) -> str:
    """Return ``dispatch`` or ``operator`` for the home a registry path sits under."""
    try:
        parts = Path(home).expanduser().resolve().parts
    except OSError:
        parts = Path(home).parts
    return "dispatch" if DISPATCH_HOME_MARKER in parts else "operator"


def _registry_home() -> Path:
    """Home implied by current ``REGISTRY_DIR`` (``{home}/.gateway/cdp-registry``)."""
    return REGISTRY_DIR.parent.parent


def _load_json_object(path: Path, *, label: str) -> tuple[dict[str, dict[str, Any]], bool]:
    if not path.exists():
        return {}, False
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise RegistryStoreError(f"corrupt {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryStoreError(f"{label} must be a JSON object")
    return data, True


def _scoped_read(path: Path, *, label: str) -> RegistryRead:
    home = _registry_home()
    data, present = _load_json_object(path, label=label)
    return RegistryRead(
        data=data,
        observed_home_kind=classify_observed_home_kind(home),
        observed_home=home,
        source_path=path,
        present=present,
    )


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


def load_active_read() -> RegistryRead:
    """Load ``active.json`` and name which home was observed."""
    return _scoped_read(ACTIVE_JSON, label="active.json")


def load_active() -> dict[str, dict[str, Any]]:
    """Return the active map. Empty dict is a scoped-null — use ``load_active_read``."""
    return load_active_read().data


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


def load_sessions_read() -> RegistryRead:
    """Load ``sessions.json`` and name which home was observed."""
    return _scoped_read(SESSIONS_JSON, label="sessions.json")


def load_sessions() -> dict[str, dict[str, Any]]:
    """Load obligation projection — empty dict is a scoped-null, not a global empty."""
    return load_sessions_read().data


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
