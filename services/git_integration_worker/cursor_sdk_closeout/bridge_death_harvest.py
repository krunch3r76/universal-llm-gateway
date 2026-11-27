"""Partial harvest when a cursor-sdk bridge dies mid-run (Leg F / agent-bus:10269)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text
from implement_admission.closeout_helpers import cortex_files_root
from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_bridge_partial_harvest,
    emit_sdk_bridge_resume_eligible,
)

logger = get_logger(__name__)

_BRIDGE_DEATH_PARTIAL_REL = (
    "notes/system/threads/{thread_id}-bridge-death-partial-{dispatch_id}.md"
)
_PARK_PARTIAL_REL = "notes/system/threads/{thread_id}-park-partial-{dispatch_id}.md"
_DISCARD_METHODS = frozenset({"idle_discard", "cancel_discard"})


def bridge_death_partial_uri(*, thread_id: str, dispatch_id: str) -> str:
    rel = _BRIDGE_DEATH_PARTIAL_REL.format(thread_id=thread_id, dispatch_id=dispatch_id)
    return f"cortex://{rel}"


def park_partial_uri(*, thread_id: str, dispatch_id: str) -> str:
    """Sidecar URI for the partial harvest written when a run is parked."""
    rel = _PARK_PARTIAL_REL.format(thread_id=thread_id, dispatch_id=dispatch_id)
    return f"cortex://{rel}"


def _load_row(dispatch_id: str) -> dict[str, Any] | None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id, thread_id, state_root, record_json "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _bridge_state_dir(*, dispatch_id: str, state_root: str | None) -> Path | None:
    if state_root:
        root = Path(state_root)
        if root.is_dir():
            return root
    from services.git_integration_worker.cursor_home import dispatch_home_path

    candidate = dispatch_home_path(dispatch_id) / "bridge-state"
    return candidate if candidate.is_dir() else None


def _sidecar_body(
    *,
    dispatch_id: str,
    thread_id: str,
    tool_call_count: int,
    last_tools: list[dict[str, str]] | None,
    cortex_writes_observed: list[str] | None,
    forensics: dict[str, Any] | None,
    resume_eligible: bool,
    status: str = "partial",
    degraded_reason: str = "bridge_read_timeout",
    stop_cause: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "status": status,
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "tool_call_count": tool_call_count,
        "last_tool_calls": list(last_tools or ()),
        "cortex_writes_observed": list(cortex_writes_observed or ()),
        "resume_eligible": resume_eligible,
        "degraded_reason": degraded_reason,
    }
    if stop_cause:
        payload["stop_cause"] = stop_cause
    if forensics:
        payload["forensics"] = forensics
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def read_sidecar_payload(uri: str) -> dict[str, Any] | None:
    rel = uri[len("cortex://") :]
    path = cortex_files_root() / rel
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _merge_discard_onto_prior_stop_sidecar(
    prior: dict[str, Any],
    *,
    method: str,
    tool_call_count: int,
    last_tools: list[dict[str, str]] | None,
    forensics: dict[str, Any] | None,
) -> tuple[int, str | None, dict[str, Any]]:
    """Keep budget-stop evidence; record the discard leg without zeroing the park stop."""
    prior_stop = prior.get("stop_cause")
    stop_cause = str(prior_stop) if isinstance(prior_stop, str) and prior_stop else None
    prior_count = prior.get("tool_call_count")
    try:
        prior_n = int(prior_count) if prior_count is not None else 0
    except (TypeError, ValueError):
        prior_n = 0
    merged_count = max(prior_n, tool_call_count)
    merged_forensics = dict(prior.get("forensics") or {})
    if isinstance(forensics, dict):
        merged_forensics.update(forensics)
    discard_log = merged_forensics.get("discard_records")
    if not isinstance(discard_log, list):
        discard_log = []
    discard_log.append(
        {
            "method": method,
            "tool_call_count": tool_call_count,
            "observed_at_discard": True,
        }
    )
    merged_forensics["discard_records"] = discard_log
    last = list(last_tools or ())
    if not last and prior.get("last_tool_calls"):
        raw = prior.get("last_tool_calls")
        if isinstance(raw, list):
            last = [
                {
                    "tool_name": str(t.get("tool_name", "")),
                    "status": str(t.get("status", "")),
                }
                for t in raw
                if isinstance(t, dict)
            ]
    return merged_count, stop_cause, {"last_tools": last, "forensics": merged_forensics}


def _write_sidecar(uri: str, body: str) -> None:
    rel = uri[len("cortex://") :]
    dest = cortex_files_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    durable_write_text(dest, body, retain_store_root=cortex_files_root())


def emit_partial_harvest_on_park(
    dispatch_id: str,
    *,
    thread_id: str,
    intent_id: str | None,
    method: str,
    tool_call_count: int,
    last_tools: list[dict[str, str]] | None = None,
    stop_cause: str | None = None,
) -> dict[str, Any]:
    """Write the park partial sidecar (steer-restart D6) and emit the harvest event.

    A cooperative ``park_for_restart`` is resume-eligible by construction. Discard
    methods (``idle_discard``, ``cancel_discard``) emit ``resume_eligible=False``
    with cause ``discard`` so harvest matches ``park_kind=cancel_discard``. No
    ``bridge_death_*`` keys are stamped; ``cursor_sdk_park_ledger.mark_parked``
    owns the ``record_json.park`` block that carries the sidecar URI.
    """
    uri = park_partial_uri(thread_id=thread_id, dispatch_id=dispatch_id)
    discard = method in _DISCARD_METHODS
    forensics: dict[str, Any] = {
        "cause": "discard" if discard else "park_for_restart",
        "intent_id": intent_id,
        "method": method,
    }
    resolved_count = tool_call_count
    resolved_last = last_tools
    resolved_stop = stop_cause
    if discard:
        prior = read_sidecar_payload(uri)
        if prior is not None:
            merged_count, merged_stop, merged = _merge_discard_onto_prior_stop_sidecar(
                prior,
                method=method,
                tool_call_count=tool_call_count,
                last_tools=last_tools,
                forensics=forensics,
            )
            resolved_count = merged_count
            resolved_stop = merged_stop or resolved_stop
            resolved_last = merged.get("last_tools")
            forensics = merged.get("forensics") or forensics
        forensics["cause"] = "discard"
    resume_eligible = not discard
    degraded_reason = "discard" if discard else "park_for_restart"
    body = _sidecar_body(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        tool_call_count=resolved_count,
        last_tools=resolved_last,
        cortex_writes_observed=[],
        forensics=forensics,
        resume_eligible=resume_eligible,
        status="parked",
        degraded_reason=degraded_reason,
        stop_cause=resolved_stop,
    )
    _write_sidecar(uri, body)
    emit_sdk_bridge_partial_harvest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        tool_call_count=resolved_count,
        sidecar_uri=uri,
        resume_eligible=resume_eligible,
    )
    return {"sidecar_uri": uri, "resume_eligible": resume_eligible}


def emit_partial_harvest_on_bridge_death(
    dispatch_id: str,
    *,
    tool_call_count: int,
    last_tools: list[dict[str, str]] | None = None,
    cortex_writes_observed: list[str] | None = None,
    thread_id: str | None = None,
    forensics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write bridge-death partial sidecar; stamp ``resume_eligible`` when warranted."""
    row = _load_row(dispatch_id)
    if row is None:
        logger.warning(
            "bridge death partial harvest skipped — dispatch missing: %s",
            dispatch_id,
        )
        return {"sidecar_uri": None, "resume_eligible": False}

    resolved_thread = thread_id or str(row.get("thread_id") or "")
    state_root = row.get("state_root")
    bridge_state = _bridge_state_dir(
        dispatch_id=dispatch_id,
        state_root=str(state_root) if state_root else None,
    )
    resume_eligible = bool(bridge_state is not None and tool_call_count > 0)

    uri = bridge_death_partial_uri(thread_id=resolved_thread, dispatch_id=dispatch_id)
    body = _sidecar_body(
        dispatch_id=dispatch_id,
        thread_id=resolved_thread,
        tool_call_count=tool_call_count,
        last_tools=last_tools,
        cortex_writes_observed=cortex_writes_observed,
        forensics=forensics,
        resume_eligible=resume_eligible,
    )
    _write_sidecar(uri, body)

    patch: dict[str, Any] = {
        "bridge_death_partial_uri": uri,
        "bridge_death_tool_call_count": tool_call_count,
    }
    if resume_eligible:
        patch["resume_eligible"] = True
        patch["bridge_death_degraded_reason"] = "bridge_read_timeout"
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id=dispatch_id, patch=patch
    )

    emit_sdk_bridge_partial_harvest(
        dispatch_id=dispatch_id,
        thread_id=resolved_thread,
        tool_call_count=tool_call_count,
        sidecar_uri=uri,
        resume_eligible=resume_eligible,
    )
    if resume_eligible:
        emit_sdk_bridge_resume_eligible(
            dispatch_id=dispatch_id,
            thread_id=resolved_thread,
            sidecar_uri=uri,
        )

    return {"sidecar_uri": uri, "resume_eligible": resume_eligible}


__all__ = [
    "bridge_death_partial_uri",
    "emit_partial_harvest_on_bridge_death",
    "emit_partial_harvest_on_park",
    "park_partial_uri",
    "read_sidecar_payload",
]
