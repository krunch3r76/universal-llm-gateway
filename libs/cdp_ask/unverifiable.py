"""Unverifiable vs CSE-death stall classification (a:30678 / a:28790).

Poller wall expiry whose Stop-click is unconfirmed stays in the unverifiable
set so reconcile horizon, not the poller, decides whether the CSE is dead.
"""

from __future__ import annotations

import ast
from typing import Any

from cdp_ask.models import StallStage, classify_stall_stage

WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED = "wall_clock_exceeded_abort_unconfirmed"

UNVERIFIABLE_STALL_STAGES = frozenset(
    {
        "observer_unverified",
        "horizon_unverifiable_retained",
        "reconcile_abandoned_unverifiable",
        "archive_write",
        "post_terminal_poll",
        WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED,
    }
)

DEATH_STALL_STAGES = frozenset(
    {
        "weekly_limit",
        "upstream_overloaded",
        "worker_cancelled",
        "mark_terminal",
        "completion_detection",
        "no_progress",
        "wall_clock_exceeded",
    }
)

_CSE_URL_MARKER = "claude.ai/cowork/cse_"

_UNSET = object()

_DEATH_ERROR_TOKENS = (
    "weekly limit",
    "hit a limit",
    "overloaded",
    "error_banner",
    "aborted",
    "cancelled",
    "worker_crash",
    "worker_cancelled",
)


def wall_abort_unconfirmed(
    abort_info: dict[str, Any] | None,
    *,
    sat_id: str | None,
) -> bool:
    """True when a wall-expiry Stop-click was not confirmed.

    Unconfirmed when *abort_info* carries ``error``, a non-2xx ``status_code``,
    or *sat_id* is set and *abort_info* is empty. A confirmed abort (no error,
    missing or 2xx status, non-empty body) stays on the ``wall_clock_exceeded``
    death path. Callers then stamp ``retain_cse`` and leave proof unset so
    reconcile horizon decides death.
    """
    info = abort_info or {}
    if info.get("error"):
        return True
    status = info.get("status_code")
    if status is not None:
        try:
            code = int(status)
        except (TypeError, ValueError):
            return True
        if code < 200 or code >= 300:
            return True
    return bool(sat_id) and not info


def is_unverifiable_stall(
    stall_stage: str | None,
    error: str | None = None,
    *,
    url: str | None = None,
    satellite_execution_id: Any = _UNSET,
) -> bool:
    """True when a failed snapshot is observer-unverifiable, not CSE death.

    Requires compose witness (``cse_`` in *url*) after death-stage/token gates.
    Explicit ``satellite_execution_id=None`` means pre-submit death (``cdp FAILED``).
    """
    stage = (stall_stage or "").strip()
    if stage == "select_no_attest" or "select_no_attest" in (error or "").lower():
        return False
    if stage in DEATH_STALL_STAGES:
        return False
    err = (error or "").lower()
    if any(token in err for token in _DEATH_ERROR_TOKENS):
        return False
    if satellite_execution_id is not _UNSET and satellite_execution_id is None:
        return False
    return _CSE_URL_MARKER in str(url or "")


def converse_fail_error(last_error: str | None) -> str:
    """Keep inner harness error; generic token is fallback only."""
    raw = (last_error or "").strip()
    return raw if raw else "conversation failed"


def converse_stall_stage(last_error: str | None, *, conv_ok: bool) -> StallStage | None:
    """Stall for a converse payload — generic unknown becomes observer_unverified."""
    if conv_ok:
        return None
    stall = classify_stall_stage(converse_fail_error(last_error))
    if stall == "unknown":
        return "observer_unverified"
    return stall


def transport_miss_fields(
    error: str | None,
    url: str | None,
    satellite_execution_id: Any = _UNSET,
) -> dict[str, Any]:
    """Project a poller transport-miss onto stall, retain, and envelope extras."""
    unverifiable = is_unverifiable_stall(
        None,
        error,
        url=url,
        satellite_execution_id=satellite_execution_id,
    )
    extras: dict[str, Any] = {}
    raw = str(url or "").strip()
    if raw and _CSE_URL_MARKER in raw:
        extras["chat_url"] = raw
    return {
        "stall_stage": "observer_unverified",
        "error": str(error or ""),
        "unverifiable": unverifiable,
        "retain_reason": "observer_unverified" if unverifiable else None,
        "extras": extras,
    }


def select_record_from_error(error: str | None) -> dict[str, Any] | None:
    """Parse the ``model select failed:`` dict when it is a select record."""
    raw = error or ""
    marker = "model select failed:"
    idx = raw.lower().find(marker)
    if idx < 0:
        return None
    payload = raw[idx + len(marker) :].strip()
    if not payload.startswith("{"):
        return None
    try:
        record = ast.literal_eval(payload)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(record, dict) or "step" not in record:
        return None
    return record


def model_select_status_lines(error: str | None) -> list[str]:
    """Render a select miss for the CDP failure body.

    ``observer_unverified`` is the empty-CSE class. A select record keeps
    ``step``, the chip pair, the menu label, and the request as their own lines.
    """
    record = select_record_from_error(error)
    if not record or record.get("step") != "select_no_attest":
        return []
    keys = (
        "value",
        "step",
        "before",
        "after",
        "matched",
        "requested",
        "path",
        "as_of",
        "source",
        "scope",
        "epoch",
        "menu_label_glued",
        "matched_is_chip",
        "effort_only_chip_change",
    )
    lines = ["", "model_select:"]
    for key in keys:
        if key not in record:
            continue
        lines.append(f"- {key}: `{record[key]}`")
    return lines


def failed_snapshot_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project a failed satellite snapshot onto stall, error, and extras."""
    stall = snapshot.get("stall_stage")
    error = str(snapshot.get("error") or snapshot.get("status") or "")
    url = snapshot.get("url")
    sat_kw: dict[str, Any] = {}
    if "satellite_execution_id" in snapshot:
        sat_kw["satellite_execution_id"] = snapshot.get("satellite_execution_id")
    select_miss = (
        stall or ""
    ) == "select_no_attest" or "select_no_attest" in error.lower()
    if select_miss:
        stall = "select_no_attest"
        unverifiable = False
    else:
        unverifiable = is_unverifiable_stall(stall, error, url=url, **sat_kw)
        if unverifiable and (not stall or stall == "unknown"):
            stall = "observer_unverified"
    extras: dict[str, Any] = {}
    raw = str(url or "").strip()
    if raw and _CSE_URL_MARKER in raw:
        extras["chat_url"] = raw
    return {
        "stall_stage": stall,
        "error": error,
        "unverifiable": unverifiable,
        "retain_reason": "observer_unverified" if unverifiable else None,
        "extras": extras,
    }
