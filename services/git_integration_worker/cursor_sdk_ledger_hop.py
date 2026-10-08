"""Hop authority keys on conductor ledger ``record_json`` (todo:conductor-hop-reactor R2).

Ledger row is authority; CHECKPOINT/journal are projections. R3 reactor merges
``hop_successor``, ``hop_admit_error``, and closeout ``hop_declared`` via
``merge_hop_patch`` or ``CursorDispatchLedger.merge_record_json``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from services.git_integration_worker.cursor_sdk_hop_events import _HOP_REASONS

HOP_REASONS = _HOP_REASONS

_HOP_SEQ_KEY = "hop_seq"
_HOP_FROM_KEY = "hop_from"
_HOP_REASON_KEY = "hop_reason"
_HOP_DECLARED_KEY = "hop_declared"
_HOP_SUCCESSOR_KEY = "hop_successor"
_HOP_ADMIT_ERROR_KEY = "hop_admit_error"

_HOP_KEYS = frozenset(
    {
        _HOP_SEQ_KEY,
        _HOP_FROM_KEY,
        _HOP_REASON_KEY,
        _HOP_DECLARED_KEY,
        _HOP_SUCCESSOR_KEY,
        _HOP_ADMIT_ERROR_KEY,
    }
)


def validate_hop_reason(reason: str) -> bool:
    """Return True when ``reason`` is a known hop reason."""
    return reason in HOP_REASONS


def _validate_hop_seq(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"hop_seq must be int >= 0, got {value!r}")
    if value < 0:
        raise ValueError(f"hop_seq must be >= 0, got {value}")
    return value


def _validate_hop_reason_value(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"hop_reason must be non-empty str, got {value!r}")
    if value not in HOP_REASONS:
        raise ValueError(
            f"hop_reason must be one of {sorted(HOP_REASONS)!r}, got {value!r}"
        )
    return value


def _validate_hop_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty str, got {value!r}")
    return value


def _validate_hop_declared(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"hop_declared must be bool, got {value!r}")
    return value


def _validate_hop_admit_error(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {
            "retryable": False,
            "attempts": 1,
            "last_status_code": None,
            "last_error": value,
        }
    if isinstance(value, dict):
        return _normalize_hop_admit_error(value)
    raise ValueError(f"hop_admit_error must be dict or str, got {value!r}")


def hop_admit_error_is_stop_not_claimed(admit_err: dict[str, Any]) -> bool:
    """Structured ``reason`` first; legacy rows fall back to ``last_error`` substring."""
    reason = admit_err.get("reason")
    if reason == "stop_not_claimed":
        return True
    if isinstance(reason, str) and reason:
        return False
    last_error = str(admit_err.get("last_error") or "")
    return "stop_not_claimed" in last_error


def park_harvest_stop_not_claimed_exhausted(admit_err: dict[str, Any]) -> bool:
    """Cap applies to dedicated counter; legacy rows without it use ``attempts`` + substring."""
    sn = admit_err.get("stop_not_claimed_attempts")
    if isinstance(sn, int):
        return sn >= 2
    if int(admit_err.get("attempts") or 0) < 2:
        return False
    return hop_admit_error_is_stop_not_claimed(admit_err)


def _normalize_hop_admit_error(value: dict[str, Any]) -> dict[str, Any]:
    status = value.get("last_status_code")
    if status is None:
        status = value.get("status_code")
    retryable = value.get("retryable")
    if retryable is None and status is not None:
        retryable = int(status) >= 500 or int(status) == 429
    if retryable is None:
        # Status-less transport (httpx, stargate_unreachable) is transient — §E.2.
        retryable = status is None
    attempts = value.get("attempts")
    if not isinstance(attempts, int):
        attempts = 1
    last_error = value.get("last_error") or value.get("error") or ""
    out: dict[str, Any] = {
        "retryable": bool(retryable),
        "attempts": attempts,
        "last_status_code": status,
        "last_error": str(last_error),
    }
    reason = value.get("reason")
    if isinstance(reason, str) and reason:
        out["reason"] = reason
    sn = value.get("stop_not_claimed_attempts")
    if isinstance(sn, int):
        out["stop_not_claimed_attempts"] = sn
    return out


def merge_hop_admit_error(
    existing: dict[str, Any] | str | None,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge admit-error counters — never overwrite prior attempts on sweep."""
    base: dict[str, Any] = {}
    if isinstance(existing, dict):
        base = _normalize_hop_admit_error(existing)
    elif isinstance(existing, str):
        base = _normalize_hop_admit_error({"last_error": existing})
    merged_patch = dict(patch)
    if "error" in merged_patch and "last_error" not in merged_patch:
        merged_patch["last_error"] = merged_patch.pop("error")
    if "status_code" in merged_patch and "last_status_code" not in merged_patch:
        merged_patch["last_status_code"] = merged_patch.pop("status_code")
    attempt = int(base.get("attempts") or 0) + 1
    stop_sn = int(base.get("stop_not_claimed_attempts") or 0)
    failure_reason = merged_patch.get("reason")
    if failure_reason == "stop_not_claimed":
        stop_sn += 1
    merged_body: dict[str, Any] = {**base, **merged_patch, "attempts": attempt}
    if stop_sn > 0 or failure_reason == "stop_not_claimed":
        merged_body["stop_not_claimed_attempts"] = stop_sn
    normalized = _normalize_hop_admit_error(merged_body)
    return normalized


def merge_hop_admit_error_into_record_json_conn(
    conn: Any,
    *,
    dispatch_id: str,
    patch: dict[str, Any],
    finalize: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge ``hop_admit_error`` from a fresh row read — safe under concurrent sweepers."""
    row = conn.execute(
        "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
        (dispatch_id,),
    ).fetchone()
    if row is None:
        merged_err = merge_hop_admit_error(None, patch)
        if finalize is not None:
            merged_err = finalize(merged_err)
        return merged_err
    try:
        data = json.loads(row["record_json"] or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    prior = data.get(_HOP_ADMIT_ERROR_KEY)
    merged_err = merge_hop_admit_error(prior, patch)
    if finalize is not None:
        merged_err = finalize(merged_err)
    data[_HOP_ADMIT_ERROR_KEY] = merged_err
    conn.execute(
        "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
        (json.dumps(data, sort_keys=True, separators=(",", ":")), dispatch_id),
    )
    return merged_err


def hop_fields_from_record_json(record_json: str | None) -> dict[str, Any]:
    """Read all six hop keys when present on ``record_json``."""
    if not record_json:
        return {}
    try:
        data = json.loads(record_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    if _HOP_SEQ_KEY in data:
        out[_HOP_SEQ_KEY] = data[_HOP_SEQ_KEY]
    if _HOP_FROM_KEY in data:
        out[_HOP_FROM_KEY] = data[_HOP_FROM_KEY]
    if _HOP_REASON_KEY in data:
        out[_HOP_REASON_KEY] = data[_HOP_REASON_KEY]
    if _HOP_DECLARED_KEY in data:
        out[_HOP_DECLARED_KEY] = data[_HOP_DECLARED_KEY]
    if _HOP_SUCCESSOR_KEY in data:
        out[_HOP_SUCCESSOR_KEY] = data[_HOP_SUCCESSOR_KEY]
    if _HOP_ADMIT_ERROR_KEY in data:
        out[_HOP_ADMIT_ERROR_KEY] = data[_HOP_ADMIT_ERROR_KEY]
    return out


def stamp_hop_on_record_json(
    record_json: str,
    *,
    hop_seq: int,
    hop_from: str,
    hop_reason: str,
    hop_declared: bool | None = None,
    hop_successor: str | None = None,
    hop_admit_error: dict[str, Any] | str | None = None,
) -> str:
    """Stamp admit-time hop authority keys onto ``record_json``."""
    data = json.loads(record_json) if record_json else {}
    if not isinstance(data, dict):
        data = {}
    data[_HOP_SEQ_KEY] = _validate_hop_seq(hop_seq)
    data[_HOP_FROM_KEY] = _validate_hop_id(hop_from, field="hop_from")
    data[_HOP_REASON_KEY] = _validate_hop_reason_value(hop_reason)
    if hop_declared is not None:
        data[_HOP_DECLARED_KEY] = _validate_hop_declared(hop_declared)
    if hop_successor is not None:
        data[_HOP_SUCCESSOR_KEY] = _validate_hop_id(
            hop_successor, field="hop_successor"
        )
    if hop_admit_error is not None:
        data[_HOP_ADMIT_ERROR_KEY] = _validate_hop_admit_error(hop_admit_error)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def merge_hop_patch(record_json: str, patch: dict[str, Any]) -> str:
    """Typed shallow merge for hop keys only; validates types/reason on merge."""
    data = json.loads(record_json) if record_json else {}
    if not isinstance(data, dict):
        data = {}
    unknown = set(patch) - _HOP_KEYS
    if unknown:
        raise ValueError(
            f"merge_hop_patch accepts hop keys only, got {sorted(unknown)!r}"
        )
    for key, value in patch.items():
        if key == _HOP_SEQ_KEY:
            data[key] = _validate_hop_seq(value)
        elif key == _HOP_FROM_KEY:
            data[key] = _validate_hop_id(value, field="hop_from")
        elif key == _HOP_REASON_KEY:
            data[key] = _validate_hop_reason_value(value)
        elif key == _HOP_DECLARED_KEY:
            data[key] = _validate_hop_declared(value)
        elif key == _HOP_SUCCESSOR_KEY:
            data[key] = _validate_hop_id(value, field="hop_successor")
        elif key == _HOP_ADMIT_ERROR_KEY:
            prior = data.get(_HOP_ADMIT_ERROR_KEY)
            if isinstance(value, dict) and prior is not None:
                data[key] = merge_hop_admit_error(prior, value)
            else:
                data[key] = _validate_hop_admit_error(value)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


__all__ = [
    "HOP_REASONS",
    "hop_admit_error_is_stop_not_claimed",
    "hop_fields_from_record_json",
    "merge_hop_admit_error",
    "merge_hop_admit_error_into_record_json_conn",
    "merge_hop_patch",
    "park_harvest_stop_not_claimed_exhausted",
    "stamp_hop_on_record_json",
    "validate_hop_reason",
]
