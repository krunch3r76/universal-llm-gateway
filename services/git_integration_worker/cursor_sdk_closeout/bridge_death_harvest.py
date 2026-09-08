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


def bridge_death_partial_uri(*, thread_id: str, dispatch_id: str) -> str:
    rel = _BRIDGE_DEATH_PARTIAL_REL.format(
        thread_id=thread_id, dispatch_id=dispatch_id
    )
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
) -> str:
    payload: dict[str, Any] = {
        "status": "partial",
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "tool_call_count": tool_call_count,
        "last_tool_calls": list(last_tools or ()),
        "cortex_writes_observed": list(cortex_writes_observed or ()),
        "resume_eligible": resume_eligible,
        "degraded_reason": "bridge_read_timeout",
    }
    if forensics:
        payload["forensics"] = forensics
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


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

    uri = bridge_death_partial_uri(
        thread_id=resolved_thread, dispatch_id=dispatch_id
    )
    rel = uri[len("cortex://") :]
    body = _sidecar_body(
        dispatch_id=dispatch_id,
        thread_id=resolved_thread,
        tool_call_count=tool_call_count,
        last_tools=last_tools,
        cortex_writes_observed=cortex_writes_observed,
        forensics=forensics,
        resume_eligible=resume_eligible,
    )
    dest = cortex_files_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    durable_write_text(dest, body, retain_store_root=cortex_files_root())

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
]
