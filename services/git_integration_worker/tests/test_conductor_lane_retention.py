"""Lane retention: a conductor mission keeps its branch across its own closeouts.

Worker 13713 (2026-10-01) declared ``land_disposition: unlanded <tip>`` on two
``ROW_HOP`` closeouts and lost ``cursor-sdk/lane-13713`` each time (archive
tags ``lane-13713-af2602e4`` / ``lane-13713-1b0635e3``); worker 13691 hit
``CURSOR_LANE_PIN_FAILED`` on resume (a:37043). Settlement must not discharge,
charge, or abandon a branch an open conductor mission still owns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_branch_debt import get_branch_debt
from services.git_integration_worker.cursor_sdk_branch_discharge import DischargeResult
from services.git_integration_worker.cursor_sdk_branch_terminal import (
    settle_lane_branch,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_lane_retention import (
    RETAINED_FOR_MISSION,
    closeout_stop_tokens,
    conductor_mission_open,
    lane_retention_reason,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_THREAD = "13713"
_BRANCH = f"cursor-sdk/lane-{_THREAD}"
_TIP = "af2602e4d740ba35db3dd353e32ade88e4bd027b"


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


@pytest.fixture
def discharge_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace the git-touching discharge with a recorder; settlement stays pure."""
    calls: list[dict] = []

    def _fake_discharge(**kwargs):
        calls.append(kwargs)
        return DischargeResult(
            discharged=True,
            branch=str(kwargs.get("branch_name") or ""),
            verb=str(kwargs.get("verb") or ""),
        )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_terminal.discharge",
        _fake_discharge,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_terminal.remove_land_required_tag",
        lambda **_kwargs: None,
    )
    return calls


def _admit(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    contract: str,
    work_key: str,
    thread_id: str = _THREAD,
    lease_key: str = "/repo",
) -> None:
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message=contract,
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
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        contract=contract,
        source_repo=lease_key,
        lease_key=lease_key,
        work_key=work_key,
        source_ref=work_key,
    )


def _settle(*, dispatch_id: str, closeout_text: str, tmp_path: Path):
    return settle_lane_branch(
        source_repo=tmp_path / "repo",
        branch_name=_BRANCH,
        thread_id=_THREAD,
        dispatch_id=dispatch_id,
        closeout_text=closeout_text,
        commits_ahead=1,
        landed=False,
        head_sha=_TIP,
        files=["libs/x.py"],
    )


_HOP_CLOSEOUT = (
    "status: partial\n"
    f"land_disposition: unlanded {_TIP}\n"
    "land_reason: R1-R2 stay on the lane\n"
    "stop: ROW_HOP\n"
    "hop_seq: 2\n"
)


def test_conductor_row_hop_closeout_retains_lane(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    """13713 hop 2: ``unlanded <tip>`` on a ROW_HOP must not archive the branch."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-2", contract="conductor", work_key="todo:swap")
    ledger.mark_terminal(dispatch_id="hop-2", terminal_status="completed")
    settlement = _settle(dispatch_id="hop-2", closeout_text=_HOP_CLOSEOUT, tmp_path=tmp_path)
    assert settlement.outcome == RETAINED_FOR_MISSION
    assert settlement.branch == _BRANCH
    assert settlement.detail == "conductor_mission_open:ROW_HOP"
    assert discharge_calls == []
    assert get_branch_debt(branch_name=_BRANCH) is None


@pytest.mark.parametrize(
    "token", ["CONSULT_PENDING", "PARKED_TRANSPORT", "ROW_PINNED", "HOLD_MERGE", "OPERATOR_GATE"]
)
def test_every_non_done_designed_stop_retains_lane(
    tmp_path: Path, discharge_calls: list[dict], token: str
) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-x", contract="conductor", work_key="todo:swap")
    ledger.mark_terminal(dispatch_id="hop-x", terminal_status="completed")
    text = f"land_disposition: unlanded {_TIP}\nstop: {token}\n"
    settlement = _settle(dispatch_id="hop-x", closeout_text=text, tmp_path=tmp_path)
    assert settlement.outcome == RETAINED_FOR_MISSION
    assert discharge_calls == []


def test_conductor_done_closeout_settles(tmp_path: Path, discharge_calls: list[dict]) -> None:
    """Only the DONE closeout settles the mission's branch."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-last", contract="conductor", work_key="todo:swap")
    ledger.mark_terminal(dispatch_id="hop-last", terminal_status="completed")
    text = "land_disposition: landed\nstop: DONE\n"
    settlement = _settle(dispatch_id="hop-last", closeout_text=text, tmp_path=tmp_path)
    assert settlement.outcome == "discharged"
    assert len(discharge_calls) == 1
    assert discharge_calls[0]["verb"] == "landed"


def test_crashed_conductor_hop_retains_lane_for_resume(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    """A failed hop with no stop token is resumed on the same branch."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-crash", contract="conductor", work_key="todo:swap")
    ledger.mark_terminal(dispatch_id="hop-crash", terminal_status="failed")
    settlement = _settle(dispatch_id="hop-crash", closeout_text="", tmp_path=tmp_path)
    assert settlement.outcome == RETAINED_FOR_MISSION
    assert settlement.detail == "conductor_mission_open:crash"
    assert discharge_calls == []


def test_nested_child_under_open_conductor_retains_lane(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    """13713 hop 3: a nested limb that ends badly must not reset the shared lane."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-3", contract="conductor", work_key="todo:swap")
    _admit(
        ledger,
        dispatch_id="limb-r3",
        contract="pure-mechanical",
        work_key="packet:swap-r3",
        lease_key="/repo-limb",
    )
    ledger.merge_record_json(dispatch_id="limb-r3", patch={"nest_under": "hop-3"})
    ledger.mark_terminal(dispatch_id="limb-r3", terminal_status="failed")
    settlement = _settle(
        dispatch_id="limb-r3",
        closeout_text="land_disposition: discard\nland_reason: tests red\n",
        tmp_path=tmp_path,
    )
    assert settlement.outcome == RETAINED_FOR_MISSION
    assert settlement.detail is not None
    assert settlement.detail.startswith("nested_under_open_conductor:hop-3:conductor_live:")
    assert discharge_calls == []


def test_plain_row_without_mission_settles_as_before(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="solo-1", contract="freeform", work_key="agent-bus:1")
    ledger.mark_terminal(dispatch_id="solo-1", terminal_status="completed")
    settlement = _settle(
        dispatch_id="solo-1",
        closeout_text="land_disposition: discard\nland_reason: scratch\n",
        tmp_path=tmp_path,
    )
    assert settlement.outcome == "discharged"
    assert len(discharge_calls) == 1


def test_unknown_dispatch_settles_as_before(tmp_path: Path, discharge_calls: list[dict]) -> None:
    """No ledger row means no mission to protect — the old path runs unchanged."""
    settlement = _settle(
        dispatch_id="never-admitted",
        closeout_text="land_disposition: discard\nland_reason: scratch\n",
        tmp_path=tmp_path,
    )
    assert settlement.outcome == "discharged"
    assert len(discharge_calls) == 1


def test_closeout_tokens_prefer_text_over_stamp() -> None:
    row = {"record_json": '{"closeout_stop_tokens":["PARKED_TRANSPORT"]}'}
    assert closeout_stop_tokens(row, "stop: DONE\n") == frozenset({"DONE"})
    assert closeout_stop_tokens(row, None) == frozenset({"PARKED_TRANSPORT"})
    assert closeout_stop_tokens({"record_json": ""}, None) == frozenset()


def test_conductor_mission_open_reasons() -> None:
    live = {"contract": "conductor", "status": "running", "record_json": ""}
    assert conductor_mission_open(live, closeout_text=None) == "conductor_live:running"
    done = {"contract": "conductor", "status": "completed", "record_json": ""}
    assert conductor_mission_open(done, closeout_text="stop: DONE\n") is None
    other = {"contract": "freeform", "status": "completed", "record_json": ""}
    assert conductor_mission_open(other, closeout_text="stop: ROW_HOP\n") is None


def test_lane_retention_reason_unknown_row_is_none() -> None:
    assert lane_retention_reason(dispatch_id="nope", thread_id=_THREAD) is None
