"""Publish memoized fleet-idle gate observations to a life-readable cortex path."""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root

from .config import fire_interval_s
from .fleet_idle import FleetIdleSnapshot

logger = logging.getLogger(__name__)

SNAPSHOT_URI = "cortex://notes/system/operational/fleet-idle-gate-observation.json"
_SNAPSHOT_REL = "notes/system/operational/fleet-idle-gate-observation.json"
_SCHEMA = "fleet-idle-gate-observation/v1"


def _json_default(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def staleness_rule_text() -> str:
    """Human-readable staleness contract for life-surface readers."""
    interval = fire_interval_s()
    return (
        "This file records one gate observation per trigger pass when fleet_idle "
        "predicate evaluation runs — not live tracked state. "
        f"pass_at_utc older than ~{interval * 2:.0f}s (~2× fire interval) while a "
        "fleet_idle row is known-due ⇒ UNDETERMINED-for-observation. "
        "An older stamp outside an active evaluation window means no row was under "
        "evaluation at last publish — legitimate staleness, not probe failure. "
        "Never refresh by lease-taking probe or agent_bus.request."
    )


def build_observation_payload(
    snapshot: FleetIdleSnapshot,
    *,
    trigger_row_id: str,
    defer_count: int,
    grace_s: int,
    pass_at: datetime,
) -> dict[str, Any]:
    """Serialize the memoized pass snapshot plus trigger-row context."""
    when = pass_at if pass_at.tzinfo is not None else pass_at.replace(tzinfo=UTC)
    stamp = when.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema": _SCHEMA,
        "snapshot_uri": SNAPSHOT_URI,
        "staleness_rule": staleness_rule_text(),
        "pass_at_utc": stamp,
        "trigger_row_id": trigger_row_id,
        "defer_count": defer_count,
        "grace_s": grace_s,
        "fleet_verdict": snapshot.verdict.value,
        "dispatch_idle": snapshot.dispatch_idle,
        "tick_empty": snapshot.tick_empty,
        "cursor_auto_idle": snapshot.cursor_auto_idle,
        "cdp_lane_idle": snapshot.cdp_lane_idle,
        "dispatch_undetermined": snapshot.dispatch_undetermined,
        "tick_undetermined": snapshot.tick_undetermined,
        "cdp_undetermined": snapshot.cdp_undetermined,
        "tick_empty_strict": snapshot.tick_empty_strict,
    }


def snapshot_dest_path() -> Path:
    return cortex_files_root() / _SNAPSHOT_REL


def _atomic_write_text(dest: Path, content: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest.with_suffix(
        dest.suffix + f".tmp-{os.getpid()}-{secrets.token_hex(4)}"
    )
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, dest)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def publish_pass_snapshot(
    snapshot: FleetIdleSnapshot,
    *,
    trigger_row_id: str,
    defer_count: int,
    grace_s: int,
    pass_at: datetime | None = None,
) -> None:
    """Best-effort publish — must never wedge the gate (AC2)."""
    try:
        payload = build_observation_payload(
            snapshot,
            trigger_row_id=trigger_row_id,
            defer_count=defer_count,
            grace_s=grace_s,
            pass_at=pass_at or datetime.now(UTC),
        )
        body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        _atomic_write_text(snapshot_dest_path(), body)
    except Exception:
        logger.warning(
            "fleet idle pass snapshot publish failed trigger_row_id=%s",
            trigger_row_id,
            exc_info=True,
        )


def grace_s_from_predicate_args(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return 0
    if not isinstance(args, dict):
        return 0
    grace = args.get("grace_s", 0)
    if not isinstance(grace, int) or isinstance(grace, bool) or grace < 0:
        return 0
    return grace
