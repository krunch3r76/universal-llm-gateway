"""Conductor hop body carriers (summoning thread, no house leak)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    build_hop_team_dispatch_body,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "9964",
        "model": "cursor/composer-2.5",
        "dispatch_id": "pred-hop-body-1",
        "execution_id": "exec-pred-hop-body-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _terminal_row(
    ledger: CursorDispatchLedger,
    *,
    summoning_thread_id: str = "9964",
) -> dict:
    req = _req()
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract="conductor",
        source_repo="/repo",
        lease_key="/repo",
        work_key="todo:conductor-hop-fixture",
        source_ref="todo:conductor-hop-fixture",
        hop_seq=1,
        hop_from="spawn-parent",
        hop_reason="spawn",
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "contract": "conductor",
            "lane": "B",
            "closeout_stop_tokens": ["ROW_HOP"],
            "summoning_thread_id": summoning_thread_id,
            "source_ref": "todo:conductor-hop-fixture",
        },
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def test_hop_body_preserves_summoning_thread_id() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, summoning_thread_id="9964")
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._scoreboard_text_for_row",
        return_value=("", ""),
    ):
        body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body.get("dispatch_thread_id") == "9964"
    opts = body.get("generation_options") or {}
    assert opts.get("summoning_thread_id") == "9964"
    assert "house_critic_model" not in opts
    assert "house_induction_binds" not in opts


def test_watch_dir_isolation_active_gw(tmp_path: Path) -> None:
    import bus_watch.fable_lock

    assert str(bus_watch.fable_lock.WATCH_DIR).startswith(str(tmp_path))
