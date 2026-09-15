"""On-disk CDP registry persistence — active.json, jsonl log, ports.lock."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pwd
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_protocol.errors import ProtocolError

from claude_bundles.cdp_registry.models import seat_open

# Same directory name as cursor_home._default_dispatch_home_root. Libs must
# not import GIW; keep this fingerprint aligned if that root is renamed.
DISPATCH_HOME_MARKER = "cursor-dispatch-homes"


def _operator_home() -> Path:
    """Real operator home — not cursor-sdk per-dispatch HOME swap."""
    if op_home := os.environ.get("CHARTER_RUNNER_OPERATOR_HOME"):
        return Path(op_home).expanduser()
    current = Path.home()
    if DISPATCH_HOME_MARKER in current.as_posix():
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    return current


def _resolve_registry_dir() -> Path:
    """Pinned registry root — ``CDP_REGISTRY_HOME`` override, else operator home."""
    if override := os.environ.get("CDP_REGISTRY_HOME", "").strip():
        return Path(override).expanduser()
    return _operator_home() / ".gateway" / "cdp-registry"


REGISTRY_DIR = _resolve_registry_dir()
REGISTRY_LOG = REGISTRY_DIR / "registry.jsonl"
ACTIVE_JSON = REGISTRY_DIR / "active.json"
SESSIONS_JSON = REGISTRY_DIR / "sessions.json"
SESSION_TRANSITIONS_JSONL = REGISTRY_DIR / "session_transitions.jsonl"
PORTS_LOCK = REGISTRY_DIR / "ports.lock"
REGISTRATIONS_DIR = REGISTRY_DIR / "registrations"


class RegistryStoreError(RuntimeError):
    """Corrupt or invalid registry on-disk state."""


def is_seat_authority() -> bool:
    """True when this process may mutate the fleet seat key."""
    raw = os.environ.get("CDP_REGISTRY_SEAT_AUTHORITY", "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return classify_observed_home_kind(Path.home()) == "operator"


def require_seat_authority(*, operation: str) -> None:
    """Refuse seat mutation from a non-authority process."""
    if is_seat_authority():
        return
    raise ProtocolError(
        code="seat.authority_refused",
        message=(
            f"seat mutation {operation!r} refused: this process is not the "
            "cdp_ask seat authority"
        ),
        source="rpc",
        retryable=False,
        data={
            "operation": operation,
            "observed_home_kind": classify_observed_home_kind(Path.home()),
            "recovery": os.environ.get("PROJECT_ASK_URL", "").strip() or None,
        },
    )


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
    """Create the registry home and per-registration lock directory if missing."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRATIONS_DIR.mkdir(parents=True, exist_ok=True)


def open_lock(path: Path) -> int:
    """Open (or create) a lock file and return its file descriptor."""
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
    """Atomically replace ``active.json``, keeping any omitted ``seat_open`` rows.

    I6: a dropped key whose prior row is still seat-open is restored from disk.
    Callers that intend to drop a seat must persist ``seat_closed_at`` first.
    """
    ensure_dirs()
    prior, _present = _load_json_object(ACTIVE_JSON, label="active.json")
    merged = dict(active)
    for rid, row in prior.items():
        if rid not in merged and isinstance(row, dict) and seat_open(row):
            merged[rid] = row
    tmp = ACTIVE_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, ACTIVE_JSON)


def append_log(event: str, record: dict[str, Any]) -> None:
    """Append one fsync'd registry log line named by *event*."""
    ensure_dirs()
    line = json.dumps({"event": event, "ts": time.time(), **record}, sort_keys=True)
    with REGISTRY_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_registry_log() -> list[dict[str, Any]]:
    """Read all registry.jsonl records in append order."""
    if not REGISTRY_LOG.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in REGISTRY_LOG.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_seat_transition_journal(
    *,
    registration_id: str,
    seat_lane: str | None,
    seat_bound_at: float | None,
    superseded: list[str],
) -> None:
    """Append one durable seat-axis journal line under authority."""
    require_seat_authority(operation="append_seat_transition_journal")
    append_log(
        "seat_lane_bound",
        {
            "registration_id": registration_id,
            "seat_lane": seat_lane,
            "seat_bound_at": seat_bound_at,
            "superseded": list(superseded),
            "event_id": uuid.uuid4().hex,
        },
    )


def _apply_seat_lane_bound(active: dict[str, dict[str, Any]], record: dict[str, Any]) -> None:
    """Apply one ``seat_lane_bound`` journal line to *active* (in-memory replay)."""
    reg_id = str(record.get("registration_id") or "").strip()
    lane = str(record.get("seat_lane") or "").strip()
    if not reg_id or not lane:
        return
    ts = record.get("seat_bound_at")
    closed_at = record.get("ts")
    for sid in record.get("superseded") or []:
        other_id = str(sid or "").strip()
        if not other_id or other_id not in active:
            continue
        closed = dict(active[other_id])
        closed["seat_closed_at"] = closed_at if closed_at is not None else time.time()
        closed["seat_close_reason"] = "superseded"
        closed["superseded_by"] = reg_id
        active[other_id] = closed
    row = dict(active.get(reg_id) or {})
    row["registration_id"] = reg_id
    row["seat_lane"] = lane
    row["seat_bound_at"] = ts
    row["seat_closed_at"] = None
    active[reg_id] = row


def fold_seat_journal(active: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Replay seat_lane_bound lines from registry.jsonl into *active* or a fresh map."""
    state = dict(active) if active is not None else {}
    for record in read_registry_log():
        if str(record.get("event") or "") == "seat_lane_bound":
            _apply_seat_lane_bound(state, record)
    return state


def open_seats_per_lane(active: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Return open seat registration ids grouped by lane."""
    by_lane: dict[str, list[str]] = {}
    for rid, row in active.items():
        if not isinstance(row, dict) or not seat_open(row):
            continue
        lane = str(row.get("seat_lane") or "").strip()
        if lane:
            by_lane.setdefault(lane, []).append(str(rid))
    return by_lane


def verify_seat_fold_invariant() -> None:
    """Raise when replay at any journal prefix yields >1 open seat on a lane."""
    prefix: dict[str, dict[str, Any]] = {}
    for record in read_registry_log():
        if str(record.get("event") or "") != "seat_lane_bound":
            continue
        _apply_seat_lane_bound(prefix, record)
        for lane, open_ids in open_seats_per_lane(prefix).items():
            if len(open_ids) > 1:
                raise RegistryStoreError(
                    f"seat fold invariant violated on lane {lane!r}: "
                    f"{len(open_ids)} open seats {open_ids}"
                )


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
    """Return the flock path that serializes drivers for *registration_id*."""
    return REGISTRATIONS_DIR / f"{registration_id}.lock"
