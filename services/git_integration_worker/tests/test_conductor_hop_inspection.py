"""Loop inspection helper (friction:39253 S2)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    HopBudgetConfig,
    _PARK_REASON_NO_PROGRESS_CAP,
    list_mission_terminal_chain,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_inspection import (
    PARK_REASON_LOOP,
    inspect_hop_loop,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:hop-inspection-fixture"
_CFG = HopBudgetConfig(
    crash_cap_per_row=3,
    no_progress_cap=2,
    mission_cap=48,
    crash_backoff_s=(30.0, 120.0, 300.0),
    reactor_grace_s=120.0,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _terminal_row(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    hop_seq: int,
    hop_from: str,
    closeout_tokens: list[str],
    record_patch: dict,
    park_kind: str | None = None,
) -> dict:
    req = CursorDispatchRequest(
        thread_id="9964",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="conductor",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract="conductor",
        source_repo="/repo",
        lease_key="/repo",
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=hop_seq,
        hop_from=hop_from,
        hop_reason="planned" if hop_seq > 1 else "spawn",
    )
    patch = {"contract": "conductor", "closeout_stop_tokens": closeout_tokens}
    patch.update(record_patch)
    ledger.merge_record_json(dispatch_id=dispatch_id, patch=patch)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    if park_kind:
        with ledger._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET park_kind=? WHERE dispatch_id=?",
                (park_kind, dispatch_id),
            )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def test_eight_distinct_scoreboard_tips_clear() -> None:
    ledger = CursorDispatchLedger.instance()
    prior = "spawn"
    row: dict = {}
    for idx in range(1, 9):
        row = _terminal_row(
            ledger,
            dispatch_id=f"clr-{idx}",
            hop_seq=idx,
            hop_from=prior,
            closeout_tokens=["ROW_HOP"],
            record_patch={
                "hop_entry_gate": "G4",
                "hop_witnessed_done": [],
                "hop_scoreboard_tip": f"sha{idx:02d}",
                "hop_lane_tip": "lane1",
            },
        )
        prior = f"clr-{idx}"
    chain = list_mission_terminal_chain(work_key=_WORK_KEY)
    result = inspect_hop_loop(
        row, chain, config=_CFG, closeout_tokens=frozenset({"ROW_HOP"})
    )
    assert result.outcome == "clear"


def test_consult_pending_identical_signatures_loop() -> None:
    ledger = CursorDispatchLedger.instance()
    stuck = {
        "hop_entry_gate": "G2",
        "hop_witnessed_done": ["G1"],
        "hop_lane_tip": "lane-a",
        "hop_scoreboard_tip": "board-a",
        "hop_next_admit": "harvest G3",
    }
    prior = "spawn"
    row: dict = {}
    for idx in range(1, 4):
        row = _terminal_row(
            ledger,
            dispatch_id=f"cp-{idx}",
            hop_seq=idx,
            hop_from=prior,
            closeout_tokens=["CONSULT_PENDING"],
            record_patch=dict(stuck),
        )
        prior = f"cp-{idx}"
    chain = list_mission_terminal_chain(work_key=_WORK_KEY)
    result = inspect_hop_loop(
        row, chain, config=_CFG, closeout_tokens=frozenset({"CONSULT_PENDING"})
    )
    assert result.outcome == "loop"
    assert result.loop_reason == _PARK_REASON_NO_PROGRESS_CAP


def test_unpaid_shape_unprovable_at_hop_49() -> None:
    ledger = CursorDispatchLedger.instance()
    prior = "spawn"
    row: dict = {}
    for idx in range(1, 50):
        row = _terminal_row(
            ledger,
            dispatch_id=f"unpaid-{idx}",
            hop_seq=idx,
            hop_from=prior,
            closeout_tokens=["ROW_HOP"],
            record_patch={"hop_entry_gate": "G1", "hop_witnessed_done": []},
        )
        prior = f"unpaid-{idx}"
    chain = list_mission_terminal_chain(work_key=_WORK_KEY)
    result = inspect_hop_loop(
        row, chain, config=_CFG, closeout_tokens=frozenset({"ROW_HOP"})
    )
    assert result.outcome == "unprovable"
    assert result.outcome != "loop"


def test_restart_parent_skipped_resume_loop() -> None:
    ledger = CursorDispatchLedger.instance()
    stuck = {
        "hop_entry_gate": "G4",
        "hop_witnessed_done": [],
        "hop_lane_tip": "same",
        "hop_scoreboard_tip": "same",
    }
    _terminal_row(
        ledger,
        dispatch_id="r1",
        hop_seq=1,
        hop_from="spawn",
        closeout_tokens=["PARKED_TRANSPORT"],
        record_patch=stuck,
        park_kind="park_for_restart",
    )
    _terminal_row(
        ledger,
        dispatch_id="r2",
        hop_seq=2,
        hop_from="r1",
        closeout_tokens=["ROW_HOP"],
        record_patch=stuck,
    )
    _terminal_row(
        ledger,
        dispatch_id="r3",
        hop_seq=3,
        hop_from="r2",
        closeout_tokens=["PARKED_TRANSPORT"],
        record_patch=stuck,
        park_kind="park_for_restart",
    )
    _terminal_row(
        ledger,
        dispatch_id="r4",
        hop_seq=4,
        hop_from="r3",
        closeout_tokens=["ROW_HOP"],
        record_patch=stuck,
    )
    row = _terminal_row(
        ledger,
        dispatch_id="r5",
        hop_seq=5,
        hop_from="r4",
        closeout_tokens=["ROW_HOP"],
        record_patch=stuck,
    )
    chain = list_mission_terminal_chain(work_key=_WORK_KEY)
    result = inspect_hop_loop(
        row, chain, config=_CFG, closeout_tokens=frozenset({"ROW_HOP"})
    )
    assert result.outcome == "loop"


def test_recurrence_non_adjacent_full_signature_loop() -> None:
    ledger = CursorDispatchLedger.instance()
    sig_a = {
        "hop_entry_gate": "G4",
        "hop_witnessed_done": ["G1"],
        "hop_lane_tip": "lane-x",
        "hop_scoreboard_tip": "board-x",
    }
    sig_b = {
        "hop_entry_gate": "G4",
        "hop_witnessed_done": ["G1", "G2"],
        "hop_lane_tip": "lane-y",
        "hop_scoreboard_tip": "board-y",
    }
    _terminal_row(
        ledger,
        dispatch_id="rec-1",
        hop_seq=1,
        hop_from="spawn",
        closeout_tokens=["ROW_HOP"],
        record_patch=sig_a,
    )
    _terminal_row(
        ledger,
        dispatch_id="rec-2",
        hop_seq=2,
        hop_from="rec-1",
        closeout_tokens=["ROW_HOP"],
        record_patch=sig_b,
    )
    row = _terminal_row(
        ledger,
        dispatch_id="rec-3",
        hop_seq=3,
        hop_from="rec-2",
        closeout_tokens=["ROW_HOP"],
        record_patch=sig_a,
    )
    chain = list_mission_terminal_chain(work_key=_WORK_KEY)
    result = inspect_hop_loop(
        row, chain, config=_CFG, closeout_tokens=frozenset({"ROW_HOP"})
    )
    assert result.outcome == "loop"
    assert result.loop_reason == PARK_REASON_LOOP
