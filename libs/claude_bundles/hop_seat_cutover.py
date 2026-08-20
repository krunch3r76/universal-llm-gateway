"""Hop seat cutover — refuse-at-request for live incumbent and superseded predecessor.

Rank-1 bind (auto-34f6cae99b00): cadence must not re-commission while the watched
registration still streams; ``agent_bus.request`` refuses a superseded predecessor
once the successor execution is observed running (successor_confirm).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from universal_logging import get_logger
from universal_protocol.errors import ProtocolError

from claude_bundles.hop_cadence_id_map import claim_join_keys

logger = get_logger(__name__)

_WATCH_FILENAME = "hop_cadence_watches.json"
_REFUSE_REASON_CADENCE = "seat_live_refuse_at_request"
_REFUSE_REASON_REQUEST = "superseded_predecessor_refuse_at_request"


def watches_path() -> Path:
    """Hop watch ledger beside the CDP registry store.

    Override with env ``CURSOR_AUTO_HOP_WATCHES_PATH`` (non-empty) for tests.
    """
    raw = os.environ.get("CURSOR_AUTO_HOP_WATCHES_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".gateway" / "cdp-registry" / _WATCH_FILENAME


def load_watches(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load watch rows; empty dict on missing or corrupt ledger."""
    target = path or watches_path()
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("hop_seat_cutover watch load failed path=%s err=%s", target, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


_HOST_RUNNING_STATUSES = frozenset({"pending", "running"})


def _host_store_rows(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Execution-store rows only — never identity_rows / seated_rows / seat_rows."""
    rows = snap.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def running_registration_ids(snap: dict[str, Any]) -> set[str]:
    """Return registration ids for pending/running execution-store streams on this snap.

    Host-only: ignores seated_rows / identity_rows so a dormant or idle seated
    CSE cannot refuse a successor hop (F6). Capture still uses identity_rows.
    """
    out: set[str] = set()
    for row in _host_store_rows(snap):
        status = str(row.get("status") or "")
        reg_id = str(row.get("registration_id") or "").strip()
        if reg_id and status in _HOST_RUNNING_STATUSES:
            out.add(reg_id)
    return out


def running_execution_ids(snap: dict[str, Any]) -> set[str]:
    """Return execution ids for pending/running execution-store streams on this snap."""
    out: set[str] = set()
    for row in _host_store_rows(snap):
        status = str(row.get("status") or "")
        exec_id = str(row.get("execution_id") or "").strip()
        if exec_id and status in _HOST_RUNNING_STATUSES:
            out.add(exec_id)
    return out


def matched_active_work_row(
    row: dict[str, Any],
    snap: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """Return matched active-work row when claim join keys intersect the running set."""
    keys = claim_join_keys(row)
    if not keys:
        return None, None
    running = running_execution_ids(snap)
    intersection = keys & running
    if not intersection:
        return None, None
    rows = snap.get("rows") if isinstance(snap.get("rows"), list) else []
    for aw_row in rows:
        if not isinstance(aw_row, dict):
            continue
        status = str(aw_row.get("status") or "")
        exec_id = str(aw_row.get("execution_id") or "").strip()
        if exec_id in intersection and status in {"pending", "running"}:
            return exec_id, aw_row
    return next(iter(intersection)), None


def successor_confirm_active(row: dict[str, Any], snap: dict[str, Any]) -> bool:
    """True when claim join keys intersect live ``cdp_ask`` active-work execution ids."""
    matched_key, _ = matched_active_work_row(row, snap)
    return matched_key is not None


def joinable_pending_succession(
    row: dict[str, Any],
    *,
    now: float | None = None,
) -> bool:
    """True when a hop claim recorded joinable ``pending_succession`` state."""
    if row.get("succession_status") == "revoked":
        return False
    pending = row.get("pending_succession")
    if not isinstance(pending, dict):
        return False
    exec_id = str(pending.get("execution_id") or "").strip()
    if not exec_id:
        return False
    ts = time.time() if now is None else now
    claimed_at = pending.get("claimed_at")
    if claimed_at is None:
        return True
    try:
        age = ts - float(claimed_at)
    except (TypeError, ValueError):
        return True
    max_age = float(pending.get("join_max_age_s") or 600.0)
    return age <= max_age


def lease_fence_active(
    row: dict[str, Any],
    snap: dict[str, Any],
    *,
    now: float | None = None,
) -> bool:
    """True when successor confirm or a joinable pending claim arms the fence."""
    return successor_confirm_active(row, snap) or joinable_pending_succession(row, now=now)


def clear_lease_fence_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Clear request-fence identity after CSE-terminal reclaim."""
    updated = dict(row)
    updated.pop("superseded_registration_id", None)
    updated.pop("superseded_execution_id", None)
    updated.pop("predecessor_verdict", None)
    return updated


def refuse_cadence_hop_for_live_seat(
    row: dict[str, Any],
    snap: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    """Refuse a repeat cadence hop while the watched registration has a live host stream.

    Host-only: refuse keys off ``running_registration_ids`` (execution-store
    ``rows`` pending/running). Idle or dormant seated identity rows do not
    refuse. First hop (``last_hop_at`` unset) is never refused here.
    """
    reg_id = str(row.get("registration_id") or "").strip()
    if not reg_id:
        return False, None, {}
    if row.get("last_hop_at") is None:
        return False, None, {}
    if reg_id not in running_registration_ids(snap):
        return False, None, {}
    return (
        True,
        _REFUSE_REASON_CADENCE,
        {
            "registration_id": reg_id,
            "last_hop_at": row.get("last_hop_at"),
            "signal": "hop_cadence_identity_seated_registration_id",
        },
    )


def resolve_request_refusal(
    *,
    thread_id: str | None,
    cse_registration_id: str | None,
    snap: dict[str, Any],
    path: Path | None = None,
    identity_source: str | None = None,
) -> dict[str, Any] | None:
    """Return a ``seat.lease_lost`` envelope for a superseded predecessor, else None."""
    tid = (thread_id or "").strip()
    reg_id = (cse_registration_id or "").strip()
    if not tid or not reg_id:
        return None
    row = load_watches(path).get(tid)
    if not row:
        return None
    # Current lane holder always admits — including self-supersede poison rows
    # (registration_id == superseded_registration_id from Stargate/satellite miss).
    holder = str(row.get("registration_id") or "").strip()
    if holder and reg_id == holder:
        return None
    superseded = str(row.get("superseded_registration_id") or "").strip()
    if not superseded or reg_id != superseded:
        return None
    # Poisoned ledger: never refuse on self-supersede even if identity bind
    # somehow diverges from watch.registration_id.
    if holder and superseded == holder:
        return None
    if not lease_fence_active(row, snap):
        return None
    matched_key, _ = matched_active_work_row(row, snap)
    successor = str(row.get("successor_execution_id") or "").strip()
    stargate_keys = {
        str(row.get("successor_execution_id") or "").strip(),
        str(row.get("last_hop_execution_id") or "").strip(),
        str(row.get("pending_execution_id") or "").strip(),
    }
    pending = row.get("pending_succession")
    if isinstance(pending, dict):
        stargate_keys.add(str(pending.get("execution_id") or "").strip())
    stargate_keys.discard("")
    satellite_id = matched_key if matched_key and matched_key not in stargate_keys else None
    data: dict[str, Any] = {
        "thread_id": tid,
        "superseded_registration_id": superseded,
        "successor_execution_id": successor or None,
        "successor_satellite_execution_id": satellite_id,
        "signal": "cdp_ask_active_work_membership",
        "reason": _REFUSE_REASON_REQUEST,
    }
    if identity_source:
        data["identity_source"] = identity_source
    return ProtocolError(
        code="seat.lease_lost",
        message=(
            "request: lane write authority cut over to successor; "
            f"registration {reg_id!r} is superseded"
        ),
        source="rpc",
        retryable=False,
        data=data,
    ).to_dict()


def effective_seated_at_after_hop(
    row: dict[str, Any],
    *,
    registry_started_at: Callable[[str | None], float | None],
) -> float | None:
    """Age source for hop cadence — post-hop ``seated_at`` wins over stale registry."""
    if row.get("last_hop_at") is not None:
        seated = row.get("seated_at")
        if seated is not None:
            try:
                return float(seated)
            except (TypeError, ValueError):
                pass
    reg_id = str(row.get("registration_id") or "") or None
    reg_started = registry_started_at(reg_id)
    if reg_started is not None:
        return reg_started
    seated = row.get("seated_at")
    if seated is None:
        return None
    try:
        return float(seated)
    except (TypeError, ValueError):
        return None


__all__ = [
    "clear_lease_fence_fields",
    "effective_seated_at_after_hop",
    "joinable_pending_succession",
    "lease_fence_active",
    "load_watches",
    "matched_active_work_row",
    "refuse_cadence_hop_for_live_seat",
    "resolve_request_refusal",
    "running_execution_ids",
    "running_registration_ids",
    "successor_confirm_active",
    "watches_path",
]
