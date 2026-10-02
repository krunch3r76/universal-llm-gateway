"""H2 park gate — refuse/release and envelope shape."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    WorkerThreadOccupied,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    HOP_PARK_REASON_KEY,
    HOP_PARKED_KEY,
)
from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
    CONDUCTOR_MISSION_PARKED_CODE,
    HOP_PARK_RELEASED_AT_KEY,
    ConductorMissionParked,
    mission_park_state,
    refuse_parked_conductor_mission,
    release_mission_parks,
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


def test_release_mission_parks_allows_subsequent_admit() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_terminal(ledger)
    with ledger._connect() as conn:
        released = release_mission_parks(
            conn,
            work_key=_WORK_KEY,
            thread_id="9964",
            caller_agent="liaison",
            post_commit_emits=[],
        )
        assert released
        assert released[0].parked_dispatch_id == "parked-row"
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


def test_ac7_set_release_stamps_all_parks_after_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC7: hop_park_release clears every open budget park; emits after commit."""
    ledger = CursorDispatchLedger.instance()
    for dispatch_id, hop_seq in (("park-a", 1), ("park-b", 2)):
        _admit_terminal(ledger, dispatch_id=dispatch_id, hop_seq=hop_seq, parked=False)
    for dispatch_id in ("park-a", "park-b"):
        ledger.merge_record_json(
            dispatch_id=dispatch_id,
            patch={
                HOP_PARKED_KEY: True,
                HOP_PARK_REASON_KEY: "hop_budget_mission_cap",
            },
        )
    emit_observations: list[tuple[str, bool]] = []

    def _recorder(**kwargs: object) -> None:
        parked = str(kwargs.get("parked_dispatch_id"))
        with CursorDispatchLedger.instance()._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (parked,),
            ).fetchone()
        data = json.loads(row["record_json"])
        emit_observations.append((parked, HOP_PARK_RELEASED_AT_KEY in data))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_hop_events.emit_frontier_sdk_conductor_hop_park_released",
        _recorder,
    )
    req = _req(dispatch_id="release-all")
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
        hop_from="park-b",
        hop_reason="planned",
        hop_park_release=True,
    )
    assert sorted(pid for pid, _ in emit_observations) == ["park-a", "park-b"]
    assert all(stamped for _, stamped in emit_observations)
    with ledger._connect() as conn:
        for dispatch_id in ("park-a", "park-b"):
            row = conn.execute(
                "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            assert HOP_PARK_RELEASED_AT_KEY in json.loads(row["record_json"])


def test_ac8_rollback_skips_emit_on_thread_occupied(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC8: failed admit rolls back park stamps and emits nothing."""
    ledger = CursorDispatchLedger.instance()
    _admit_terminal(ledger, dispatch_id="parked-only")
    lb_req = _req(
        dispatch_id="lb-holder",
        thread_id="9964",
        message="light-bounded occupant",
    )
    ledger.admit(
        req=lb_req,
        fingerprint=ledger.fingerprint(lb_req),
        execution_id=lb_req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=lb_req.dispatch_id,
            thread_id=lb_req.thread_id,
            model_id="composer-2.5",
        ),
        contract="light-bounded",
        source_repo="/repo-lb",
        lease_key="/repo-lb",
        identity_class="adhoc",
        work_key=None,
        source_ref="adhoc:lb-occupant",
    )
    calls: list[str] = []

    def _recorder(**kwargs: object) -> None:
        calls.append(str(kwargs.get("parked_dispatch_id")))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_hop_events.emit_frontier_sdk_conductor_hop_park_released",
        _recorder,
    )
    req = _req(dispatch_id="blocked-release")
    with pytest.raises(WorkerThreadOccupied):
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
            hop_from="parked-only",
            hop_reason="planned",
            hop_park_release=True,
        )
    assert calls == []
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='parked-only'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert HOP_PARK_RELEASED_AT_KEY not in data


def test_ac10_roster_play_read_only_arms_flag() -> None:
    """AC10: arm_hop_park_release sets hop_park_release without stamping."""
    from libs.bus_watch.spawn_wake.park_release import arm_hop_park_release

    ledger = CursorDispatchLedger.instance()
    for dispatch_id in ("cap-a", "cap-b"):
        _admit_terminal(ledger, dispatch_id=dispatch_id, hop_seq=1, parked=False)
    for dispatch_id in ("cap-a", "cap-b"):
        ledger.merge_record_json(
            dispatch_id=dispatch_id,
            patch={
                HOP_PARKED_KEY: True,
                HOP_PARK_REASON_KEY: "hop_budget_mission_cap",
            },
        )
    body: dict = {"reuse_thread": "9964"}
    assert arm_hop_park_release(body, _WORK_KEY) is True
    assert body["generation_options"]["hop_park_release"] is True
    with ledger._connect() as conn:
        for dispatch_id in ("cap-a", "cap-b"):
            row = conn.execute(
                "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            assert HOP_PARK_RELEASED_AT_KEY not in json.loads(row["record_json"])


def test_ac10_mixed_cap_reasons_do_not_arm() -> None:
    from libs.bus_watch.spawn_wake.park_release import arm_hop_park_release

    ledger = CursorDispatchLedger.instance()
    _admit_terminal(ledger, dispatch_id="cap-only", hop_seq=1, parked=False)
    _admit_terminal(ledger, dispatch_id="progress-cap", hop_seq=2, parked=False)
    ledger.merge_record_json(
        dispatch_id="cap-only",
        patch={
            HOP_PARKED_KEY: True,
            HOP_PARK_REASON_KEY: "hop_budget_mission_cap",
        },
    )
    ledger.merge_record_json(
        dispatch_id="progress-cap",
        patch={
            HOP_PARKED_KEY: True,
            HOP_PARK_REASON_KEY: "hop_budget_no_progress_cap",
        },
    )
    body: dict = {}
    assert arm_hop_park_release(body, _WORK_KEY) is False
    assert "generation_options" not in body
