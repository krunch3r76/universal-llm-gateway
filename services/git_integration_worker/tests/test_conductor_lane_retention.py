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
from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
    get_disposition,
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


def _settle(
    *,
    dispatch_id: str,
    closeout_text: str,
    tmp_path: Path,
    branch_name: str = _BRANCH,
    thread_id: str = _THREAD,
):
    return settle_lane_branch(
        source_repo=tmp_path / "repo",
        branch_name=branch_name,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        closeout_text=closeout_text,
        commits_ahead=1,
        landed=False,
        head_sha=_TIP,
        files=["libs/x.py"],
    )


def _running(ledger: CursorDispatchLedger, dispatch_id: str) -> None:
    """Closeout settlement runs before ``mark_terminal``; the row is still running."""
    ledger.mark_running(dispatch_id=dispatch_id)


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
    _running(ledger, "hop-2")
    settlement = _settle(
        dispatch_id="hop-2", closeout_text=_HOP_CLOSEOUT, tmp_path=tmp_path
    )
    assert settlement.outcome == RETAINED_FOR_MISSION
    assert settlement.branch == _BRANCH
    assert settlement.detail == "conductor_mission_open:ROW_HOP"
    assert discharge_calls == []
    assert get_branch_debt(branch_name=_BRANCH) is None


@pytest.mark.parametrize(
    "token",
    [
        "CONSULT_PENDING",
        "PARKED_TRANSPORT",
        "ROW_PINNED",
        "HOLD_MERGE",
        "OPERATOR_GATE",
    ],
)
def test_every_non_done_designed_stop_retains_lane(
    tmp_path: Path, discharge_calls: list[dict], token: str
) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-x", contract="conductor", work_key="todo:swap")
    _running(ledger, "hop-x")
    text = f"land_disposition: unlanded {_TIP}\nstop: {token}\n"
    settlement = _settle(dispatch_id="hop-x", closeout_text=text, tmp_path=tmp_path)
    assert settlement.outcome == RETAINED_FOR_MISSION
    assert discharge_calls == []


def test_conductor_done_closeout_settles(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    """Only the DONE closeout settles the mission's branch."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-last", contract="conductor", work_key="todo:swap")
    _running(ledger, "hop-last")
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
    _running(ledger, "hop-crash")
    settlement = _settle(dispatch_id="hop-crash", closeout_text="", tmp_path=tmp_path)
    assert settlement.outcome == RETAINED_FOR_MISSION
    assert settlement.detail == "conductor_mission_open:crash"
    assert discharge_calls == []


def test_nested_child_under_open_conductor_retains_lane(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    """13713 hop 3: a nested limb that ends badly must not reset the shared lane.

    The limb runs on its own worker thread (13718 under 13713 in the specimen);
    a second top-level admit on the parent's thread is refused by design.
    """
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-3", contract="conductor", work_key="todo:swap")
    _admit(
        ledger,
        dispatch_id="limb-r3",
        contract="pure-mechanical",
        work_key="packet:swap-r3",
        thread_id="13718",
        lease_key="/repo-limb",
    )
    ledger.merge_record_json(dispatch_id="limb-r3", patch={"nest_under": "hop-3"})
    _running(ledger, "hop-3")
    _running(ledger, "limb-r3")
    settlement = _settle(
        dispatch_id="limb-r3",
        closeout_text="land_disposition: discard\nland_reason: tests red\n",
        tmp_path=tmp_path,
    )
    assert settlement.outcome == RETAINED_FOR_MISSION
    assert settlement.detail is not None
    assert settlement.detail.startswith(
        "nested_under_open_conductor:hop-3:conductor_live:"
    )
    assert discharge_calls == []


def test_plain_row_without_mission_settles_as_before(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="solo-1", contract="freeform", work_key="agent-bus:1")
    _running(ledger, "solo-1")
    settlement = _settle(
        dispatch_id="solo-1",
        closeout_text="land_disposition: discard\nland_reason: scratch\n",
        tmp_path=tmp_path,
    )
    assert settlement.outcome == "discharged"
    assert len(discharge_calls) == 1


def test_unknown_dispatch_settles_as_before(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    """No ledger row means no mission to protect — the old path runs unchanged."""
    settlement = _settle(
        dispatch_id="never-admitted",
        closeout_text="land_disposition: discard\nland_reason: scratch\n",
        tmp_path=tmp_path,
    )
    assert settlement.outcome == "discharged"
    assert len(discharge_calls) == 1


def test_conductor_done_with_commits_and_no_disposition_opens_debt(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    """Gate 3: a DONE closeout that names no disposition still owes the branch.

    Settlement runs while the row is ``running``. The live-status shortcut
    would retain this and never open ``land_required``.
    """
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-done", contract="conductor", work_key="todo:swap")
    _running(ledger, "hop-done")
    settlement = _settle(
        dispatch_id="hop-done",
        closeout_text="status: complete\nstop: DONE\n",
        tmp_path=tmp_path,
    )
    assert settlement.outcome == "debt_opened"
    assert discharge_calls == []
    debt = get_branch_debt(branch_name=_BRANCH)
    assert debt is not None and debt.open


def test_nested_limb_own_branch_settles(
    tmp_path: Path, discharge_calls: list[dict]
) -> None:
    """A nested limb on its own lane branch is not the conductor's branch.

    Retaining it would keep ``cursor-sdk/lane-13718`` forever while the
    mission continues on ``cursor-sdk/lane-13713``.
    """
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-3", contract="conductor", work_key="todo:swap")
    limb_thread = "13718"
    limb_branch = f"cursor-sdk/lane-{limb_thread}"
    _admit(
        ledger,
        dispatch_id="limb-r3",
        contract="pure-mechanical",
        work_key="packet:swap-r3",
        thread_id=limb_thread,
        lease_key="/repo-limb",
    )
    ledger.merge_record_json(dispatch_id="limb-r3", patch={"nest_under": "hop-3"})
    _running(ledger, "hop-3")
    _running(ledger, "limb-r3")
    settlement = _settle(
        dispatch_id="limb-r3",
        closeout_text="land_disposition: discard\nland_reason: tests red\n",
        tmp_path=tmp_path,
        branch_name=limb_branch,
        thread_id=limb_thread,
    )
    assert settlement.outcome == "discharged"
    assert len(discharge_calls) == 1
    assert discharge_calls[0]["branch_name"] == limb_branch


def test_ledger_read_failure_still_settles(
    tmp_path: Path, discharge_calls: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ledger that cannot be read returns None and the old settlement runs."""

    def _boom(_dispatch_id: str) -> dict:
        raise RuntimeError("ledger down")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout."
        "conductor_lane_retention._load_row",
        _boom,
    )
    settlement = _settle(
        dispatch_id="hop-2",
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
    # Ancestors keep the live-status shortcut. The closing row does not.
    assert conductor_mission_open(live, closeout_text=None) == "conductor_live:running"
    assert (
        conductor_mission_open(live, closeout_text="stop: ROW_HOP\n", closing=True)
        == "conductor_mission_open:ROW_HOP"
    )
    assert (
        conductor_mission_open(live, closeout_text="stop: DONE\n", closing=True) is None
    )
    assert (
        conductor_mission_open(live, closeout_text=None, closing=True)
        == "conductor_mission_open:crash"
    )
    done = {"contract": "conductor", "status": "completed", "record_json": ""}
    assert conductor_mission_open(done, closeout_text="stop: DONE\n") is None
    other = {"contract": "freeform", "status": "completed", "record_json": ""}
    assert conductor_mission_open(other, closeout_text="stop: ROW_HOP\n") is None


def test_lane_retention_reason_unknown_row_is_none() -> None:
    assert lane_retention_reason(dispatch_id="nope", thread_id=_THREAD) is None


def _fake_lane_record(tmp_path: Path, branch_name: str):
    from types import SimpleNamespace

    wt = tmp_path / "wt" / branch_name.replace("/", "-")
    wt.mkdir(parents=True)
    return SimpleNamespace(
        worktree_path=wt,
        branch_name=branch_name,
        branch_point="abc123",
    )


def _run_failed_lane_settlement(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dispatch_id: str,
    thread_id: str,
    branch_name: str,
    closeout_text: str,
) -> list[dict]:
    """Drive the abandoned-mark branch of lane settlement without a git repo."""
    from services.git_integration_worker.relay.closeout_plane_probe import (
        PlaneObservation,
    )
    from services.git_integration_worker.cursor_sdk_capture_binding import (
        CaptureBinding,
    )
    from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
    from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
        SdkRunOutcome,
    )
    from services.git_integration_worker.cursor_sdk_closeout.delivery_assembly.lane_settlement import (
        settle_lane_and_dispatch_fields,
    )
    from services.git_integration_worker.cursor_sdk_lane_b_commit import (
        BranchState,
        SalvageResult,
    )

    record = _fake_lane_record(tmp_path, branch_name)
    # Settlement imports the name from cursor_sdk_worktree, which bound it at
    # import time. Patch that binding, not only the registry definition.
    lookups: list[str] = []

    def _lookup(**_kwargs):
        lookups.append("hit")
        return record

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree.lookup_dispatch_worktree",
        _lookup,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_b_commit.commit_on_terminal",
        lambda **_kwargs: SalvageResult(committed=False, head_sha=None),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_b_commit.branch_state",
        lambda *_args, **_kwargs: BranchState(
            head_sha=_TIP, commits_ahead=1, merged_into_master=False
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_deliverables_expected.resolve_lane_b_landed_head",
        lambda *_args, **_kwargs: (None, None, None),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.relay.closeout_plane_probe.probe_three_planes",
        lambda *_args, **_kwargs: PlaneObservation(
            head_sha=_TIP,
            branch=branch_name,
            commit_exists=True,
            landed_local_master=False,
            published_origin=False,
            unknown_reason=None,
            as_of="t",
        ),
    )
    marks: list[dict] = []

    def _mark(**kwargs):
        marks.append(kwargs)
        return None

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_b_disposition.mark_lane_b_disposition",
        _mark,
    )
    binding = CaptureBinding(
        lane="B",
        write_tree=record.worktree_path,
        receipt_tree=tmp_path / "repo",
        mount_root=record.worktree_path,
        repo_roots=(record.worktree_path,),
    )
    settle_lane_and_dispatch_fields(
        binding=binding,
        dispatch_id=dispatch_id,
        write_tree=record.worktree_path,
        receipt_tree=tmp_path / "repo",
        repo_change_set=ChangeSet(created=(), modified=(), deleted=()),
        outcome=SdkRunOutcome(
            body=closeout_text,
            status="failed",
            duration_ms=1,
            tool_call_count=0,
        ),
        deviations=[],
        divergence_reason=None,
        baseline=None,
        files_untracked_or_ignored=(),
        offgit_uris=(),
        thread_id=thread_id,
        gate_d_created_rels=(),
        closeout_text=closeout_text,
    )
    assert lookups, "lane settlement never resolved the worktree"
    return marks


def test_lane_settlement_skips_abandoned_while_conductor_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crashed or hopping conductor must not stamp abandoned on its own lane."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-crash", contract="conductor", work_key="todo:swap")
    _running(ledger, "hop-crash")
    marks = _run_failed_lane_settlement(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        dispatch_id="hop-crash",
        thread_id=_THREAD,
        branch_name=_BRANCH,
        closeout_text="",
    )
    assert marks == []
    assert get_disposition(branch_name=_BRANCH) is None


def test_lane_settlement_abandons_nested_limb_own_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed limb on its own branch is abandoned; the conductor lane is not."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-3", contract="conductor", work_key="todo:swap")
    limb_thread = "13718"
    limb_branch = f"cursor-sdk/lane-{limb_thread}"
    _admit(
        ledger,
        dispatch_id="limb-own",
        contract="pure-mechanical",
        work_key="packet:swap-r3",
        thread_id=limb_thread,
        lease_key="/repo-limb",
    )
    ledger.merge_record_json(dispatch_id="limb-own", patch={"nest_under": "hop-3"})
    _running(ledger, "hop-3")
    _running(ledger, "limb-own")
    marks = _run_failed_lane_settlement(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        dispatch_id="limb-own",
        thread_id=limb_thread,
        branch_name=limb_branch,
        closeout_text="",
    )
    assert len(marks) == 1
    assert marks[0]["branch_name"] == limb_branch
    assert marks[0]["reason"] == "abandoned"


def test_failed_terminal_skips_abandon_on_conductor_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_mark_terminal_and_promote`` abandons after ``mark_terminal``.

    The row is already ``failed``. The same predicate must still keep the
    conductor's lane (crash, no stop token) off the reap marker.
    """
    from services.git_integration_worker.routes.cursor_sdk import (
        _mark_lane_b_abandon_disposition,
    )

    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-crash", contract="conductor", work_key="todo:swap")
    _running(ledger, "hop-crash")
    ledger.mark_terminal(dispatch_id="hop-crash", terminal_status="failed")
    record = _fake_lane_record(tmp_path, _BRANCH)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_registry.lookup_dispatch_worktree",
        lambda **_kwargs: record,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_b_disposition.mark_lane_b_disposition_for_dispatch",
        lambda **kwargs: calls.append(kwargs["dispatch_id"]),
    )
    _mark_lane_b_abandon_disposition(
        dispatch_id="hop-crash", source_repo=tmp_path / "repo"
    )
    assert calls == []


def test_failed_terminal_abandons_nested_limb_own_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.routes.cursor_sdk import (
        _mark_lane_b_abandon_disposition,
    )

    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="hop-3", contract="conductor", work_key="todo:swap")
    limb_thread = "13718"
    limb_branch = f"cursor-sdk/lane-{limb_thread}"
    _admit(
        ledger,
        dispatch_id="limb-own",
        contract="pure-mechanical",
        work_key="packet:swap-r3",
        thread_id=limb_thread,
        lease_key="/repo-limb",
    )
    ledger.merge_record_json(dispatch_id="limb-own", patch={"nest_under": "hop-3"})
    _running(ledger, "hop-3")
    ledger.mark_terminal(dispatch_id="limb-own", terminal_status="failed")
    record = _fake_lane_record(tmp_path, limb_branch)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_registry.lookup_dispatch_worktree",
        lambda **_kwargs: record,
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_b_disposition.mark_lane_b_disposition_for_dispatch",
        lambda **kwargs: calls.append(kwargs),
    )
    _mark_lane_b_abandon_disposition(
        dispatch_id="limb-own", source_repo=tmp_path / "repo"
    )
    assert len(calls) == 1
    assert calls[0]["dispatch_id"] == "limb-own"
    assert calls[0]["reason"] == "abandoned"


def test_failed_terminal_abandon_proceeds_when_ledger_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ledger read that fails returns None; the abandoned mark still runs."""
    from services.git_integration_worker.routes.cursor_sdk import (
        _mark_lane_b_abandon_disposition,
    )

    def _boom(_dispatch_id: str) -> dict:
        raise RuntimeError("ledger down")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout."
        "conductor_lane_retention._load_row",
        _boom,
    )
    record = _fake_lane_record(tmp_path, _BRANCH)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_registry.lookup_dispatch_worktree",
        lambda **_kwargs: record,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_b_disposition.mark_lane_b_disposition_for_dispatch",
        lambda **kwargs: calls.append(kwargs["dispatch_id"]),
    )
    _mark_lane_b_abandon_disposition(
        dispatch_id="hop-crash", source_repo=tmp_path / "repo"
    )
    assert calls == ["hop-crash"]
