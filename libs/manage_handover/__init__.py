"""Armed-record helpers for manage handover (successor proves before incumbent quits)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from deploy_identity.code_version import process_age_s, resolve_code_version
from transport_utils import MANAGE_SOCKET

MANAGE_HANDOVER_ENV = "MANAGE_HANDOVER_RECORD"
DEFAULT_ARMED_RECORD_PATH = Path("/tmp/universal-protocol/manage.armed.json")


def default_armed_record_path() -> Path:
    """Path beside manage.sock unless the runner passes an explicit record path."""
    return Path(MANAGE_SOCKET).parent / "manage.armed.json"


def record_path_from_env() -> Path | None:
    """Return the armed-record path when ``MANAGE_HANDOVER_RECORD`` is set."""
    raw = os.environ.get(MANAGE_HANDOVER_ENV)
    if not raw or not str(raw).strip():
        return None
    return Path(raw)


def _process_start_time_iso() -> str:
    age_s = process_age_s()
    if age_s is not None:
        return (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat()
    return datetime.now(UTC).isoformat()


def write_armed_record(
    path: Path,
    *,
    pid: int,
    process_start_time: str | None = None,
    code_version: str | None = None,
) -> None:
    """Persist the successor armed proof payload (atomic replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": pid,
        "process_start_time": process_start_time or _process_start_time_iso(),
        "code_version": code_version or resolve_code_version(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_armed_record(path: Path) -> dict | None:
    """Read an armed record, or ``None`` when absent or invalid."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def remove_armed_record(path: Path) -> None:
    """Remove the armed record; missing path is success."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def prove_armed_record(
    record: dict,
    *,
    target_ref: str,
    whoami_before: dict,
) -> tuple[bool, str]:
    """Validate pid alive, code_version, and start time later than incumbent."""
    pid = record.get("pid")
    if not isinstance(pid, int):
        return False, "successor_proof_failed: pid_missing"
    try:
        os.kill(pid, 0)
    except OSError:
        return False, "successor_proof_failed: pid_not_alive"

    code_version = record.get("code_version")
    if not isinstance(code_version, str) or code_version != target_ref:
        return False, (
            f"successor_proof_failed: code_version_mismatch "
            f"observed={code_version!r} target={target_ref!r}"
        )

    before_raw = whoami_before.get("process_start_time")
    after_raw = record.get("process_start_time")
    if not isinstance(before_raw, str) or not isinstance(after_raw, str):
        return False, "successor_proof_failed: process_start_time_missing"
    try:
        before_ts = datetime.fromisoformat(before_raw.replace("Z", "+00:00"))
        after_ts = datetime.fromisoformat(after_raw.replace("Z", "+00:00"))
    except ValueError:
        return False, "successor_proof_failed: process_start_time_unparseable"
    if after_ts <= before_ts:
        return False, (
            "successor_proof_failed: process_start_time_not_later "
            f"before={before_raw!r} record={after_raw!r}"
        )
    return True, "armed"


__all__ = [
    "DEFAULT_ARMED_RECORD_PATH",
    "MANAGE_HANDOVER_ENV",
    "default_armed_record_path",
    "prove_armed_record",
    "read_armed_record",
    "record_path_from_env",
    "remove_armed_record",
    "write_armed_record",
]
