"""R3: conductor hop reactor (todo:conductor-hop-reactor)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    build_conductor_hop_idempotency_key,
    build_hop_team_dispatch_body,
    hop_owed,
    maybe_fire_conductor_hop_reactor,
    merge_conductor_closeout_hop_authority,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:conductor-hop-fixture"
_ROW_HOP_CLOSEOUT = """\
status: complete
stop: ROW_HOP
hop_seq: 1
"""

_SPURIOUS_DONE_ROW_HOP_CLOSEOUT = """\
| G1 | Architecture / recon | DONE |
| G2 | Frame | DONE |
status: complete
stop: ROW_HOP
hop_seq: 1
"""


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
        "dispatch_id": "pred-hop-1",
        "execution_id": "exec-pred-hop-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_conductor(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    record_patch: dict | None = None,
) -> None:
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
        contract="light-bounded",
        source_repo="/repo",
        lease_key="/repo",
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=1,
        hop_from="spawn-parent",
        hop_reason="spawn",
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"packet_kind": "conductor", "lane": "B"},
    )
    if record_patch:
        ledger.merge_record_json(dispatch_id=req.dispatch_id, patch=record_patch)


def _terminal_row(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str = "pred-hop-1",
    closeout_tokens: list[str] | None = None,
    terminal_status: str = "completed",
) -> dict:
    req = _req(dispatch_id=dispatch_id)
    _admit_conductor(ledger, req)
    if closeout_tokens is not None:
        ledger.merge_record_json(
            dispatch_id=dispatch_id,
            patch={"closeout_stop_tokens": closeout_tokens},
        )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status=terminal_status)
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def test_hop_owed_true_for_planned_terminal_without_exit_tokens() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is True


def test_hop_owed_false_when_done_token() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["DONE"])
    assert hop_owed(row, closeout_tokens=frozenset({"DONE"})) is False


def test_hop_owed_true_when_spurious_table_done_excluded_from_designed() -> None:
    """Designed-stop scoping: table-cell DONE must not block successor admit."""
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_SPURIOUS_DONE_ROW_HOP_CLOSEOUT,
        thread_id="9964",
    )
    ledger.mark_terminal(dispatch_id="pred-hop-1", terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: row[k] for k in row.keys()}
    data = json.loads(row["record_json"])
    assert data.get("closeout_stop_tokens") == ["ROW_HOP"]
    assert "DONE" not in data.get("closeout_stop_tokens", [])
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is True


def test_hop_owed_false_when_exit_persist_token() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_PINNED"])
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_PINNED"})) is False


def test_hop_owed_false_when_successor_already_stamped() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"hop_successor": "already-admitted"},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is False


def test_merge_closeout_stamps_hop_declared_and_tokens() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_ROW_HOP_CLOSEOUT,
        thread_id="9964",
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert fields.get("hop_declared") is True
    data = json.loads(row["record_json"])
    assert "ROW_HOP" in data.get("closeout_stop_tokens", [])


_PARKED_HARVEST_CLOSEOUT = """\
status: complete
stop: PARKED_TRANSPORT
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
NEXT_ADMIT: harvest G1
"""

_NONE_NEXT_ADMIT_CLOSEOUT = """\
status: complete
stop: PARKED_TRANSPORT
NEXT_ADMIT: none
"""


def test_merge_closeout_stamps_closeout_turn_and_harvest_owed() -> None:
    """L1-1: production path stamps closeout_turn + closeout_harvest_owed."""
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_PARKED_HARVEST_CLOSEOUT,
        thread_id="9964",
        closeout_turn=48,
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data.get("closeout_turn") == 48
    assert data.get("closeout_harvest_owed") is True


def test_merge_closeout_harvest_owed_false_when_next_admit_none() -> None:
    """L1-2: NEXT_ADMIT:none ⇒ closeout_harvest_owed False."""
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_NONE_NEXT_ADMIT_CLOSEOUT,
        thread_id="9964",
        closeout_turn=48,
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data.get("closeout_harvest_owed") is False


def test_build_hop_team_dispatch_body_clones_predecessor() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["op"] == "generate"
    assert body["seat"] == "cursor-sdk"
    assert body["caller_agent"] == "conductor-hop"
    assert body["reuse_thread"] == "9964"
    assert body["dispatch_thread_id"] == "9964"
    assert body["source_ref"] == _WORK_KEY
    assert body["packet_kind"] == "conductor"
    assert body["lane"] == "B"
    assert str(body["model"]).startswith("cursor/")
    assert body["generation_options"]["idempotency_key"] == build_conductor_hop_idempotency_key(
        "pred-hop-1"
    )
    assert body["hop_seq"] == 2
    assert body["hop_reason"] == "planned"
    assert body["hop_from"] == "pred-hop-1"


def test_build_hop_team_dispatch_body_routing_model_from_record_json() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"model": "cursor/composer-2.5"},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["model"] == "cursor/composer-2.5"


def test_build_hop_team_dispatch_body_source_ref_from_record_json_only() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET source_ref=NULL, work_key=NULL "
            "WHERE dispatch_id='pred-hop-1'"
        )
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"source_ref": _WORK_KEY},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["source_ref"] == _WORK_KEY


def test_merge_uses_outcome_body_not_json_envelope() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
        SdkRunOutcome,
    )
    from services.git_integration_worker.cursor_sdk_closeout.implement_body import (
        build_implement_closeout_body,
    )

    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    outcome = SdkRunOutcome(
        body=_ROW_HOP_CLOSEOUT,
        status="finished",
        duration_ms=100,
        tool_call_count=1,
    )
    json_body = build_implement_closeout_body(
        dispatch_id="pred-hop-1",
        outcome=outcome,
        degraded_reason="conductor_row_hop",
        sidecar_ref="workspaces://x/sidecar.md",
        result_bytes=100,
        thread_id="9964",
        work_item_ref=_WORK_KEY,
    )
    assert "ROW_HOP" not in json_body
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=outcome.body,
        thread_id="9964",
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert "ROW_HOP" in data.get("closeout_stop_tokens", [])
    assert data.get("hop_declared") is True


@pytest.mark.asyncio
async def test_maybe_fire_reactor_stamps_successor_on_admit() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        AsyncMock(return_value=(True, {"dispatch_id": "succ-hop-2"})),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert fields.get("hop_successor") == "succ-hop-2"


@pytest.mark.asyncio
async def test_reactor_exception_does_not_block_second_hop_attempt() -> None:
    """R-4: reactor POST failure is recorded; idempotent re-entry still works."""
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        AsyncMock(side_effect=[RuntimeError("relay down"), (True, {"dispatch_id": "succ-hop-3"})]),
    ):
        with pytest.raises(RuntimeError):
            await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert fields.get("hop_successor") == "succ-hop-3"


@pytest.mark.asyncio
async def test_maybe_fire_emits_skipped_when_not_conductor() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="impl-row-1", message="implement task")
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
        contract="implement",
        source_repo="/repo",
        lease_key="/repo",
        source_ref="todo:other",
    )
    ledger.mark_terminal(dispatch_id="impl-row-1", terminal_status="completed")
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.emit_frontier_sdk_conductor_hop_skipped",
    ) as skipped:
        await maybe_fire_conductor_hop_reactor(dispatch_id="impl-row-1")
    skipped.assert_called_once()
    assert skipped.call_args.kwargs["gate"] == "not_conductor_row"


@pytest.mark.asyncio
async def test_maybe_fire_reactor_stamps_admit_error_on_failure() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        AsyncMock(return_value=(False, {"status_code": 503, "error": "down"})),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert "hop_admit_error" in fields
    assert fields.get("hop_successor") is None
