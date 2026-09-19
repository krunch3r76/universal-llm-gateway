"""H2 park gate — refuse/release and envelope shape."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    HOP_PARKED_KEY,
    HOP_PARK_REASON_KEY,
)
from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
    CONDUCTOR_MISSION_PARKED_CODE,
    ConductorMissionParked,
    HOP_PARK_RELEASED_AT_KEY,
    mission_park_state,
    refuse_parked_conductor_mission,
    release_mission_park,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:park-gate-fixture"


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
        "dispatch_id": "park-gate-1",
        "execution_id": "exec-park-gate-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_terminal(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str = "parked-row",
    hop_seq: int = 2,
    parked: bool = True,
) -> None:
    req = _req(dispatch_id=dispatch_id)
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
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=hop_seq,
        hop_from="spawn-parent",
        hop_reason="spawn",
    )
    if parked:
        ledger.merge_record_json(
            dispatch_id=dispatch_id,
            patch={
                HOP_PARKED_KEY: True,
                HOP_PARK_REASON_KEY: "hop_budget_no_progress_cap",
            },
        )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")


def test_mission_park_state_returns_latest_parked_row() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_terminal(ledger, dispatch_id="park-old", hop_seq=1, parked=False)
    _admit_terminal(ledger, dispatch_id="park-new", hop_seq=3)
    with ledger._connect() as conn:
        state = mission_park_state(conn, work_key=_WORK_KEY, thread_id="9964")
    assert state is not None
    assert state.parked_dispatch_id == "park-new"
    assert state.hop_seq == 3


def test_refuse_parked_conductor_mission_raises_envelope() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_terminal(ledger)
    with ledger._connect() as conn:
        with pytest.raises(ConductorMissionParked) as exc_info:
            refuse_parked_conductor_mission(
                conn,
                work_key=_WORK_KEY,
                thread_id="9964",
                read_only=False,
                contract="conductor",
            )
    envelope = exc_info.value.to_protocol_error().to_dict()
    assert envelope["code"] == CONDUCTOR_MISSION_PARKED_CODE
    assert envelope["retryable"] is False
    assert envelope["data"]["release"] == "generation_options.hop_park_release=true"


def test_release_mission_park_allows_subsequent_admit() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_terminal(ledger)
    with ledger._connect() as conn:
        released = release_mission_park(
            conn,
            work_key=_WORK_KEY,
            thread_id="9964",
            caller_agent="liaison",
        )
        assert released is not None
        refuse_parked_conductor_mission(
            conn,
            work_key=_WORK_KEY,
            thread_id="9964",
            read_only=False,
            contract="conductor",
        )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='parked-row'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert HOP_PARK_RELEASED_AT_KEY in data


def test_admit_with_hop_park_release_refuses_without_flag() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_terminal(ledger)
    req = _req(dispatch_id="succ-after-park")
    with pytest.raises(ConductorMissionParked):
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent="liaison",
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
            work_key=_WORK_KEY,
            source_ref=_WORK_KEY,
            hop_seq=3,
            hop_from="parked-row",
            hop_reason="planned",
        )


def test_admit_with_hop_park_release_and_flag_succeeds() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_terminal(ledger)
    req = _req(dispatch_id="succ-after-release")
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="liaison",
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
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=3,
        hop_from="parked-row",
        hop_reason="planned",
        hop_park_release=True,
    )
