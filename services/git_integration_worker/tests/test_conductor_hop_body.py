"""Conductor hop body carriers (summoning thread, no house leak)."""

from __future__ import annotations

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


def test_hop_body_inherits_parent_work_key_and_model_knobs() -> None:
    """Successor generate body must carry predecessor work_key for ledger/registry."""
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    ledger = CursorDispatchLedger.instance()
    work_key = "todo:interlocutor-facade"
    knobs = {"effort": "low", "fast": "true"}
    req = _req(dispatch_id="pred-interlocutor-hop")
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
        work_key=work_key,
        source_ref=work_key,
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
            "summoning_thread_id": "9964",
            "source_ref": work_key,
            "model_knobs": {"effort": "xhigh"},
            "hop_reactor_model_knobs": knobs,
        },
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    row = {k: row[k] for k in row.keys()}
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._scoreboard_text_for_row",
        return_value=("", ""),
    ):
        body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["work_key"] == work_key
    assert body["model_knobs"] == knobs
    wire = dict(body)
    wire["dispatch_id"] = "successor-interlocutor-hop"
    parsed = TeamDispatchGenerateBody.model_validate(wire)
    assert parsed.work_key == work_key


def test_hop_body_work_key_falls_back_to_record_json_when_row_column_empty() -> None:
    ledger = CursorDispatchLedger.instance()
    work_key = "todo:hop-work-key-fallback"
    row = _terminal_row(ledger)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET work_key=NULL WHERE dispatch_id=?",
            ("pred-hop-body-1",),
        )
    ledger.merge_record_json(
        dispatch_id="pred-hop-body-1",
        patch={"work_key": work_key},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-body-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._scoreboard_text_for_row",
        return_value=("", ""),
    ):
        body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["work_key"] == work_key


def test_hop_body_model_knobs_fallback_from_rec_when_hop_fields_omit_knobs() -> None:
    from implement_admission.conductor_admit_model import (
        resolve_hop_successor_default_from_pipeline,
    )

    ledger = CursorDispatchLedger.instance()
    rec_knobs = {"effort": "medium", "fast": "false"}
    row = _terminal_row(ledger)
    ledger.merge_record_json(
        dispatch_id="pred-hop-body-1",
        patch={"model_knobs": rec_knobs},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-body-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    pipeline_default = resolve_hop_successor_default_from_pipeline()
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._scoreboard_text_for_row",
        return_value=("", ""),
    ), patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_model.resolve_hop_successor_model_fields",
        return_value={"model": pipeline_default["model"]},
    ):
        body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["model"] == pipeline_default["model"]
    assert body["model_knobs"] == rec_knobs


def test_watch_dir_isolation_active_gw(tmp_path: Path) -> None:
    import bus_watch.fable_lock

    assert str(bus_watch.fable_lock.WATCH_DIR).startswith(str(tmp_path))
