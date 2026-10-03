"""R6: conductor hop budget enforcement."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    maybe_fire_conductor_hop_reactor,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    _PARK_REASON_CRASH_CAP,
    _PARK_REASON_NO_PROGRESS_CAP,
    HOP_MISSION_CAP_RELEASE_BASELINE_KEY,
    HOP_PARK_REASON_KEY,
    HOP_PARKED_KEY,
    PARK_REASON_MISSION_CAP,
    HopBudgetConfig,
    evaluate_hop_budget,
    load_hop_budget_config,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park import (
    build_parked_transport_body,
)
from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
    HOP_PARK_RELEASED_AT_KEY,
    release_mission_parks,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:hop-budget-fixture"
_PROVABLE_CRASH_ROW = {
    "hop_entry_gate": "G4",
    "hop_witnessed_done": [],
    "hop_lane_tip": "aaaaaaa",
}


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _no_live_scoreboard_tip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the live tip read off the real cortex root; stamps still win."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress.read_scoreboard_tip",
        lambda *, slug: None,
        raising=False,
    )


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "9964",
        "model": "cursor/composer-2.5",
        "dispatch_id": "pred-budget-1",
        "execution_id": "exec-pred-budget-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_and_terminal(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    hop_seq: int,
    hop_from: str,
    hop_reason: str,
    closeout_tokens: list[str] | None = None,
    terminal_status: str = "completed",
    record_patch: dict | None = None,
    work_key: str = _WORK_KEY,
) -> dict:
    req = _req(dispatch_id=dispatch_id)
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
        work_key=work_key,
        source_ref=work_key,
        hop_seq=hop_seq,
        hop_from=hop_from,
        hop_reason=hop_reason,
    )
    patch_body = {"contract": "conductor", "lane": "B"}
    if closeout_tokens is not None:
        patch_body["closeout_stop_tokens"] = closeout_tokens
    if record_patch:
        patch_body.update(record_patch)
    ledger.merge_record_json(dispatch_id=dispatch_id, patch=patch_body)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status=terminal_status)
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def _tight_config(**overrides: object) -> HopBudgetConfig:
    base = {
        "crash_cap_per_row": 3,
        "no_progress_cap": 2,
        "mission_cap": 24,
        "crash_backoff_s": (30.0, 120.0, 300.0),
        "reactor_grace_s": 120.0,
    }
    base.update(overrides)
    return HopBudgetConfig(**base)


def test_load_hop_budget_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONDUCTOR_HOP_CRASH_CAP_PER_ROW", raising=False)
    monkeypatch.delenv("CONDUCTOR_HOP_NO_PROGRESS_CAP", raising=False)
    monkeypatch.delenv("CONDUCTOR_HOP_MISSION_CAP", raising=False)
    cfg = load_hop_budget_config()
    assert cfg.crash_cap_per_row == 3
    assert cfg.no_progress_cap == 2
    assert cfg.mission_cap == 24
    assert cfg.crash_backoff_s == (30.0, 120.0, 300.0)
    assert cfg.reactor_grace_s == 120.0


def test_load_hop_budget_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONDUCTOR_HOP_CRASH_CAP_PER_ROW", "5")
    monkeypatch.setenv("CONDUCTOR_HOP_NO_PROGRESS_CAP", "1")
    monkeypatch.setenv("CONDUCTOR_HOP_MISSION_CAP", "10")
    cfg = load_hop_budget_config()
    assert cfg.crash_cap_per_row == 5
    assert cfg.no_progress_cap == 1
    assert cfg.mission_cap == 10


def test_planned_row_hop_budget_ok() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _admit_and_terminal(
        ledger,
        dispatch_id="pred-budget-1",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="spawn",
        closeout_tokens=["ROW_HOP"],
        record_patch={
            "hop_entry_gate": "G4",
            "hop_witnessed_done": [],
        },
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(),
    )
    assert verdict.ok is True
    assert verdict.park is False
    assert verdict.backoff_s == 0.0


def test_mission_cap_parks() -> None:
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 4):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"hop-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"hop-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=["ROW_HOP"],
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="hop-4",
        hop_seq=4,
        hop_from="hop-3",
        hop_reason="planned",
        closeout_tokens=["ROW_HOP"],
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.park is True
    assert verdict.reason == PARK_REASON_MISSION_CAP


def test_crash_cap_parks() -> None:
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 3):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"crash-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"crash-{idx - 1}",
            hop_reason="crash",
            terminal_status="failed",
            record_patch=dict(_PROVABLE_CRASH_ROW),
        )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="crash-3",
        hop_seq=3,
        hop_from="crash-2",
        hop_reason="crash",
        terminal_status="failed",
        record_patch=dict(_PROVABLE_CRASH_ROW),
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.park is True
    assert verdict.reason == _PARK_REASON_CRASH_CAP


def test_crash_backoff_under_cap() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _admit_and_terminal(
        ledger,
        dispatch_id="crash-1",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="crash",
        terminal_status="failed",
        record_patch=dict(_PROVABLE_CRASH_ROW),
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.ok is True
    assert verdict.park is False
    assert verdict.backoff_s == 30.0


def test_no_progress_cap_parks() -> None:
    """AC2: same gate, no witness growth, and a lane tip that never moved."""
    ledger = CursorDispatchLedger.instance()
    witness: list[str] = []
    stuck = {
        "hop_entry_gate": "G4",
        "hop_witnessed_done": witness,
        "hop_lane_tip": "aaaaaaa",
    }
    for idx in range(1, 4):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"plan-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"plan-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=["ROW_HOP"],
            record_patch=dict(stuck),
        )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="plan-4",
        hop_seq=4,
        hop_from="plan-3",
        hop_reason="planned",
        closeout_tokens=["ROW_HOP"],
        record_patch=dict(stuck),
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(no_progress_cap=2),
    )
    assert verdict.park is True
    assert verdict.reason == _PARK_REASON_NO_PROGRESS_CAP


def test_build_parked_transport_body() -> None:
    body = build_parked_transport_body(
        reason=_PARK_REASON_CRASH_CAP,
        hop_seq=3,
    )
    assert "stop: PARKED_TRANSPORT" in body
    assert f"reason: {_PARK_REASON_CRASH_CAP}" in body
    assert "hop_seq: 3" in body


@pytest.mark.asyncio
async def test_maybe_fire_parks_on_budget_exhaustion() -> None:
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 4):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"park-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"park-{idx - 1}",
            hop_reason="crash",
            terminal_status="failed",
            record_patch=dict(_PROVABLE_CRASH_ROW),
        )
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget.load_hop_budget_config",
            return_value=_tight_config(crash_cap_per_row=3),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(),
        ) as post_mock,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.default_park_poster",
        ) as park_poster,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.page_hop_budget_parked",
            AsyncMock(return_value=True),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_hop_events.emit_frontier_sdk_conductor_hop_parked",
        ) as parked_event,
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="park-3")
    post_mock.assert_not_called()
    park_poster.assert_called_once()
    parked_event.assert_called_once()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='park-3'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data.get("hop_parked") is True
    assert data.get("hop_park_reason") == _PARK_REASON_CRASH_CAP


@pytest.mark.asyncio
async def test_maybe_fire_planned_hop_no_backoff() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_and_terminal(
        ledger,
        dispatch_id="pred-budget-1",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="spawn",
        closeout_tokens=["ROW_HOP"],
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
    )
    with (
        patch("asyncio.sleep", AsyncMock()) as sleep_mock,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(return_value=(True, {"dispatch_id": "succ-2"})),
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-budget-1")
    sleep_mock.assert_not_called()


def _planned_chain(ledger: CursorDispatchLedger, patches: list[dict]) -> dict:
    """Admit one planned ROW_HOP row per patch; return the last row."""
    row: dict = {}
    for idx, record_patch in enumerate(patches, start=1):
        row = _admit_and_terminal(
            ledger,
            dispatch_id=f"a32411-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"a32411-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=["ROW_HOP"],
            record_patch=record_patch,
        )
    return row


def test_a32411_shipping_hops_do_not_park() -> None:
    """AC1: three ROW_HOPs pinned at G1+[] whose lane tip advanced each hop."""
    ledger = CursorDispatchLedger.instance()
    row = _planned_chain(
        ledger,
        [
            {"hop_entry_gate": "G1", "hop_witnessed_done": [], "hop_lane_tip": tip}
            for tip in ("84f53520", "e96037df", "cd5cf10a")
        ],
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(no_progress_cap=2),
    )
    assert verdict.park is False
    assert verdict.ok is True


def test_a32411_unpaid_fold_alone_does_not_park() -> None:
    """AC1: a fold that never witnessed anything cannot prove a loop by itself."""
    ledger = CursorDispatchLedger.instance()
    row = _planned_chain(
        ledger,
        [{"hop_entry_gate": "G1", "hop_witnessed_done": []} for _ in range(3)],
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(no_progress_cap=2),
    )
    assert verdict.park is False
    assert verdict.reason is None


def test_next_admit_advance_breaks_no_progress_streak() -> None:
    """A conductor naming a new NEXT_ADMIT each hop is advancing."""
    ledger = CursorDispatchLedger.instance()
    row = _planned_chain(
        ledger,
        [
            {"hop_entry_gate": "G1", "hop_witnessed_done": [], "hop_next_admit": admit}
            for admit in ("harvest G2", "harvest G3", "harvest G4")
        ],
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(no_progress_cap=2),
    )
    assert verdict.park is False


def test_static_next_admit_still_parks() -> None:
    """AC2: an unmoving NEXT_ADMIT is a bound signal, so the loop still parks."""
    ledger = CursorDispatchLedger.instance()
    row = _planned_chain(
        ledger,
        [
            {
                "hop_entry_gate": "G1",
                "hop_witnessed_done": [],
                "hop_next_admit": "harvest G1",
            }
            for _ in range(3)
        ],
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(no_progress_cap=2),
    )
    assert verdict.park is True
    assert verdict.reason == _PARK_REASON_NO_PROGRESS_CAP


def test_witness_growth_breaks_no_progress_streak() -> None:
    """AC2 boundary: a fold that did move keeps the mission out of the park."""
    ledger = CursorDispatchLedger.instance()
    row = _planned_chain(
        ledger,
        [
            {
                "hop_entry_gate": "G4",
                "hop_witnessed_done": done,
                "hop_lane_tip": "aaaaaaa",
            }
            for done in ([], ["G1"], ["G1", "G2"])
        ],
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(no_progress_cap=2),
    )
    assert verdict.park is False


def test_budget_authority_patch_carries_bound_progress_signals() -> None:
    """AC3: the snapshot records every component the streak later compares."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        build_budget_authority_patch,
    )

    ledger = CursorDispatchLedger.instance()
    row = _admit_and_terminal(
        ledger,
        dispatch_id="authority-1",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="spawn",
        closeout_tokens=["ROW_HOP"],
        record_patch={
            "hop_entry_gate": "G4",
            "hop_witnessed_done": ["G1"],
            "hop_lane_tip": "cd5cf10a",
            "hop_next_admit": "harvest G5",
        },
    )
    patch_body = build_budget_authority_patch(row)
    assert patch_body["hop_entry_gate"] == "G4"
    assert patch_body["hop_witnessed_done"] == ["G1"]
    assert patch_body["hop_lane_tip"] == "cd5cf10a"
    assert patch_body["hop_next_admit"] == "harvest G5"


# --- AC-A1–A6 (crash identity P1′) ---


def test_ac_a1_designed_priors_done_current_no_crash_park() -> None:
    """AC-A1: ROW_HOP + PARKED_TRANSPORT priors, DONE current → no crash park."""
    ledger = CursorDispatchLedger.instance()
    chain = [
        (["ROW_HOP"], "completed"),
        (["PARKED_TRANSPORT"], "completed"),
        (["PARKED_TRANSPORT"], "completed"),
        (["DONE"], "completed"),
    ]
    row: dict = {}
    for idx, (tokens, status) in enumerate(chain, start=1):
        row = _admit_and_terminal(
            ledger,
            dispatch_id=f"ac-a1-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"ac-a1-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=tokens,
            terminal_status=status,
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"DONE"}),
        config=_tight_config(),
    )
    assert verdict.park is False
    assert verdict.reason is None


def test_ac_a2_consult_waits_break_crash_streak() -> None:
    """AC-A2: two CONSULT_PENDING waits then failed current → ok, backoff 30."""
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 3):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"ac-a2-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"ac-a2-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=["CONSULT_PENDING"],
            terminal_status="completed",
            record_patch=dict(_PROVABLE_CRASH_ROW),
        )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="ac-a2-3",
        hop_seq=3,
        hop_from="ac-a2-2",
        hop_reason="crash",
        closeout_tokens=[],
        terminal_status="failed",
        record_patch=dict(_PROVABLE_CRASH_ROW),
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.ok is True
    assert verdict.park is False
    assert verdict.backoff_s == 30.0


def test_ac_a3_completed_empty_tokens_crash_cap_parks() -> None:
    """AC-A3: three completed rows with empty tokens → third parks on crash cap."""
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 3):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"ac-a3-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"ac-a3-{idx - 1}",
            hop_reason="silent",
            closeout_tokens=[],
            terminal_status="completed",
            record_patch=dict(_PROVABLE_CRASH_ROW),
        )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="ac-a3-3",
        hop_seq=3,
        hop_from="ac-a3-2",
        hop_reason="silent",
        closeout_tokens=[],
        terminal_status="completed",
        record_patch=dict(_PROVABLE_CRASH_ROW),
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.park is True
    assert verdict.reason == _PARK_REASON_CRASH_CAP


@pytest.mark.parametrize(
    "stop_token",
    sorted(
        {
            "CONSULT_PENDING",
            "CONFIRM_PENDING",
            "ROW_PINNED",
            "HOLD_MERGE",
            "OPERATOR_GATE",
            "PARKED_TRANSPORT",
            "DONE",
        }
    ),
)
def test_ac_a4_designed_prior_breaks_crash_streak(stop_token: str) -> None:
    """AC-A4: a prior with any designed stop (except ROW_HOP) breaks crash streak."""
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 3):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"ac-a4-crash-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"ac-a4-crash-{idx - 1}",
            hop_reason="crash",
            closeout_tokens=[],
            terminal_status="failed",
            record_patch=dict(_PROVABLE_CRASH_ROW),
        )
    _admit_and_terminal(
        ledger,
        dispatch_id="ac-a4-designed",
        hop_seq=3,
        hop_from="ac-a4-crash-2",
        hop_reason="planned",
        closeout_tokens=[stop_token],
        terminal_status="completed",
        record_patch=dict(_PROVABLE_CRASH_ROW),
    )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="ac-a4-current",
        hop_seq=4,
        hop_from="ac-a4-designed",
        hop_reason="crash",
        closeout_tokens=[],
        terminal_status="failed",
        record_patch=dict(_PROVABLE_CRASH_ROW),
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.park is False
    assert verdict.ok is True
    assert verdict.backoff_s == 30.0


def test_ac_a5_third_parked_transport_no_crash_park() -> None:
    """AC-A5: third consecutive PARKED_TRANSPORT → park False (park_harvest path)."""
    ledger = CursorDispatchLedger.instance()
    row: dict = {}
    for idx in range(1, 4):
        row = _admit_and_terminal(
            ledger,
            dispatch_id=f"ac-a5-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"ac-a5-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=["PARKED_TRANSPORT"],
            terminal_status="completed",
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"PARKED_TRANSPORT"}),
        config=_tight_config(),
    )
    assert verdict.park is False


# --- H1: crash streak keyed per provable row, not per dispatch ---


def test_h1_unpaid_fold_crash_chain_does_not_park() -> None:
    """G1+[] crashes cannot attribute to a row — no crash cap park."""
    ledger = CursorDispatchLedger.instance()
    row: dict = {}
    for idx in range(1, 4):
        row = _admit_and_terminal(
            ledger,
            dispatch_id=f"h1-unpaid-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"h1-unpaid-{idx - 1}",
            hop_reason="crash",
            closeout_tokens=[],
            terminal_status="failed",
            record_patch={"hop_entry_gate": "G1", "hop_witnessed_done": []},
        )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.park is False
    assert verdict.backoff_s == 0.0


def test_h1_stamped_gate_alone_does_not_count_crash_streak() -> None:
    """A stamped G4 without witness/tip/admit is still an unpaid instrument."""
    ledger = CursorDispatchLedger.instance()
    row: dict = {}
    for idx in range(1, 4):
        row = _admit_and_terminal(
            ledger,
            dispatch_id=f"h1-gate-only-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"h1-gate-only-{idx - 1}",
            hop_reason="crash",
            closeout_tokens=[],
            terminal_status="failed",
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.park is False


def test_h1_lane_tip_move_breaks_crash_streak() -> None:
    """Shipping between crashes resets the row identity."""
    ledger = CursorDispatchLedger.instance()
    for idx, tip in enumerate(("aaaaaaa", "bbbbbbb"), start=1):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"h1-tip-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"h1-tip-{idx - 1}",
            hop_reason="crash",
            closeout_tokens=[],
            terminal_status="failed",
            record_patch={
                "hop_entry_gate": "G4",
                "hop_witnessed_done": [],
                "hop_lane_tip": tip,
            },
        )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="h1-tip-3",
        hop_seq=3,
        hop_from="h1-tip-2",
        hop_reason="crash",
        closeout_tokens=[],
        terminal_status="failed",
        record_patch={
            "hop_entry_gate": "G4",
            "hop_witnessed_done": [],
            "hop_lane_tip": "bbbbbbb",
        },
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.park is False
    assert verdict.backoff_s == 120.0


# --- a:32502 / worker 13713 (2026-10-01): progress the fold could not see ---


def test_a32502_13713_new_bind_each_hop_does_not_park() -> None:
    """Replay of 13713's three ledger stamps plus their scoreboard tip shas.

    The fold read the G-ladder for an R-row mission and stayed at G2/{G1} on
    every hop; the lane tip was unreadable after hop 1 because the branch had
    been archived at closeout; no NEXT_ADMIT was stamped. Each hop hung a new
    R-row bind, so the scoreboard tip moved every hop. That movement is
    progress and must not park the mission.
    """
    ledger = CursorDispatchLedger.instance()
    row = _planned_chain(
        ledger,
        [
            {
                "hop_entry_gate": "G2",
                "hop_witnessed_done": ["G1"],
                "hop_lane_tip": "a6ce58e82263778f704d2b5324efe33da1321ea5",
                "hop_scoreboard_tip": "56ea5c726f07541c6e6a104080607951b04a308b",
            },
            {
                "hop_entry_gate": "G2",
                "hop_witnessed_done": ["G1"],
                "hop_scoreboard_tip": "ba556e0a852421f18a801f998a5f35839f7a962a",
            },
            {
                "hop_entry_gate": "G2",
                "hop_witnessed_done": ["G1"],
                "hop_scoreboard_tip": "69ea17fb8a01eb5f4455a4a4f1878dd348ac214f",
            },
        ],
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(no_progress_cap=2),
    )
    assert verdict.park is False
    assert verdict.ok is True


def test_static_scoreboard_tip_with_frozen_fold_still_parks() -> None:
    """Guard: a tip that never moved is a bound signal, so a real loop still parks."""
    ledger = CursorDispatchLedger.instance()
    row = _planned_chain(
        ledger,
        [
            {
                "hop_entry_gate": "G2",
                "hop_witnessed_done": ["G1"],
                "hop_scoreboard_tip": "69ea17fb8a01eb5f4455a4a4f1878dd348ac214f",
            }
            for _ in range(3)
        ],
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(no_progress_cap=2),
    )
    assert verdict.park is True
    assert verdict.reason == _PARK_REASON_NO_PROGRESS_CAP


def test_budget_authority_patch_carries_scoreboard_tip() -> None:
    """The snapshot stamps the tip sha so a prior hop is reconstructed from it."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        build_budget_authority_patch,
    )

    ledger = CursorDispatchLedger.instance()
    row = _admit_and_terminal(
        ledger,
        dispatch_id="authority-tip-1",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="spawn",
        closeout_tokens=["ROW_HOP"],
        record_patch={
            "hop_entry_gate": "G2",
            "hop_witnessed_done": ["G1"],
            "hop_scoreboard_tip": "ba556e0a852421f18a801f998a5f35839f7a962a",
        },
    )
    patch_body = build_budget_authority_patch(row)
    assert (
        patch_body["hop_scoreboard_tip"] == "ba556e0a852421f18a801f998a5f35839f7a962a"
    )


# --- mission cap: only rows that owe a hop are budgeted ---


def _chain_at_cap(ledger: CursorDispatchLedger, *, last_tokens: list[str]) -> dict:
    for idx in range(1, 4):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"cap-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"cap-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=["ROW_HOP"],
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
    return _admit_and_terminal(
        ledger,
        dispatch_id="cap-4",
        hop_seq=4,
        hop_from="cap-3",
        hop_reason="planned",
        closeout_tokens=last_tokens,
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
    )


@pytest.mark.parametrize(
    "token",
    ["DONE", "ROW_PINNED", "OPERATOR_GATE", "HOLD_MERGE"],
)
def test_mission_cap_never_parks_a_designed_stop(token: str) -> None:
    """A designed stop owes no hop; the cap must not add a second lock to it.

    Sixteen ``hop_budget_mission_cap`` parks landed on DONE / ROW_PINNED /
    OPERATOR_GATE rows in the week to 2026-10-01, each paging and each needing
    a hand ``hop_park_release`` on top of the designed stop.
    """
    ledger = CursorDispatchLedger.instance()
    row = _chain_at_cap(ledger, last_tokens=[token])
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({token}),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.ok is True
    assert verdict.park is False
    assert verdict.reason is None


@pytest.mark.parametrize("token", ["CONSULT_PENDING", "PARKED_TRANSPORT"])
def test_mission_cap_still_parks_continue_owed_stops(token: str) -> None:
    """CONSULT_PENDING and PARKED_TRANSPORT admit successors, so the cap binds.

    ``consult_pending_continue_owed`` and ``park_harvest_continue_owed`` are
    the only budget check those chains get. An early exit on either token
    drops the mission cap.
    """
    ledger = CursorDispatchLedger.instance()
    row = _chain_at_cap(ledger, last_tokens=[token])
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({token}),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.park is True
    assert verdict.ok is False
    assert verdict.reason == PARK_REASON_MISSION_CAP


def test_mission_cap_parks_consult_pending_with_row_pinned() -> None:
    """{CONSULT_PENDING, ROW_PINNED} at the cap still parks.

    ``consult_pending_continue_owed`` does not reject ``ROW_PINNED``, and
    ``mission_open_for_row`` rejects only ``DONE``, so an exempt token in the
    same closeout must not skip the mission cap.
    """
    ledger = CursorDispatchLedger.instance()
    tokens = ["CONSULT_PENDING", "ROW_PINNED"]
    row = _chain_at_cap(ledger, last_tokens=tokens)
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(tokens),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.park is True
    assert verdict.ok is False
    assert verdict.reason == PARK_REASON_MISSION_CAP


def test_mission_cap_parks_parked_transport_with_operator_gate() -> None:
    """{PARKED_TRANSPORT, OPERATOR_GATE} at the cap still parks.

    ``park_harvest_continue_owed`` requires ``PARKED_TRANSPORT`` and does not
    reject ``OPERATOR_GATE``. That successor's only budget check is
    ``evaluate_hop_budget``.
    """
    ledger = CursorDispatchLedger.instance()
    tokens = ["PARKED_TRANSPORT", "OPERATOR_GATE"]
    row = _chain_at_cap(ledger, last_tokens=tokens)
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(tokens),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.park is True
    assert verdict.ok is False
    assert verdict.reason == PARK_REASON_MISSION_CAP


def test_mission_cap_row_pinned_alone_still_exits_early() -> None:
    """{ROW_PINNED} alone owes no successor and must not park at the cap."""
    ledger = CursorDispatchLedger.instance()
    row = _chain_at_cap(ledger, last_tokens=["ROW_PINNED"])
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_PINNED"}),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.ok is True
    assert verdict.park is False
    assert verdict.reason is None


def test_mission_cap_still_parks_a_planned_hop_at_cap() -> None:
    """Guard: the cap still binds the rows that do owe a hop."""
    ledger = CursorDispatchLedger.instance()
    row = _chain_at_cap(ledger, last_tokens=["ROW_HOP"])
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.park is True
    assert verdict.reason == PARK_REASON_MISSION_CAP


def test_mission_cap_skips_restart_park_rows() -> None:
    """A GIW restart park and its resume child are one hop, not two.

    Worker 13618 carried six restart-park parents among fifteen rows; counting
    them charged every restart against the mission cap twice.
    """
    ledger = CursorDispatchLedger.instance()
    row = _chain_at_cap(ledger, last_tokens=["ROW_HOP"])
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind='park_for_restart' "
            "WHERE dispatch_id IN ('cap-1', 'cap-2')"
        )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.park is False
    assert verdict.ok is True


def test_mission_cap_counts_cancel_discard_rows() -> None:
    """Only ``park_for_restart`` is the same hop as its resume child.

    ``cancel_discard`` ended the attempt. Skipping every ``park_kind`` would
    hide those rows from the mission cap.
    """
    ledger = CursorDispatchLedger.instance()
    row = _chain_at_cap(ledger, last_tokens=["ROW_HOP"])
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind='cancel_discard' "
            "WHERE dispatch_id IN ('cap-1', 'cap-2')"
        )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.park is True
    assert verdict.reason == PARK_REASON_MISSION_CAP


def test_cancel_discard_row_refuses_budget_without_park() -> None:
    """a:37149 — the discarded row itself must not mission-cap park (watchdog).

    Prior discards still count toward the cap for a later hop-owed row
    (``test_mission_cap_counts_cancel_discard_rows``). The kill row must not
    page/lock the mission the operator just discarded.
    """
    ledger = CursorDispatchLedger.instance()
    row = _chain_at_cap(ledger, last_tokens=[])
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind='cancel_discard' "
            "WHERE dispatch_id='cap-4'"
        )
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='cap-4'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.park is False
    assert verdict.ok is False
    assert verdict.reason == "cancel_discard"


def _planned_hop_row(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    hop_seq: int,
    hop_from: str,
) -> dict:
    return _admit_and_terminal(
        ledger,
        dispatch_id=dispatch_id,
        hop_seq=hop_seq,
        hop_from=hop_from,
        hop_reason="planned" if hop_seq > 1 else "spawn",
        closeout_tokens=["ROW_HOP"],
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
    )


def _release_mission_cap_park(ledger: CursorDispatchLedger, *, parked_id: str) -> None:
    ledger.merge_record_json(
        dispatch_id=parked_id,
        patch={
            HOP_PARKED_KEY: True,
            HOP_PARK_REASON_KEY: PARK_REASON_MISSION_CAP,
        },
    )
    with ledger._connect() as conn:
        release_mission_parks(
            conn,
            work_key=_WORK_KEY,
            thread_id="9964",
            caller_agent="liaison",
            post_commit_emits=[],
        )


def test_mission_cap_release_allows_hops_until_window_exhausted() -> None:
    """Released mission-cap park resets the window; re-parks only after cap hops since release."""
    ledger = CursorDispatchLedger.instance()
    cap = 3
    for idx in range(1, cap + 1):
        _planned_hop_row(
            ledger,
            dispatch_id=f"rel-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"rel-{idx - 1}",
        )
    row_at_cap = _planned_hop_row(
        ledger,
        dispatch_id="rel-at-cap",
        hop_seq=cap + 1,
        hop_from=f"rel-{cap}",
    )
    assert (
        evaluate_hop_budget(
            row_at_cap,
            closeout_tokens=frozenset({"ROW_HOP"}),
            config=_tight_config(mission_cap=cap),
        ).park
        is True
    )
    _release_mission_cap_park(ledger, parked_id="rel-at-cap")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='rel-at-cap'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data[HOP_MISSION_CAP_RELEASE_BASELINE_KEY] == cap + 1

    for extra in range(1, cap):
        row = _planned_hop_row(
            ledger,
            dispatch_id=f"rel-post-{extra}",
            hop_seq=cap + 1 + extra,
            hop_from="rel-at-cap" if extra == 1 else f"rel-post-{extra - 1}",
        )
        verdict = evaluate_hop_budget(
            row,
            closeout_tokens=frozenset({"ROW_HOP"}),
            config=_tight_config(mission_cap=cap),
        )
        assert verdict.park is False, f"extra hop {extra} should stay under window"

    row_repark = _planned_hop_row(
        ledger,
        dispatch_id="rel-repark",
        hop_seq=cap + 1 + cap,
        hop_from=f"rel-post-{cap - 1}",
    )
    verdict = evaluate_hop_budget(
        row_repark,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(mission_cap=cap),
    )
    assert verdict.park is True
    assert verdict.reason == PARK_REASON_MISSION_CAP


def test_mission_cap_second_release_restarts_window() -> None:
    """A second mission-cap release replaces the baseline for the next window."""
    ledger = CursorDispatchLedger.instance()
    cap = 3
    for idx in range(1, cap + 2):
        _planned_hop_row(
            ledger,
            dispatch_id=f"win-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"win-{idx - 1}",
        )
    _release_mission_cap_park(ledger, parked_id=f"win-{cap + 1}")
    for idx in range(cap + 2, cap + cap + 1):
        _planned_hop_row(
            ledger,
            dispatch_id=f"win-{idx}",
            hop_seq=idx,
            hop_from=f"win-{idx - 1}",
        )
    repark_id = f"win-{cap + cap + 1}"
    _planned_hop_row(
        ledger,
        dispatch_id=repark_id,
        hop_seq=cap + cap + 1,
        hop_from=f"win-{cap + cap}",
    )
    _release_mission_cap_park(ledger, parked_id=repark_id)
    row = _planned_hop_row(
        ledger,
        dispatch_id="win-after-second-release",
        hop_seq=cap + cap + 2,
        hop_from=repark_id,
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(mission_cap=cap),
    )
    assert verdict.park is False


def test_mission_cap_release_baseline_uses_park_work_key_not_caller() -> None:
    """Thread-scoped release must stamp baseline from the parked row's work_key."""
    ledger = CursorDispatchLedger.instance()
    cap = 3
    for idx in range(1, cap + 2):
        _planned_hop_row(
            ledger,
            dispatch_id=f"wk-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"wk-{idx - 1}",
        )
    parked_id = f"wk-{cap + 1}"
    ledger.merge_record_json(
        dispatch_id=parked_id,
        patch={
            HOP_PARKED_KEY: True,
            HOP_PARK_REASON_KEY: PARK_REASON_MISSION_CAP,
        },
    )
    with ledger._connect() as conn:
        release_mission_parks(
            conn,
            work_key=None,
            thread_id="9964",
            caller_agent="liaison",
            post_commit_emits=[],
        )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parked_id,),
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data[HOP_MISSION_CAP_RELEASE_BASELINE_KEY] == cap + 1


def test_mission_cap_release_baseline_uses_parked_mission_not_caller_work_key() -> None:
    """Release scoped to caller work_key W1 must still count hops on parked mission W2."""
    ledger = CursorDispatchLedger.instance()
    w1 = "todo:hop-budget-cross-w1"
    w2 = "todo:hop-budget-cross-w2"
    cap = 3
    for idx in range(1, cap + 2):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"x2-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"x2-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=["ROW_HOP"],
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
            work_key=w2,
        )
    _admit_and_terminal(
        ledger,
        dispatch_id="x1-only",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="spawn",
        closeout_tokens=["ROW_HOP"],
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        work_key=w1,
    )
    parked_id = f"x2-{cap + 1}"
    ledger.merge_record_json(
        dispatch_id=parked_id,
        patch={
            HOP_PARKED_KEY: True,
            HOP_PARK_REASON_KEY: PARK_REASON_MISSION_CAP,
        },
    )
    with ledger._connect() as conn:
        release_mission_parks(
            conn,
            work_key=w1,
            thread_id="9964",
            caller_agent="liaison",
            post_commit_emits=[],
        )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parked_id,),
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data[HOP_MISSION_CAP_RELEASE_BASELINE_KEY] == cap + 1
    assert data[HOP_MISSION_CAP_RELEASE_BASELINE_KEY] != 1


def test_crash_cap_release_does_not_stamp_mission_baseline() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_and_terminal(
        ledger,
        dispatch_id="crash-park",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="crash",
        terminal_status="failed",
        record_patch=dict(_PROVABLE_CRASH_ROW),
    )
    ledger.merge_record_json(
        dispatch_id="crash-park",
        patch={
            HOP_PARKED_KEY: True,
            HOP_PARK_REASON_KEY: _PARK_REASON_CRASH_CAP,
        },
    )
    with ledger._connect() as conn:
        release_mission_parks(
            conn,
            work_key=_WORK_KEY,
            thread_id="9964",
            caller_agent="liaison",
            post_commit_emits=[],
        )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='crash-park'"
        ).fetchone()
    data = json.loads(refreshed["record_json"])
    assert HOP_MISSION_CAP_RELEASE_BASELINE_KEY not in data
    assert HOP_PARK_RELEASED_AT_KEY in data
