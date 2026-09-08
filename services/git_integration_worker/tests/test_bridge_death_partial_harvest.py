"""Leg F partial harvest on bridge death (AC-S1-F1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.bridge_death_harvest import (
    bridge_death_partial_uri,
    emit_partial_harvest_on_bridge_death,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _insert_dispatch(
    *,
    dispatch_id: str,
    thread_id: str,
    state_root: Path,
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="work",
    )
    from services.git_integration_worker.models.cursor_api import CursorDispatchResponse

    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
    )
    ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(state_root))


def test_partial_harvest_sidecar_matches_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S1-F1: N≥10 tool calls → sidecar with matching counts and last tools."""
    cortex_root = tmp_path / "cortex"
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.bridge_death_harvest.cortex_files_root",
        lambda: cortex_root,
    )
    dispatch_id = "disp-harvest-f1"
    thread_id = "10269"
    state_root = tmp_path / "bridge-state"
    state_root.mkdir()
    _insert_dispatch(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        state_root=state_root,
    )

    last_tools = [
        {"tool_name": "fs", "status": "completed"},
        {"tool_name": "grep", "status": "completed"},
        {"tool_name": "shell", "status": "completed"},
    ]
    events: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.bridge_death_harvest.emit_sdk_bridge_partial_harvest",
        lambda **kwargs: events.append(json.dumps(kwargs, sort_keys=True)),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.bridge_death_harvest.emit_sdk_bridge_resume_eligible",
        lambda **_: None,
    )

    result = emit_partial_harvest_on_bridge_death(
        dispatch_id,
        tool_call_count=52,
        last_tools=last_tools,
        cortex_writes_observed=["cortex://notes/system/threads/x.md"],
        thread_id=thread_id,
        forensics={"cause": "ReadTimeout: bridge read timed out"},
    )

    uri = bridge_death_partial_uri(thread_id=thread_id, dispatch_id=dispatch_id)
    assert result["sidecar_uri"] == uri
    assert result["resume_eligible"] is True

    sidecar_path = cortex_root / uri.removeprefix("cortex://")
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert payload["tool_call_count"] == 52
    assert payload["last_tool_calls"] == last_tools
    assert payload["resume_eligible"] is True
    assert events

    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    rec = json.loads(row["record_json"])
    assert rec["resume_eligible"] is True
    assert rec["bridge_death_degraded_reason"] == "bridge_read_timeout"
